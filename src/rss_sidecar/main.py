import asyncio
import fcntl
import time
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

from .config import settings
from . import models
from .fetcher import fetch_feed
from .extractor import extract_full_content
from .translator import translate, translate_title
from .rss_output import generate_stable_feed, generate_bilingual_feed
from .freshrss_client import FreshRSSClient
from . import graph_builder

structlog.configure(processors=[
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.add_log_level,
    structlog.processors.JSONRenderer(),
])

logger = structlog.get_logger()

app = FastAPI(title="RSS Sidecar", version="0.1.0")
_freshrss: Optional[FreshRSSClient] = None
_scheduler: Optional[AsyncIOScheduler] = None
_lock_file = None

PROCESS_INTERVAL_SECONDS = 300

@app.on_event("startup")
async def startup():
    global _freshrss, _scheduler, _lock_file

    await models.init_db()

    if settings.freshrss_enabled:
        _freshrss = FreshRSSClient()
        await _freshrss.login()

    try:
        _lock_file = open("/tmp/rss_sidecar_scheduler.lock", "w")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        logger.info("scheduler_locked_by_another_worker")
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        scheduled_fetch,
        IntervalTrigger(seconds=settings.fetch_interval_seconds),
        id="fetch",
        replace_existing=True,
    )
    _scheduler.add_job(
        scheduled_process,
        IntervalTrigger(seconds=PROCESS_INTERVAL_SECONDS),
        id="process",
        replace_existing=True,
    )
    _scheduler.add_job(
        scheduled_graph_rebuild,
        IntervalTrigger(seconds=3600),
        id="graph_rebuild",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "sidecar_started",
        freshrss=settings.freshrss_enabled,
        fetch_interval=settings.fetch_interval_seconds,
        process_interval=PROCESS_INTERVAL_SECONDS,
    )


@app.on_event("shutdown")
async def shutdown():
    global _scheduler, _lock_file
    if _scheduler:
        _scheduler.shutdown(wait=False)
    if _lock_file:
        fcntl.flock(_lock_file, fcntl.LOCK_UN)
        _lock_file.close()


@app.get("/health")
async def health():
    from datetime import date
    today = date.today().isoformat()
    daily_cost = await models.get_daily_cost(today)
    tm = await models.tm_stats()
    return {
        "status": "ok",
        "daily_cost_usd": round(daily_cost, 4),
        "daily_budget_usd": settings.daily_budget_usd,
        "budget_used_pct": round(daily_cost / settings.daily_budget_usd * 100, 1) if settings.daily_budget_usd else 0,
        "freshrss_connected": _freshrss is not None and _freshrss._auth_token is not None,
        "scheduler_running": _scheduler is not None and _scheduler.running,
        "tm_entries": tm["total_entries"],
        "tm_matches": tm["total_matches"],
    }


@app.get("/scheduler/status")
async def scheduler_status():
    if not _scheduler:
        return {"running": False, "reason": "locked_or_not_started"}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })

    feeds = await models.get_active_feeds()
    return {
        "running": _scheduler.running,
        "jobs": jobs,
        "active_feeds": len(feeds),
    }


@app.get("/graph/status")
async def graph_status():
    G = graph_builder.load_graph()
    if not G:
        return {"enabled": True, "nodes": 0, "edges": 0, "multi_article": 0}

    multi = sum(1 for _, d in G.nodes(data=True) if len(d.get("articles", set())) > 1)
    with_entities = await models.get_articles_with_entities()
    return {
        "enabled": True,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "multi_article_entities": multi,
        "articles_with_entities": len(with_entities),
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    from datetime import date
    today = date.today().isoformat()

    state_counts = await models.get_article_state_counts()
    daily_cost = await models.get_daily_cost(today)
    cost_history = await models.get_cost_history(7)
    tm = await models.tm_stats()

    G = graph_builder.load_graph()
    graph_nodes = G.number_of_nodes() if G else 0
    graph_edges = G.number_of_edges() if G else 0
    graph_multi = sum(1 for _, d in G.nodes(data=True) if len(d.get("articles", set())) > 1) if G else 0

    feeds = await models.get_active_feeds()

    from jinja2 import Template
    template = Template(DASHBOARD_TEMPLATE)
    return template.render(
        state_counts=state_counts,
        total_articles=sum(state_counts.values()),
        daily_cost=round(daily_cost, 4),
        daily_budget=settings.daily_budget_usd,
        cost_history=cost_history,
        tm_entries=tm["total_entries"],
        tm_matches=tm["total_matches"],
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        graph_multi=graph_multi,
        active_feeds=len(feeds),
        scheduler_running=_scheduler is not None and _scheduler.running,
        target_language=settings.target_language,
        model=settings.openai_model,
    )


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
    feed_url = f"http://{settings.host}:{settings.port}"
    feed_title = articles[0].get("feed_title", "RSS Sidecar")
    rss_xml = generate_stable_feed(articles, feed_title, feed_url)
    return Response(content=rss_xml, media_type="application/rss+xml")


@app.get("/feed/bilingual/{feed_id}.xml", response_class=Response)
async def bilingual_feed_endpoint(feed_id: int):
    """Enhanced RSS feed (bilingual, versioned guids)."""
    articles = await _get_published_articles(feed_id)
    if not articles:
        return Response(content="<rss></rss>", media_type="application/rss+xml")
    feed_url = f"http://{settings.host}:{settings.port}"
    feed_title = articles[0].get("feed_title", "RSS Sidecar")

    connections_map = {}
    G = graph_builder.load_graph()
    if G:
        for art in articles:
            related = graph_builder.find_related_articles(G, art["id"], limit=2)
            if related:
                connections_map[art["id"]] = [
                    {
                        "title": f"Article {r['article_id']}",
                        "shared_concepts": r["shared_concepts"],
                    }
                    for r in related
                ]

    rss_xml = generate_bilingual_feed(articles, feed_title, feed_url, connections_map=connections_map)
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

    connections = []
    surprises = []
    G = graph_builder.load_graph()
    if G:
        related = graph_builder.find_related_articles(G, article_id, limit=3)
        for r in related:
            related_art = await models.get_article(r["article_id"])
            if related_art:
                connections.append({
                    "id": r["article_id"],
                    "title": related_art.get("title_trans") or related_art.get("title_orig", ""),
                    "shared": ", ".join(r["shared_concepts"][:4]),
                })

        surprising = graph_builder.find_surprising_connections(G, article_id, limit=2)
        for s in surprising:
            s_art = await models.get_article(s["article_id"])
            if s_art:
                surprises.append({
                    "id": s["article_id"],
                    "title": s_art.get("title_trans") or s_art.get("title_orig", ""),
                    "concepts": ", ".join(s["rare_concepts"]),
                    "score": s["surprise_score"],
                })

    from jinja2 import Template
    template = Template(ARTICLE_TEMPLATE)
    return template.render(
        title=art.get("title_trans") or art.get("title_orig") or "Untitled",
        orig_title=art.get("title_orig") or "",
        max_blocks=max(len(orig_paras), len(trans_paras)),
        orig_paras=orig_paras,
        trans_paras=trans_paras,
        source_url=art.get("original_url") or "",
        connections=connections,
        surprises=surprises,
    )


@app.post("/process")
async def process_pipeline(limit: int = 5):
    """Run one processing cycle: fetch -> extract -> translate -> publish."""
    return await run_pipeline(limit)


async def scheduled_fetch():
    feeds = await models.get_active_feeds()
    if not feeds:
        return

    logger.info("scheduled_fetch_start", feeds=len(feeds))
    total_new = 0

    for feed in feeds:
        feed_title, items = await fetch_feed(feed["url"])
        if not items:
            continue

        for item in items:
            await models.create_article(feed["id"], item.url, item.guid, item.title)
            total_new += 1

        await models.update_feed_fetched(feed["id"])

    if total_new > 0:
        logger.info("scheduled_fetch_done", feeds=len(feeds), new_articles=total_new)


async def scheduled_process():
    await run_pipeline(limit=5)


async def scheduled_graph_rebuild():
    await graph_builder.rebuild_graph()


async def run_pipeline(limit: int = 5) -> dict:
    results = {"extracted": 0, "translated": 0, "published": 0, "errors": 0}

    from datetime import date
    today = date.today().isoformat()
    daily_cost = await models.get_daily_cost(today)

    if daily_cost >= settings.daily_budget_usd:
        logger.warning("budget_exceeded", spent=daily_cost, budget=settings.daily_budget_usd)
        return results

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
            content = art.get("content_orig") or ""
            if content and len(content) > 100:
                await graph_builder.extract_entities(
                    art["id"],
                    art.get("title_orig", ""),
                    content,
                )
        else:
            results["errors"] += 1

    if any(v > 0 for v in results.values()):
        logger.info("pipeline_cycle", **results)
    return results


async def _upsert_feed(url: str, title: str) -> int:
    async with aiosqlite.connect(models.DB_PATH) as db:
        await db.execute(
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

    title_orig = art.get("title_orig") or ""
    title_trans = await translate_title(title_orig) or title_orig

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
  .connections {
    margin-top: 3em; padding: 1.2em;
    background: #f0f0f0; border-radius: 8px;
  }
  .connections h3 { font-size: 1em; margin: 0 0 0.8em; color: #555; }
  .connections ul { list-style: none; padding: 0; margin: 0; }
  .connections li { margin-bottom: 0.6em; }
  .connections a { color: #0066cc; text-decoration: none; }
  .connections a:hover { text-decoration: underline; }
  .connections small { color: #999; display: block; margin-top: 2px; }
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
{% if connections %}
<div class="connections">
  <h3>📎 你读过的相关文章</h3>
  <ul>
    {% for c in connections %}
    <li>
      <a href="/article/{{ c.id }}">{{ c.title }}</a>
      <small>共同概念: {{ c.shared }}</small>
    </li>
    {% endfor %}
  </ul>
</div>
{% endif %}
{% if surprises %}
<div class="connections" style="background:#fff3cd;">
  <h3>💡 意外关联</h3>
  <ul>
    {% for s in surprises %}
    <li>
      <a href="/article/{{ s.id }}">{{ s.title }}</a>
      <small>稀有共同点: {{ s.concepts }}（惊喜度 {{ s.score }}）</small>
    </li>
    {% endfor %}
  </ul>
</div>
{% endif %}
</body>
</html>"""


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RSS Sidecar Dashboard</title>
<style>
  body { font-family: monospace; max-width: 900px; margin: 2rem auto; padding: 0 1rem; background: #1a1a2e; color: #e0e0e0; }
  h1 { color: #00d4ff; border-bottom: 1px solid #333; padding-bottom: 0.5em; }
  h2 { color: #00d4ff; margin-top: 2em; }
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1em; }
  .card { background: #16213e; border-radius: 8px; padding: 1em 1.2em; }
  .card .num { font-size: 2em; font-weight: bold; color: #00d4ff; }
  .card .label { color: #888; font-size: 0.85em; }
  .states { display: flex; gap: 0.5em; flex-wrap: wrap; }
  .state { padding: 0.3em 0.8em; border-radius: 4px; font-size: 0.85em; }
  .state-published { background: #0d3b0d; color: #4ade80; }
  .state-translated { background: #1a3b5c; color: #60a5fa; }
  .state-fetched { background: #3b3b0d; color: #fbbf24; }
  .state-err { background: #3b0d0d; color: #f87171; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.5em; }
  th, td { text-align: left; padding: 0.4em 0.8em; border-bottom: 1px solid #333; }
  th { color: #888; font-size: 0.85em; }
  a { color: #00d4ff; }
  .footer { margin-top: 2em; color: #555; font-size: 0.8em; }
</style>
</head>
<body>
<h1>RSS Sidecar Dashboard</h1>

<div class="grid">
  <div class="card">
    <div class="num">{{ total_articles }}</div>
    <div class="label">Total Articles</div>
  </div>
  <div class="card">
    <div class="num">{{ active_feeds }}</div>
    <div class="label">Active Feeds</div>
  </div>
  <div class="card">
    <div class="num">${{ daily_cost }}</div>
    <div class="label">Today's Cost / ${{ daily_budget }} budget</div>
  </div>
  <div class="card">
    <div class="num">{{ tm_entries }}</div>
    <div class="label">TM Entries ({{ tm_matches }} matches)</div>
  </div>
  <div class="card">
    <div class="num">{{ graph_nodes }}</div>
    <div class="label">Graph Nodes ({{ graph_edges }} edges, {{ graph_multi }} multi)</div>
  </div>
  <div class="card">
    <div class="num">{{ "RUNNING" if scheduler_running else "STOPPED" }}</div>
    <div class="label">Scheduler</div>
  </div>
</div>

<h2>Article States</h2>
<div class="states">
  {% for state, count in state_counts.items() %}
  <span class="state {% if 'err' in state %}state-err{% elif state == 'published' %}state-published{% elif state == 'translated' %}state-translated{% else %}state-fetched{% endif %}">
    {{ state }}: {{ count }}
  </span>
  {% endfor %}
</div>

<h2>Cost History (7 days)</h2>
<table>
  <tr><th>Date</th><th>Processed</th><th>Failed</th><th>Cost</th><th>Tokens (in/out)</th></tr>
  {% for c in cost_history %}
  <tr>
    <td>{{ c.date }}</td>
    <td>{{ c.articles_processed }}</td>
    <td>{{ c.articles_failed }}</td>
    <td>${{ "%.4f"|format(c.total_cost_usd) }}</td>
    <td>{{ c.total_input_tokens }} / {{ c.total_output_tokens }}</td>
  </tr>
  {% endfor %}
</table>

<div class="footer">
  Model: {{ model }} | Target: {{ target_language }} |
  <a href="/health">/health</a> |
  <a href="/scheduler/status">/scheduler/status</a> |
  <a href="/graph/status">/graph/status</a> |
  <a href="/docs">/docs</a>
</div>
</body>
</html>"""
