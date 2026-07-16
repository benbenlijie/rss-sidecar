import pytest
from rss_sidecar import memory, models as m


class TestTranslationMemory:

    async def test_store_and_lookup(self, db):
        await memory.store("This is a test paragraph for storage.", "这是用于存储的测试段落。", "test-model")
        result = await memory.lookup("This is a test paragraph for storage.")
        assert result == "这是用于存储的测试段落。"

    async def test_miss_returns_none(self, db):
        result = await memory.lookup("A completely different paragraph that was never stored before.")
        assert result is None

    async def test_increment_match_count(self, db):
        source = "Another test paragraph for match counting verification."
        await memory.store(source, "另一个测试段落。", "model")
        await memory.lookup(source)
        await memory.lookup(source)

        h = memory.paragraph_hash(source)
        entry = await m.tm_lookup(h)
        assert entry["match_count"] == 3

    async def test_short_text_skipped(self, db):
        await memory.store("short", "短", "model")
        result = await memory.lookup("short")
        assert result is None

    async def test_whitespace_normalized(self, db):
        await memory.store("  Hello world paragraph with padding.  ", "译文", "model")
        result = await memory.lookup("Hello world paragraph with padding.")
        assert result == "译文"

    async def test_batch_lookup_partial_hit(self, db):
        await memory.store("Stored paragraph number one here.", "已存储的第一段。", "model")
        paragraphs = [
            "Stored paragraph number one here.",
            "Unstored paragraph number two.",
            "Unstored paragraph number three.",
        ]
        hits = await memory.lookup_batch(paragraphs)
        assert 0 in hits
        assert 1 not in hits
        assert 2 not in hits
        assert hits[0] == "已存储的第一段。"

    async def test_tm_stats(self, db):
        await memory.store("First unique paragraph for stats.", "第一段。", "model")
        await memory.store("Second unique paragraph for stats.", "第二段。", "model")
        await memory.lookup("First unique paragraph for stats.")
        stats = await m.tm_stats()
        assert stats["total_entries"] == 2
        assert stats["total_matches"] >= 3
