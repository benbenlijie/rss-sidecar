import httpx
import ipaddress
import socket
import feedparser
from urllib.parse import urlparse
from typing import Optional
from dataclasses import dataclass
import structlog

logger = structlog.get_logger()

BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
]


def validate_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    try:
        ip = socket.gethostbyname(parsed.hostname)
        ip_obj = ipaddress.ip_address(ip)
        for net in BLOCKED_NETWORKS:
            if ip_obj in net:
                logger.warning("ssrf_blocked", url=url, ip=ip)
                return False
        return True
    except Exception:
        return False


@dataclass
class FeedItem:
    url: str
    title: str
    guid: str
    link: str
    summary: Optional[str]
    content_encoded: Optional[str]


async def fetch_feed(url: str) -> tuple[Optional[str], list[FeedItem]]:
    if not validate_url(url):
        logger.error("feed_url_invalid", url=url)
        return None, []

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "RSS-Sidecar/0.1"})
            if resp.status_code != 200:
                logger.error("feed_fetch_error", url=url, status=resp.status_code)
                return None, []

            feed_text = resp.text
            parsed = feedparser.parse(feed_text)

            feed_title = parsed.feed.get("title", url)

            items: list[FeedItem] = []
            for entry in parsed.entries[:20]:
                link = entry.get("link", "")
                guid = entry.get("id", link)
                title = entry.get("title", "")
                summary = entry.get("summary", None)

                content_encoded = None
                if hasattr(entry, "content") and entry.content:
                    content_encoded = entry.content[0].get("value")
                elif hasattr(entry, "content_encoded"):
                    content_encoded = entry.content_encoded

                if link:
                    items.append(FeedItem(
                        url=link, title=title, guid=guid,
                        link=link, summary=summary,
                        content_encoded=content_encoded,
                    ))

            logger.info("feed_fetched", url=url, title=feed_title, items=len(items))
            return feed_title, items

    except Exception as e:
        logger.error("feed_fetch_exception", url=url, error=str(e))
        return None, []
