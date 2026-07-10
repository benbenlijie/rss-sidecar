from openai import AsyncOpenAI
from typing import Optional
from dataclasses import dataclass
import time
import structlog

from .config import settings

logger = structlog.get_logger()

TRANSLATE_PROMPT = """Translate the following article to {target_lang}.

CRITICAL RULES:
1. Preserve the EXACT paragraph structure. Input has N paragraphs, output MUST have exactly N paragraphs.
2. Each input paragraph maps to exactly one output paragraph. Do NOT merge or split.
3. Separate paragraphs with a blank line (double newline).
4. Keep ALL Markdown formatting (bold, links, headings, lists, code).
5. Do NOT add preamble, explanation, or notes. Output ONLY the translation.
6. Keep proper nouns (company names, product names like GPT-4, Claude) in original language.

ARTICLE TO TRANSLATE:

{text}"""


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
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICING.get(model, {"input": 1.0, "output": 2.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


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

    prompt = TRANSLATE_PROMPT.format(target_lang=target_lang, text=text)

    for attempt in range(settings.max_retries + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )

            translated = response.choices[0].message.content.strip()

            usage = response.usage
            input_tok = usage.prompt_tokens if usage else 0
            output_tok = usage.completion_tokens if usage else 0
            cost = estimate_cost(model, input_tok, output_tok)

            input_paras = len([p for p in text.split("\n\n") if p.strip()])
            output_paras = len([p for p in translated.split("\n\n") if p.strip()])

            logger.info(
                "translate_ok",
                model=model,
                input_paras=input_paras,
                output_paras=output_paras,
                aligned=input_paras == output_paras,
                cost=cost,
            )

            return TranslationResult(
                text=translated,
                engine=settings.openai_base_url,
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cost_usd=cost,
            )

        except Exception as e:
            logger.warning("translate_retry", attempt=attempt, error=str(e))
            if attempt == settings.max_retries:
                logger.error("translate_failed", error=str(e))
                return None
            wait = 2 ** attempt * 3
            time.sleep(wait)

    return None
