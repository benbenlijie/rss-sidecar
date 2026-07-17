from feedgen.feed import FeedGenerator
from typing import Optional
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger()


def _make_feed(title: str, feed_url: str, base_guid: str = "url") -> FeedGenerator:
    fg = FeedGenerator()
    fg.id(feed_url)
    fg.title(title)
    fg.link(href=feed_url, rel="self")
    fg.subtitle(f"Translated by RSS Sidecar")
    fg.language("zh-CN")
    fg.updated(datetime.now(timezone.utc))
    return fg


def generate_stable_feed(articles: list[dict], feed_title: str, feed_url: str) -> str:
    fg = _make_feed(f"{feed_title} (译文)", feed_url)

    for art in articles:
        if not art.get("content_trans"):
            continue

        fe = fg.add_entry()
        fe.id(art["original_url"])
        fe.title(art.get("title_trans") or art.get("title_orig") or "Untitled")
        fe.link(href=art["original_url"])

        if art.get("published_at"):
            fe.published(datetime.fromtimestamp(art["published_at"], tz=timezone.utc))

        fe.content(art["content_trans"], type="CDATA")

    return fg.rss_str(pretty=True).decode()


def generate_bilingual_feed(articles: list[dict], feed_title: str, feed_url: str,
                            connections_map: dict = None) -> str:
    fg = _make_feed(f"{feed_title} (双语)", feed_url)

    for art in articles:
        if not art.get("content_trans"):
            continue

        fe = fg.add_entry()

        version = art.get("content_version", 1)
        fe.id(f'{art["original_url"]}#v{version}')
        fe.title(f'{art.get("title_trans") or art.get("title_orig", "Untitled")}')
        fe.link(href=art["original_url"])

        if art.get("published_at"):
            fe.published(datetime.fromtimestamp(art["published_at"], tz=timezone.utc))

        orig = art.get("content_orig") or ""
        trans = art.get("content_trans") or ""

        orig_paras = [p.strip() for p in orig.split("\n\n") if p.strip()]
        trans_paras = [p.strip() for p in trans.split("\n\n") if p.strip()]

        bilingual_html = _render_bilingual_html(orig_paras, trans_paras)

        if connections_map and art["id"] in connections_map:
            bilingual_html += _render_connections_html(connections_map[art["id"]])

        fe.content(bilingual_html, type="CDATA")

    return fg.rss_str(pretty=True).decode()


def _render_bilingual_html(orig_paras: list[str], trans_paras: list[str]) -> str:
    parts = []
    max_len = max(len(orig_paras), len(trans_paras))

    for i in range(max_len):
        orig = orig_paras[i] if i < len(orig_paras) else ""
        trans = trans_paras[i] if i < len(trans_paras) else ""

        parts.append(
            f'<div class="bilingual-block">'
            f'<div class="original">{orig}</div>'
            f'<div class="translated">{trans}</div>'
            f"</div>"
        )

    return "\n".join(parts)


def _render_connections_html(connections: list) -> str:
    if not connections:
        return ""

    items = []
    for c in connections:
        title = c.get("title", "")
        concepts = c.get("shared_concepts") or c.get("rare_concepts") or []
        concept_str = ", ".join(concepts[:4]) if concepts else ""
        items.append(
            f'<li>{title}'
            f'{"<br><small>" + concept_str + "</small>" if concept_str else ""}'
            f"</li>"
        )

    return (
        f'<div class="connections">'
        f"<h3>📎 相关文章</h3>"
        f'<ul>{"".join(items)}</ul>'
        f"</div>"
    )
