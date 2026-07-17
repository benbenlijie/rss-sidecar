import asyncio
import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import models
from . import graph_builder

mcp = FastMCP("rss-sidecar")


@mcp.tool()
async def search_articles(query: str, limit: int = 5) -> str:
    """Search translated articles by keyword in title or content.

    Args:
        query: Search keyword (matches original or translated text)
        limit: Max results (default 5)
    """
    await models.init_db()

    import aiosqlite
    results = []
    async with aiosqlite.connect(models.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, title_orig, title_trans, original_url, extraction_method
               FROM articles
               WHERE state = 'published'
                 AND (title_orig LIKE ? OR title_trans LIKE ? OR content_trans LIKE ?)
               ORDER BY published_at DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        )
        rows = await cursor.fetchall()

    for row in rows:
        results.append({
            "id": row["id"],
            "title": row["title_trans"] or row["title_orig"],
            "url": row["original_url"],
        })

    return json.dumps({"count": len(results), "articles": results}, ensure_ascii=False)


@mcp.tool()
async def get_article(article_id: int) -> str:
    """Get full article with bilingual content.

    Args:
        article_id: Article ID
    """
    await models.init_db()
    art = await models.get_article(article_id)

    if not art:
        return json.dumps({"error": "Article not found"})

    orig = art.get("content_orig") or ""
    trans = art.get("content_trans") or ""
    orig_paras = [p.strip() for p in orig.split("\n\n") if p.strip()][:5]
    trans_paras = [p.strip() for p in trans.split("\n\n") if p.strip()][:5]

    return json.dumps({
        "id": art["id"],
        "title_original": art.get("title_orig"),
        "title_translated": art.get("title_trans"),
        "url": art.get("original_url"),
        "model": art.get("trans_model"),
        "paragraphs_preview": list(zip(orig_paras, trans_paras)),
    }, ensure_ascii=False)


@mcp.tool()
async def get_related(article_id: int) -> str:
    """Find related articles via knowledge graph shared entities.

    Args:
        article_id: Article ID to find connections for
    """
    await models.init_db()
    G = graph_builder.load_graph()

    if not G:
        return json.dumps({"error": "Knowledge graph not built yet"})

    related = graph_builder.find_related_articles(G, article_id, limit=5)
    surprising = graph_builder.find_surprising_connections(G, article_id, limit=2)

    return json.dumps({
        "related": related,
        "surprising": surprising,
    }, ensure_ascii=False)


@mcp.tool()
async def get_stats() -> str:
    """Get overall statistics: article counts, cost, TM, graph."""
    await models.init_db()

    counts = await models.get_article_state_counts()
    tm = await models.tm_stats()

    G = graph_builder.load_graph()
    graph_info = {
        "nodes": G.number_of_nodes() if G else 0,
        "edges": G.number_of_edges() if G else 0,
    }

    return json.dumps({
        "articles_by_state": counts,
        "total_published": counts.get("published", 0),
        "tm_entries": tm["total_entries"],
        "tm_matches": tm["total_matches"],
        "graph": graph_info,
    }, ensure_ascii=False)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
