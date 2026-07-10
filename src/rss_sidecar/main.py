import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import structlog
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

from .config import settings
from . import models
from .fetcher import fetch_feed
from .extractor import extract_full_content
from .translator import translate
from .rss_output import generate_stable_feed, generate_bilingual_feed
from .freshrss_client import FreshRSSClient

structlog.configure(processors=[
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.add_log_level,
    structlog.processors.JSONRenderer(),
])

logger = structlog.get_logger()

app = FastAPI(title="RSS Sidecar", version="0.1.0")
_freshrss: Optional[FreshRSSClient] = None


@app.on_event("startup")
async def startup():
    await models.init_db()
    global _freshrss
    if settings.freshrss_enabled:
        _freshrss = FreshRSSClient()
        await _freshrss.login()
    logger.info("sidecar_started", freshrss=settings.freshrss_enabled)


@app.get("/health")
async def health():
    from datetime import date
    today = date.today().isoformat()
    daily_cost = await models.get_daily_cost(today)
    return {
        "status": "ok",
        "daily_cost_usd": round(daily_cost, 4),
        "daily_budget_usd": settings.daily_budget_usd,
        "budget_used_pct": round(daily_cost / settings.daily_budget_usd * 100, 1) if settings.daily_budget_usd else 0,
        "freshrss_connected": _freshrss is not None and _freshrss._auth_token is not None,
    }


@app.get("/feeds/manual")
async def add_manual_feed(url: str, title: str = ""):
    """Manually add an RSS feed URL for processing."""
    feed_title, items = await fetch_feed(url)
    if not feed_title:
        return {"error": "Failed to fetch feed", "url": url}

    feed_id = await _upsert_feed(url, feed_title or title or url)
    added = 0
    for item in items:
        aid = await models.create_article(feed_id, item.url, item.guid, item.title)
        if aid:
            added += 1

    logger.info("manual_feed_added", url=url, items=added)
    return {"feed_url": url, "feed_title": feed_title, "articles_queued": added}


@app.get("/feeds/discover")
async def discover_freshrss_feeds():
    """Auto-discover subscriptions from FreshRSS."""
    if not _freshrss:
        return {"error": "FreshRSS not configured"}

    await _freshrss.ensure_logged_in()
    subs = await _freshrss.list_subscriptions()

    discovered = []
    for sub in subs:
        url = sub.get("url", "")
        title = sub.get("title", url)
        if not url:
            continue

        feed_id = await _upsert_feed(url, title)
        discovered.append({"id": feed_id, "url": url, "title": title})

    logger.info("discovered_feeds", count=len(discovered))
    return {"discovered": len(discovered), "feeds": discovered}


@app.get("/feed/stable/{feed_id}.xml", response_class=Response)
async def stable_feed(feed_id: int):
    """Stable RSS feed (pure translation, unchanging guids)."""
    articles = await _get_published_articles(feed_id)
    if not articles:
        return Response(content="<rss></rss>", media_type="application/rss+xml")

    feed_url = str(settings.host)
    feed_title = articles[0].get("feed_title", "RSS Sidecar")
    rss_xml = generate_stable_feed(articles, feed_title, feed_url)
    return Response(content=rss_xml, media_type="application/rss+xml")


@app.get("/feed/bilingual/{feed_id}.xml", response_class=Response)
async def bilingual_feed_endpoint(feed_id: int):
    """Enhanced RSS feed (bilingual, versioned guids)."""
    articles = await _get_published_articles(feed_id)
    if not articles:
        return Response(content="<rss></rss>", media_type="application/rss+xml")

    feed_url = str(settings.host)
    feed_title = articles[0].get("feed_title", "RSS Sidecar")
    rss_xml = generate_bilingual_feed(articles, feed_title, feed_url)
    return Response(content=rss_xml, media_type="application/rss+xml")


@app.get("/article/{article_id}", response_class=HTMLResponse)
async def read_article(article_id: int):
    """Minimal web reading page with bilingual display."""
    art = await models.get_article(article_id)
    if not art:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)

    orig = art.get("content_orig") or ""
    trans = art.get("content_trans") or ""
    orig_paras = [p.strip() for p in orig.split("\n\n") if p.strip()]
    trans_paras = [p.strip() for p in trans.split("\n\n") if p.strip()]

    from jinja2 import Template
    template = Template(ARTICLE_TEMPLATE)
    return template.render(
        title=art.get("title_trans") or art.get("title_orig") or "Untitled",
        orig_title=art.get("title_orig") or "",
        blocks=zip(orig_paras, trans_paras),
        max_blocks=max(len(orig_paras), len(trans_paras)),
        orig_paras=orig_paras,
        trans_paras=trans_paras,
        source_url=art.get("original_url") or "",
    )


@app.post("/process")
async def process_pipeline(limit: int = 5):
    """Run one processing cycle: fetch -> extract -> translate -> publish."""
    results = {"extracted": 0, "translated": 0, "published": 0, "errors": 0}

    from datetime import date
    today = date.today().isoformat()
    daily_cost = await models.get_daily_cost(today)

    if daily_cost >= settings.daily_budget_usd:
        return {"error": "daily_budget_exceeded", "spent": daily_cost}

    to_extract = await models.get_articles_by_state("fetched", limit)
    for art in to_extract:
        success = await _do_extract(art)
        if success:
            results["extracted"] += 1
        else:
            results["errors"] += 1

    to_translate = await models.get_articles_by_state("extracted", limit)
    for art in to_translate:
        success = await _do_translate(art, today)
        if success:
            results["translated"] += 1
        else:
            results["errors"] += 1

    to_publish = await models.get_articles_by_state("translated", limit)
    for art in to_publish:
        success = await _do_publish(art)
        if success:
            results["published"] += 1
        else:
            results["errors"] += 1

    logger.info("pipeline_cycle", **results)
    return results


async def _upsert_feed(url: str, title: str) -> int:
    async with aiosqlite.connect(models.DB_PATH) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO feeds (url, title) VALUES (?, ?)", (url, title)
        )
        await db.commit()
        cursor = await db.execute("SELECT id FROM feeds WHERE url = ?", (url,))
        row = await cursor.fetchone()
        return row[0] if row else 0


async def _get_published_articles(feed_id: int) -> list[dict]:
    async with aiosqlite.connect(models.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT a.*, f.title as feed_title FROM articles a
               JOIN feeds f ON a.feed_id = f.id
               WHERE a.feed_id = ? AND a.state = 'published'
               ORDER BY a.published_at DESC LIMIT 50""",
            (feed_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def _do_extract(art: dict) -> bool:
    result = await extract_full_content(
        art["original_url"],
        rss_summary=art.get("content_orig") or "",
    )

    if result.text:
        await models.update_article_state(
            art["id"], "extracted",
            content_orig=result.text,
            extraction_method=result.method,
        )
        return True
    else:
        await models.update_article_state(art["id"], "extract_err")
        return False


async def _do_translate(art: dict, today: str) -> bool:
    text_to_translate = art.get("content_orig") or art.get("title_orig") or ""
    if not text_to_translate:
        await models.update_article_state(art["id"], "translate_err")
        return False

    result = await translate(text_to_translate)
    if not result:
        await models.update_article_state(
            art["id"], "translate_err",
            retry_count=art.get("retry_count", 0) + 1,
        )
        return False

    await models.record_cost(today, result.cost_usd, result.input_tokens, result.output_tokens, success=True)

    title_trans = result.text.split("\n\n")[0][:200] if result.text else art.get("title_orig")

    await models.update_article_state(
        art["id"], "translated",
        content_trans=result.text,
        title_trans=title_trans,
        content_version=1,
        trans_engine=result.engine,
        trans_model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        translated_at=time.time(),
    )
    return True


async def _do_publish(art: dict) -> bool:
    await models.update_article_state(
        art["id"], "published",
        published_at=time.time(),
    )

    if _freshrss:
        feed_url = f"http://{settings.host}:{settings.port}/feed/stable/{art['feed_id']}.xml"
        await _freshrss.add_subscription(
            feed_url,
            title=f"RSS Sidecar Feed {art['feed_id']}",
        )

    return True


ARTICLE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<style>
  body {
    font-family: -apple-system, "Segoe UI", Roboto, "Noto Sans SC", sans-serif;
    max-width: 720px;
    margin: 2rem auto;
    padding: 0 1rem;
    line-height: 1.8;
    color: #1a1a1a;
    background: #fafafa;
  }
  h1 { font-size: 1.5em; line-height: 1.3; margin-bottom: 0.5em; }
  .source { color: #888; font-size: 0.85em; margin-bottom: 2em; }
  .source a { color: #666; }
  .bilingual-block { margin-bottom: 1.5em; }
  .original {
    color: #999;
    font-size: 0.9em;
    opacity: 0.4;
    transition: opacity 0.2s;
    margin-bottom: 0.5em;
  }
  .original:hover { opacity: 1; }
  .translated { font-size: 1.05em; color: #1a1a1a; }
  .toggle {
    position: fixed; top: 1rem; right: 1rem;
    background: #333; color: #fff; border: none;
    padding: 0.5rem 1rem; border-radius: 4px; cursor: pointer;
    font-size: 0.85em;
  }
  body.hide-original .original { display: none; }
</style>
</head>
<body>
<button class="toggle" onclick="document.body.classList.toggle('hide-original')">原文</button>
<h1>{{ title }}</h1>
<div class="source">Source: <a href="{{ source_url }}">{{ source_url }}</a></div>
{% for i in range(max_blocks) %}
<div class="bilingual-block">
  {% if i < orig_paras|length %}<div class="original">{{ orig_paras[i] }}</div>{% endif %}
  {% if i < trans_paras|length %}<div class="translated">{{ trans_paras[i] }}</div>{% endif %}
</div>
{% endfor %}
</body>
</html>"""
