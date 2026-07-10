import httpx
import trafilatura
from typing import Optional
from dataclasses import dataclass
import structlog

from .fetcher import validate_url

logger = structlog.get_logger()


@dataclass
class ExtractionResult:
    text: Optional[str]
    method: str


async def extract_full_content(url: str, rss_summary: str = "", rss_content: str = "") -> ExtractionResult:
    if not validate_url(url):
        logger.warning("extract_ssrf_blocked", url=url)
        return ExtractionResult(text=rss_summary or rss_content or None, method="rss_summary")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, max_redirects=3) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) RSS-Sidecar/0.1"})

            if resp.status_code != 200:
                logger.info("extract_http_error", url=url, status=resp.status_code)
                return _fallback(rss_content, rss_summary)

            html = resp.text

            extracted = trafilatura.extract(
                html,
                output_format="markdown",
                include_tables=True,
                include_links=True,
                include_images=False,
                favor_recall=True,
            )

            if extracted and len(extracted) > 200:
                paragraphs = [p.strip() for p in extracted.split("\n\n") if p.strip() and len(p.strip()) > 10]
                logger.info("extract_ok", url=url, chars=len(extracted), paragraphs=len(paragraphs))
                return ExtractionResult(text=extracted, method="trafilatura")

            logger.info("extract_too_short", url=url, chars=len(extracted or ""))
            return _fallback(rss_content, rss_summary)

    except Exception as e:
        logger.warning("extract_exception", url=url, error=str(e))
        return _fallback(rss_content, rss_summary)


def _fallback(rss_content: str, rss_summary: str) -> ExtractionResult:
    if rss_content and len(rss_content) > 100:
        return ExtractionResult(text=rss_content, method="rss_content")
    if rss_summary:
        return ExtractionResult(text=rss_summary, method="rss_summary")
    return ExtractionResult(text=None, method="failed")
