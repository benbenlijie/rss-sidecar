import json
import re
import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Optional

import networkx as nx
from openai import AsyncOpenAI
import structlog

from .config import settings
from . import models

logger = structlog.get_logger()

GRAPH_PATH = Path("data/knowledge_graph.json")

EXTRACTION_PROMPT = """Extract key entities and relationships from this article.
Return ONLY valid JSON. No markdown fences. No explanation.

{{"nodes": [{{"id": "anthropic", "label": "Anthropic", "type": "company"}}], "edges": [{{"source": "anthropic", "target": "claude", "relation": "develops"}}]}}

Rules:
- IDs: lowercase snake_case (openai, gpt_4, rlhf, constitutional_ai)
- Same concept MUST use same ID across articles
- Extract: companies, products, technologies, people, concepts
- Relations: develops, uses, references, competes_with, based_on, conceptually_related_to
- Max 12 entities per article

Title: {title}

Content (first 3000 chars):
{content}"""


async def extract_entities(article_id: int, title: str, content: str) -> Optional[dict]:
    if not settings.openai_api_key:
        return None

    truncated = content[:3000]
    prompt = EXTRACTION_PROMPT.format(title=title, content=truncated)

    client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.warning("entity_json_parse_failed", article_id=article_id, raw_len=len(raw))
                    return None
            else:
                logger.warning("entity_no_json_found", article_id=article_id)
                return None

        if not isinstance(data, dict) or "nodes" not in data:
            logger.warning("entity_extraction_bad_format", article_id=article_id)
            return None

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        for node in nodes:
            source_file = f"article_{article_id}"
            node["source_file"] = source_file
            node["article_id"] = article_id
            node["article_title"] = title

        for edge in edges:
            edge["source_file"] = f"article_{article_id}"

        result = {"nodes": nodes, "edges": edges}

        await models.update_article_state(
            article_id, "published",
            entities_json=json.dumps(result, ensure_ascii=False),
            graph_updated_at=__import__("time").time(),
        )

        logger.info("entities_extracted", article_id=article_id,
                     nodes=len(nodes), edges=len(edges))
        return result

    except Exception as e:
        logger.warning("entity_extraction_failed", article_id=article_id, error=str(e))
        return None


def build_graph(all_extractions: list[dict]) -> nx.Graph:
    G = nx.Graph()

    for extraction in all_extractions:
        try:
            data = extraction.get("entities_json")
            if isinstance(data, str):
                data = json.loads(data)
            elif not isinstance(data, dict):
                continue

            source_file = f"article_{extraction['id']}"

            for node in data.get("nodes", []):
                node_id = node.get("id", "")
                if not node_id:
                    continue

                if node_id not in G:
                    G.add_node(node_id,
                               label=node.get("label", node_id),
                               type=node.get("type", "concept"),
                               articles=set())

                article_id = extraction["id"]
                G.nodes[node_id]["articles"].add(article_id)
                if "article_titles" not in G.nodes[node_id]:
                    G.nodes[node_id]["article_titles"] = {}
                G.nodes[node_id]["article_titles"][article_id] = extraction.get("title_trans") or extraction.get("title_orig", "")

            for edge in data.get("edges", []):
                src = edge.get("source", "")
                tgt = edge.get("target", "")
                if src and tgt and src in G and tgt in G:
                    if G.has_edge(src, tgt):
                        G[src][tgt]["weight"] = G[src][tgt].get("weight", 1) + 1
                    else:
                        G.add_edge(src, tgt,
                                   relation=edge.get("relation", "relates_to"),
                                   weight=1)

        except Exception as e:
            logger.warning("graph_build_skip", article_id=extraction.get("id"), error=str(e))

    return G


def find_related_articles(G: nx.Graph, target_article_id: int, limit: int = 3) -> list[dict]:
    target_entities = set()
    for node_id, data in G.nodes(data=True):
        if target_article_id in data.get("articles", set()):
            target_entities.add(node_id)

    if not target_entities:
        return []

    article_shared = defaultdict(lambda: {"count": 0, "entities": []})

    for entity in target_entities:
        entity_articles = G.nodes[entity].get("articles", set())
        entity_label = G.nodes[entity].get("label", entity)
        for article_id in entity_articles:
            if article_id == target_article_id:
                continue
            article_shared[article_id]["count"] += 1
            article_shared[article_id]["entities"].append(entity_label)

    ranked = sorted(article_shared.items(), key=lambda x: -x[1]["count"])[:limit]

    results = []
    for article_id, info in ranked:
        unique_entities = list(dict.fromkeys(info["entities"]))[:5]
        results.append({
            "article_id": article_id,
            "shared_concepts": unique_entities,
            "shared_count": info["count"],
        })

    return results


def find_surprising_connections(G: nx.Graph, target_article_id: int, limit: int = 2) -> list[dict]:
    related = find_related_articles(G, target_article_id, limit=10)
    if not related:
        return []

    entity_rarity = {}
    for node_id, data in G.nodes(data=True):
        label = data.get("label", node_id)
        article_count = len(data.get("articles", set()))
        entity_rarity[label] = article_count

    surprising = []
    for r in related:
        rare = [e for e in r["shared_concepts"] if entity_rarity.get(e, 99) <= 2]
        if rare:
            surprise_score = len(rare) / max(len(r["shared_concepts"]), 1)
            surprising.append({
                **r,
                "rare_concepts": rare,
                "surprise_score": round(surprise_score, 2),
            })

    surprising.sort(key=lambda x: -x["surprise_score"])
    return surprising[:limit]


def save_graph(G: nx.Graph):
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    serializable = nx.node_link_data(G, edges="links")
    for node in serializable.get("nodes", []):
        if "articles" in node:
            node["articles"] = list(node["articles"])
    GRAPH_PATH.write_text(json.dumps(serializable, ensure_ascii=False, indent=2))


def load_graph() -> Optional[nx.Graph]:
    if not GRAPH_PATH.exists():
        return None
    try:
        data = json.loads(GRAPH_PATH.read_text())
        G = nx.node_link_graph(data, edges="links")
        for node_id in G.nodes():
            articles = G.nodes[node_id].get("articles", [])
            if isinstance(articles, list):
                G.nodes[node_id]["articles"] = set(articles)
        return G
    except Exception as e:
        logger.warning("graph_load_failed", error=str(e))
        return None


async def rebuild_graph():
    extractions = await models.get_articles_with_entities()
    if not extractions:
        logger.info("graph_rebuild_empty")
        return {"nodes": 0, "edges": 0}

    G = build_graph(extractions)
    save_graph(G)

    multi = sum(1 for _, d in G.nodes(data=True) if len(d.get("articles", set())) > 1)
    logger.info("graph_rebuilt",
                 nodes=G.number_of_nodes(),
                 edges=G.number_of_edges(),
                 multi_article=multi)
    return {"nodes": G.number_of_nodes(), "edges": G.number_of_edges(), "multi_article": multi}
