import pytest
import json
import networkx as nx
from rss_sidecar.graph_builder import build_graph, find_related_articles, save_graph, load_graph


def _make_extraction(article_id, title, entities):
    return {
        "id": article_id,
        "title_orig": title,
        "title_trans": title,
        "entities_json": json.dumps(entities),
    }


class TestGraphBuild:

    def test_merges_shared_entities(self):
        extractions = [
            _make_extraction(1, "Article 1", {
                "nodes": [{"id": "rlhf", "label": "RLHF"}, {"id": "claude", "label": "Claude"}],
                "edges": [{"source": "rlhf", "target": "claude"}],
            }),
            _make_extraction(2, "Article 2", {
                "nodes": [{"id": "rlhf", "label": "RLHF"}, {"id": "gpt4", "label": "GPT-4"}],
                "edges": [{"source": "rlhf", "target": "gpt4"}],
            }),
        ]
        G = build_graph(extractions)

        assert "rlhf" in G
        assert G.nodes["rlhf"]["articles"] == {1, 2}
        assert G.nodes["claude"]["articles"] == {1}
        assert G.nodes["gpt4"]["articles"] == {2}
        assert G.has_edge("rlhf", "claude")
        assert G.has_edge("rlhf", "gpt4")

    def test_no_shared_entities(self):
        extractions = [
            _make_extraction(1, "A", {"nodes": [{"id": "x", "label": "X"}], "edges": []}),
            _make_extraction(2, "B", {"nodes": [{"id": "y", "label": "Y"}], "edges": []}),
        ]
        G = build_graph(extractions)
        assert G.number_of_nodes() == 2
        assert G.number_of_edges() == 0

    def test_edge_weight_increments(self):
        extractions = [
            _make_extraction(1, "A", {
                "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                "edges": [{"source": "a", "target": "b", "relation": "r1"}],
            }),
            _make_extraction(2, "B", {
                "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                "edges": [{"source": "a", "target": "b", "relation": "r2"}],
            }),
        ]
        G = build_graph(extractions)
        assert G["a"]["b"]["weight"] == 2


class TestFindRelated:

    def _build_test_graph(self):
        extractions = [
            _make_extraction(1, "Claude", {
                "nodes": [{"id": "anthropic", "label": "Anthropic"},
                          {"id": "claude", "label": "Claude"},
                          {"id": "rlhf", "label": "RLHF"},
                          {"id": "ai_safety", "label": "AI Safety"}],
                "edges": [],
            }),
            _make_extraction(2, "GPT-4", {
                "nodes": [{"id": "openai", "label": "OpenAI"},
                          {"id": "gpt4", "label": "GPT-4"},
                          {"id": "rlhf", "label": "RLHF"},
                          {"id": "ai_safety", "label": "AI Safety"}],
                "edges": [],
            }),
            _make_extraction(3, "Gemini", {
                "nodes": [{"id": "google", "label": "Google"},
                          {"id": "gemini", "label": "Gemini"},
                          {"id": "ai_safety", "label": "AI Safety"}],
                "edges": [],
            }),
        ]
        return build_graph(extractions)

    def test_find_related_by_shared_entities(self):
        G = self._build_test_graph()
        related = find_related_articles(G, 1, limit=3)

        ids = [r["article_id"] for r in related]
        assert 2 in ids
        assert 3 in ids

        art2 = [r for r in related if r["article_id"] == 2][0]
        assert "RLHF" in art2["shared_concepts"]
        assert "AI Safety" in art2["shared_concepts"]
        assert art2["shared_count"] == 2

    def test_more_shared_ranks_higher(self):
        G = self._build_test_graph()
        related = find_related_articles(G, 1, limit=3)

        art2 = [r for r in related if r["article_id"] == 2][0]
        art3 = [r for r in related if r["article_id"] == 3][0]
        assert art2["shared_count"] >= art3["shared_count"]

    def test_no_related_returns_empty(self):
        extractions = [
            _make_extraction(1, "A", {"nodes": [{"id": "x", "label": "X"}], "edges": []}),
            _make_extraction(2, "B", {"nodes": [{"id": "y", "label": "Y"}], "edges": []}),
        ]
        G = build_graph(extractions)
        related = find_related_articles(G, 1)
        assert related == []

    def test_self_excluded(self):
        G = self._build_test_graph()
        related = find_related_articles(G, 1, limit=5)
        ids = [r["article_id"] for r in related]
        assert 1 not in ids


class TestGraphPersistence:

    def test_save_and_load(self, tmp_path, monkeypatch):
        from rss_sidecar import graph_builder
        monkeypatch.setattr(graph_builder, "GRAPH_PATH", tmp_path / "graph.json")

        extractions = [_make_extraction(1, "A", {
            "nodes": [{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}],
            "edges": [{"source": "x", "target": "y"}],
        })]
        G = build_graph(extractions)
        save_graph(G)

        loaded = load_graph()
        assert loaded is not None
        assert "x" in loaded
        assert loaded.has_edge("x", "y")

    def test_load_missing_returns_none(self, tmp_path, monkeypatch):
        from rss_sidecar import graph_builder
        monkeypatch.setattr(graph_builder, "GRAPH_PATH", tmp_path / "nonexistent.json")
        assert load_graph() is None


class TestSurprisingConnections:

    def _build_graph_with_rarity(self):
        extractions = [
            _make_extraction(1, "Claude", {
                "nodes": [
                    {"id": "ai_safety", "label": "AI Safety"},
                    {"id": "claude", "label": "Claude"},
                    {"id": "quantum", "label": "Quantum Computing"},
                ],
                "edges": [],
            }),
            _make_extraction(2, "GPT-4", {
                "nodes": [
                    {"id": "ai_safety", "label": "AI Safety"},
                    {"id": "gpt4", "label": "GPT-4"},
                ],
                "edges": [],
            }),
            _make_extraction(3, "Gemini", {
                "nodes": [
                    {"id": "ai_safety", "label": "AI Safety"},
                    {"id": "quantum", "label": "Quantum Computing"},
                ],
                "edges": [],
            }),
            _make_extraction(4, "Unrelated", {
                "nodes": [
                    {"id": "cooking", "label": "Cooking"},
                ],
                "edges": [],
            }),
        ]
        return build_graph(extractions)

    def test_finds_rare_shared_entity(self):
        from rss_sidecar.graph_builder import find_surprising_connections
        G = self._build_graph_with_rarity()
        surprising = find_surprising_connections(G, 1, limit=2)

        ids = [s["article_id"] for s in surprising]
        assert 3 in ids
        quantum_conn = [s for s in surprising if s["article_id"] == 3][0]
        assert "Quantum Computing" in quantum_conn["rare_concepts"]

    def test_common_entity_not_surprising(self):
        from rss_sidecar.graph_builder import find_surprising_connections
        G = self._build_graph_with_rarity()
        surprising = find_surprising_connections(G, 1, limit=5)

        for s in surprising:
            assert "AI Safety" not in s.get("rare_concepts", [])

    def test_no_related_returns_empty(self):
        from rss_sidecar.graph_builder import find_surprising_connections
        G = self._build_graph_with_rarity()
        result = find_surprising_connections(G, 4)
        assert result == []
