import hashlib
from typing import Optional
import structlog

from . import models

logger = structlog.get_logger()


def paragraph_hash(text: str) -> str:
    normalized = text.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def lookup(source_text: str) -> Optional[str]:
    if len(source_text.strip()) < 20:
        return None

    h = paragraph_hash(source_text)
    entry = await models.tm_lookup(h)
    if entry:
        await models.tm_increment_match(h)
        return entry["translated_text"]
    return None


async def store(source_text: str, translated_text: str, model: str):
    if len(source_text.strip()) < 20:
        return
    h = paragraph_hash(source_text)
    await models.tm_store(h, source_text, translated_text, model)


async def lookup_batch(paragraphs: list[str]) -> dict[int, str]:
    hits = {}
    for i, para in enumerate(paragraphs):
        result = await lookup(para)
        if result is not None:
            hits[i] = result
    return hits


async def store_batch(orig_paragraphs: list[str], trans_paragraphs: list[str], model: str):
    min_len = min(len(orig_paragraphs), len(trans_paragraphs))
    for i in range(min_len):
        await store(orig_paragraphs[i], trans_paragraphs[i], model)
