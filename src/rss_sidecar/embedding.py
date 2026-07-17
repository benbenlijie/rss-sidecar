import json
import math
from typing import Optional
from openai import AsyncOpenAI
import structlog

from .config import settings
from . import models

logger = structlog.get_logger()


async def generate_embedding(text: str) -> Optional[list[float]]:
    if not settings.embedding_enabled:
        return None

    truncated = text[:8000]

    client = AsyncOpenAI(
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url or "https://api.openai.com/v1",
    )

    try:
        response = await client.embeddings.create(
            model=settings.embedding_model,
            input=truncated,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.warning("embedding_failed", error=str(e))
        return None


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0

    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))

    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


async def find_similar_articles(target_article_id: int, limit: int = 3) -> list[dict]:
    import aiosqlite

    async with aiosqlite.connect(models.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT embedding_json FROM articles WHERE id = ? AND embedding_json IS NOT NULL",
            (target_article_id,),
        )
        target_row = await cursor.fetchone()

        if not target_row:
            return []

        target_emb = json.loads(target_row["embedding_json"])

        cursor = await db.execute(
            """SELECT id, title_trans, title_orig, embedding_json
               FROM articles
               WHERE state = 'published' AND embedding_json IS NOT NULL AND id != ?""",
            (target_article_id,),
        )
        rows = await cursor.fetchall()

    scored = []
    for row in rows:
        emb = json.loads(row["embedding_json"])
        sim = cosine_similarity(target_emb, emb)
        if sim > 0.7:
            scored.append({
                "article_id": row["id"],
                "title": row["title_trans"] or row["title_orig"],
                "similarity": round(sim, 3),
            })

    scored.sort(key=lambda x: -x["similarity"])
    return scored[:limit]
