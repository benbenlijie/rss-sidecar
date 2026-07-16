import pytest
import time
from rss_sidecar import models as m


class TestStateMachine:

    async def test_fetched_to_extracted(self, db):
        feed_id = await _create_feed(db)
        aid = await m.create_article(feed_id, "https://example.com/1", "guid-1", "Title")
        assert aid is not None

        await m.update_article_state(aid, "extracted",
            content_orig="content", extraction_method="trafilatura")
        art = await m.get_article(aid)
        assert art["state"] == "extracted"
        assert art["content_orig"] == "content"
        assert art["extraction_method"] == "trafilatura"

    async def test_full_lifecycle(self, db):
        feed_id = await _create_feed(db)
        aid = await m.create_article(feed_id, "https://example.com/2", "guid-2", "Title")

        await m.update_article_state(aid, "extracted", content_orig="text")
        await m.update_article_state(aid, "translated",
            content_trans="译文", title_trans="标题", content_version=1)
        await m.update_article_state(aid, "published", published_at=time.time())

        art = await m.get_article(aid)
        assert art["state"] == "published"
        assert art["content_trans"] == "译文"
        assert art["content_version"] == 1

    async def test_crash_recovery(self, db):
        feed_id = await _create_feed(db)
        aid = await m.create_article(feed_id, "https://example.com/3", "guid-3", "Title")
        await m.update_article_state(aid, "translated",
            content_trans="已翻译", content_version=1)

        pending = await m.get_articles_by_state("translated")
        assert len(pending) == 1
        assert pending[0]["id"] == aid

        await m.update_article_state(aid, "published")
        art = await m.get_article(aid)
        assert art["state"] == "published"

    async def test_invalid_state_rejected(self, db):
        feed_id = await _create_feed(db)
        aid = await m.create_article(feed_id, "https://example.com/4", "guid-4", "Title")
        with pytest.raises(ValueError):
            await m.update_article_state(aid, "bogus_state")

    async def test_retry_count_increments(self, db):
        feed_id = await _create_feed(db)
        aid = await m.create_article(feed_id, "https://example.com/5", "guid-5", "Title")
        await m.update_article_state(aid, "extracted", content_orig="text")
        await m.update_article_state(aid, "translate_err", retry_count=1)
        await m.update_article_state(aid, "extracted", content_orig="text")
        await m.update_article_state(aid, "translate_err", retry_count=2)

        art = await m.get_article(aid)
        assert art["state"] == "translate_err"
        assert art["retry_count"] == 2

    async def test_duplicate_url_ignored(self, db):
        feed_id = await _create_feed(db)
        aid1 = await m.create_article(feed_id, "https://example.com/dup", "g1", "T1")
        aid2 = await m.create_article(feed_id, "https://example.com/dup", "g2", "T2")
        assert aid1 == aid2

    async def test_cost_tracking_accumulates(self, db):
        await m.record_cost("2026-01-01", 0.01, 100, 200, success=True)
        await m.record_cost("2026-01-01", 0.02, 150, 250, success=False)
        cost = await m.get_daily_cost("2026-01-01")
        assert abs(cost - 0.03) < 0.001


async def _create_feed(db) -> int:
    import aiosqlite
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO feeds (url, title) VALUES (?, ?)",
            ("https://test.com/feed.xml", "Test"),
        )
        await conn.commit()
        cursor = await conn.execute("SELECT id FROM feeds WHERE url = ?", ("https://test.com/feed.xml",))
        row = await cursor.fetchone()
        return row[0]
