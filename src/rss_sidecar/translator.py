from openai import AsyncOpenAI
from typing import Optional
from dataclasses import dataclass
from pathlib import Path
import asyncio
import json
import structlog

from .config import settings
from . import memory

logger = structlog.get_logger()

SYSTEM_PROMPT = (
    "You are a professional technical translator. "
    "Translate the user's text to {target_lang}. "
    "You are translating content from technology blogs, research papers, and news articles.\n\n"
    "Rules:\n"
    "- Output ONLY the translated text. No preamble, no explanations.\n"
    "- Preserve the EXACT paragraph structure: same paragraph count, one-to-one mapping.\n"
    "- Do NOT merge, split, add, or omit any paragraph.\n"
    "- Separate paragraphs with a blank line.\n"
    "- Preserve all Markdown formatting (bold, links, headings, lists, code blocks).\n"
    "- Keep company names, product names, and model names in original English "
    "(OpenAI, GPT-4, Claude, Anthropic, Google, Gemini, Meta, LLaMA).\n"
    "- For technical terms with no standard translation, keep the English original.\n"
    "- Translate naturally and fluently, not word-by-word.{glossary_section}"
)

MAX_PARAGRAPHS_PER_CHUNK = 15

_glossary_cache: Optional[dict] = None
_glossary_mtime: float = 0.0


def load_glossary() -> dict[str, str]:
    global _glossary_cache, _glossary_mtime

    glossary_path = Path("glossary.yaml")
    if not glossary_path.exists():
        return {}

    mtime = glossary_path.stat().st_mtime
    if _glossary_cache is not None and mtime == _glossary_mtime:
        return _glossary_cache

    try:
        import yaml
        data = yaml.safe_load(glossary_path.read_text())
        if isinstance(data, dict):
            _glossary_cache = {str(k).lower(): str(v) for k, v in data.items()}
            _glossary_mtime = mtime
            logger.info("glossary_loaded", terms=len(_glossary_cache))
            return _glossary_cache
    except Exception as e:
        logger.warning("glossary_load_failed", error=str(e))

    return {}


def build_glossary_section() -> str:
    glossary = load_glossary()
    if not glossary:
        return ""

    lines = ["\n\nGlossary (use these translations for the following terms):"]
    for term, translation in glossary.items():
        lines.append(f'  "{term}" → "{translation}"')
    return "\n".join(lines)


async def translate_title(title: str, target_lang: str = None) -> Optional[str]:
    if not title or not settings.openai_api_key:
        return None

    target_lang = target_lang or settings.target_language
    model = settings.openai_model

    client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": f"Translate the user's text to {target_lang}. Output ONLY the translation. Keep product/company names in English."},
                {"role": "user", "content": title},
            ],
            temperature=0.3,
        )
        result = response.choices[0].message.content.strip()
        return result if result else title
    except Exception as e:
        logger.warning("title_translate_failed", error=str(e))
        return None


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
    system_content = SYSTEM_PROMPT.format(
        target_lang=target_lang,
        glossary_section=build_glossary_section(),
    )

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_content},
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
            error_str = str(e)
            if "contentFilter" in error_str or "'code': '1301'" in error_str:
                logger.warning("content_filter_skipped", chunk=chunk_idx)
                return chunk, 0, 0
            logger.warning("translate_retry", chunk=chunk_idx, attempt=attempt, error=error_str)
            if attempt == settings.max_retries:
                logger.error("translate_chunk_failed", chunk=chunk_idx, error=error_str)
                return chunk, 0, 0
            await asyncio.sleep(2 ** attempt * 3)
    return chunk, 0, 0


async def _process_chunk(
    client: AsyncOpenAI, model: str, chunk: str, target_lang: str, chunk_idx: int
) -> tuple[str, int, int]:
    paragraphs = [p.strip() for p in chunk.split("\n\n") if p.strip()]

    tm_hits = await memory.lookup_batch(paragraphs)

    miss_indices = [i for i in range(len(paragraphs)) if i not in tm_hits]

    if not miss_indices:
        logger.info("tm_full_hit", chunk=chunk_idx, paras=len(paragraphs))
        result_paras = [tm_hits[i] for i in range(len(paragraphs))]
        return "\n\n".join(result_paras), 0, 0

    miss_text = "\n\n".join(paragraphs[i] for i in miss_indices)
    translated, in_tok, out_tok = await _translate_chunk_with_retry(
        client, model, miss_text, target_lang, chunk_idx
    )

    trans_paras = [p.strip() for p in translated.split("\n\n") if p.strip()]

    for j, idx in enumerate(miss_indices):
        if j < len(trans_paras):
            await memory.store(paragraphs[idx], trans_paras[j], model)

    result_paras = [""] * len(paragraphs)
    for i, trans in tm_hits.items():
        result_paras[i] = trans
    for j, idx in enumerate(miss_indices):
        if j < len(trans_paras):
            result_paras[idx] = trans_paras[j]
        else:
            result_paras[idx] = paragraphs[idx]

    if tm_hits:
        logger.info("tm_partial_hit", chunk=chunk_idx,
                     hit=len(tm_hits), miss=len(miss_indices))

    return "\n\n".join(result_paras), in_tok, out_tok


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

    tasks = [_process_chunk(client, model, chunk, target_lang, i)
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
