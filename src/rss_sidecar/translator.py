from openai import AsyncOpenAI
from typing import Optional
from dataclasses import dataclass
import asyncio
import time
import structlog

from .config import settings

logger = structlog.get_logger()

SYSTEM_PROMPT = (
    "You are a professional translator. "
    "Translate the user's text to {target_lang}. "
    "Output ONLY the translated text with the SAME paragraph structure. "
    "Do not merge, split, add, or omit any paragraph. "
    "Preserve all Markdown formatting. "
    "Keep proper nouns (GPT-4, Claude, OpenAI) untranslated."
)

MAX_PARAGRAPHS_PER_CHUNK = 15


@dataclass
class TranslationResult:
    text: str
    engine: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "glm-4-flash": {"input": 0.001, "output": 0.001},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if settings.translation_input_price > 0 or settings.translation_output_price > 0:
        rates = {
            "input": settings.translation_input_price,
            "output": settings.translation_output_price,
        }
    else:
        rates = PRICING.get(model, {"input": 1.0, "output": 2.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def _chunk_paragraphs(text: str, chunk_size: int = MAX_PARAGRAPHS_PER_CHUNK) -> list[str]:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= chunk_size:
        return [text]

    chunks = []
    for i in range(0, len(paragraphs), chunk_size):
        chunk = "\n\n".join(paragraphs[i:i + chunk_size])
        chunks.append(chunk)
    return chunks


async def _translate_chunk(client: AsyncOpenAI, model: str, chunk: str, target_lang: str) -> tuple[str, int, int]:
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(target_lang=target_lang)},
            {"role": "user", "content": chunk},
        ],
        temperature=0.3,
    )

    translated = response.choices[0].message.content.strip()
    usage = response.usage
    input_tok = usage.prompt_tokens if usage else 0
    output_tok = usage.completion_tokens if usage else 0

    return translated, input_tok, output_tok


async def _translate_chunk_with_retry(
    client: AsyncOpenAI, model: str, chunk: str, target_lang: str, chunk_idx: int
) -> tuple[str, int, int]:
    for attempt in range(settings.max_retries + 1):
        try:
            return await _translate_chunk(client, model, chunk, target_lang)
        except Exception as e:
            logger.warning("translate_retry", chunk=chunk_idx, attempt=attempt, error=str(e))
            if attempt == settings.max_retries:
                logger.error("translate_chunk_failed", chunk=chunk_idx, error=str(e))
                return chunk, 0, 0
            await asyncio.sleep(2 ** attempt * 3)
    return chunk, 0, 0


async def translate(text: str, target_lang: str = None) -> Optional[TranslationResult]:
    if not settings.openai_api_key:
        logger.error("no_api_key")
        return None

    target_lang = target_lang or settings.target_language
    model = settings.openai_model

    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )

    chunks = _chunk_paragraphs(text)
    logger.info("translate_start", model=model, chunks=len(chunks), chars=len(text))

    tasks = [_translate_chunk_with_retry(client, model, chunk, target_lang, i)
             for i, chunk in enumerate(chunks)]
    results = await asyncio.gather(*tasks)

    translated_chunks = [r[0] for r in results]
    total_input = sum(r[1] for r in results)
    total_output = sum(r[2] for r in results)

    translated_text = "\n\n".join(translated_chunks)
    cost = estimate_cost(model, total_input, total_output)

    input_paras = len([p for p in text.split("\n\n") if p.strip()])
    output_paras = len([p for p in translated_text.split("\n\n") if p.strip()])

    logger.info(
        "translate_done",
        model=model,
        input_paras=input_paras,
        output_paras=output_paras,
        aligned=input_paras == output_paras,
        cost=cost,
        chunks=len(chunks),
    )

    return TranslationResult(
        text=translated_text,
        engine=settings.openai_base_url,
        model=model,
        input_tokens=total_input,
        output_tokens=total_output,
        cost_usd=cost,
    )
