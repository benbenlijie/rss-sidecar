import aiosqlite
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

DB_PATH = "data/rss_sidecar.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT,
    last_fetched REAL DEFAULT 0,
    fetch_interval INTEGER DEFAULT 1800,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER REFERENCES feeds(id),
    original_url TEXT UNIQUE NOT NULL,
    original_guid TEXT,
    title_orig TEXT,
    content_orig TEXT,
    extraction_method TEXT,
    title_trans TEXT,
    content_trans TEXT,
    content_version INTEGER DEFAULT 0,
    state TEXT DEFAULT 'fetched',
    retry_count INTEGER DEFAULT 0,

    trans_engine TEXT,
    trans_model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,

    entities_json TEXT,
    graph_updated_at REAL,

    fetched_at REAL,
    translated_at REAL,
    published_at REAL,

    UNIQUE(original_url)
);

CREATE INDEX IF NOT EXISTS idx_articles_state ON articles(state);
CREATE INDEX IF NOT EXISTS idx_articles_feed ON articles(feed_id);

CREATE TABLE IF NOT EXISTS daily_costs (
    date TEXT PRIMARY KEY,
    articles_processed INTEGER DEFAULT 0,
    articles_failed INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS translation_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_hash TEXT UNIQUE NOT NULL,
    source_text TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    trans_model TEXT,
    match_count INTEGER DEFAULT 1,
    created_at REAL,
    last_used_at REAL
);

CREATE INDEX IF NOT EXISTS idx_tm_hash ON translation_memory(source_hash);
"""

VALID_STATES = {
    "fetched", "extracted", "translated", "published",
    "fetch_err", "extract_err", "translate_err",
}


async def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


@dataclass
class Article:
    id: Optional[int]
    feed_id: int
    original_url: str
    original_guid: Optional[str]
    title_orig: Optional[str]
    content_orig: Optional[str]
    extraction_method: Optional[str]
    title_trans: Optional[str]
    content_trans: Optional[str]
    content_version: int
    state: str
    retry_count: int
    trans_engine: Optional[str]
    trans_model: Optional[str]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    fetched_at: Optional[float]
    translated_at: Optional[float]
    published_at: Optional[float]


async def create_article(feed_id: int, url: str, guid: str = "", title: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT OR IGNORE INTO articles
               (feed_id, original_url, original_guid, title_orig, state, fetched_at)
               VALUES (?, ?, ?, ?, 'fetched', ?)""",
            (feed_id, url, guid, title, time.time()),
        )
        await db.commit()
        return cursor.lastrowid


async def update_article_state(article_id: int, state: str, **fields):
    if state not in VALID_STATES:
        raise ValueError(f"Invalid state: {state}")

    sets = ["state = ?"]
    params = [state]

    for key, value in fields.items():
        if key in {
            "content_orig", "extraction_method", "title_trans", "content_trans",
            "content_version", "trans_engine", "trans_model",
            "input_tokens", "output_tokens", "cost_usd",
            "translated_at", "published_at", "retry_count",
            "entities_json", "graph_updated_at",
        }:
            sets.append(f"{key} = ?")
            params.append(value)

    params.append(article_id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE articles SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        await db.commit()


async def get_articles_by_state(state: str, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT * FROM articles WHERE state = ? ORDER BY fetched_at LIMIT ?", (state, limit)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_article(article_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def record_cost(date_str: str, cost_usd: float, input_tok: int, output_tok: int, success: bool):
    processed_inc = 1 if success else 0
    failed_inc = 0 if success else 1
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO daily_costs (date, articles_processed, articles_failed, total_cost_usd, total_input_tokens, total_output_tokens)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   articles_processed = articles_processed + ?,
                   articles_failed = articles_failed + ?,
                   total_cost_usd = total_cost_usd + ?,
                   total_input_tokens = total_input_tokens + ?,
                   total_output_tokens = total_output_tokens + ?""",
            (date_str, processed_inc, failed_inc, cost_usd, input_tok, output_tok,
             processed_inc, failed_inc, cost_usd, input_tok, output_tok),
        )
        await db.commit()


async def get_daily_cost(date_str: str) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT total_cost_usd FROM daily_costs WHERE date = ?", (date_str,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0


async def get_active_feeds() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM feeds WHERE active = 1")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def update_feed_fetched(feed_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE feeds SET last_fetched = ? WHERE id = ?",
            (time.time(), feed_id),
        )
        await db.commit()


async def tm_lookup(source_hash: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM translation_memory WHERE source_hash = ?",
            (source_hash,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def tm_store(source_hash: str, source_text: str, translated_text: str, model: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO translation_memory
               (source_hash, source_text, translated_text, trans_model, created_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_hash, source_text[:500], translated_text, model, time.time(), time.time()),
        )
        await db.commit()


async def tm_increment_match(source_hash: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE translation_memory SET match_count = match_count + 1, last_used_at = ? WHERE source_hash = ?",
            (time.time(), source_hash),
        )
        await db.commit()


async def tm_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) as total, COALESCE(SUM(match_count), 0) as matches FROM translation_memory")
        row = await cursor.fetchone()
        return {"total_entries": row[0], "total_matches": row[1]}


async def get_articles_with_entities() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, title_orig, title_trans, original_url, entities_json FROM articles WHERE state = 'published' AND entities_json IS NOT NULL"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_published_articles_for_graph() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, title_orig, title_trans, original_url, content_orig FROM articles WHERE state = 'published'"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
