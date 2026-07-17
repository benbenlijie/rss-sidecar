import httpx
from typing import Optional
import structlog

from .config import settings

logger = structlog.get_logger()


class MinifluxClient:
    def __init__(self):
        self.base_url = settings.miniflux_url.rstrip("/")
        self.api_key = settings.miniflux_api_key
        self._category_cache: dict[str, int] = {}

    def _headers(self) -> dict:
        return {"X-Auth-Token": self.api_key}

    async def login(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/me",
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    logger.info("miniflux_auth_ok", user=resp.json().get("username", ""))
                    return True
                logger.error("miniflux_auth_failed", status=resp.status_code)
                return False
        except Exception as e:
            logger.error("miniflux_auth_exception", error=str(e))
            return False

    async def ensure_logged_in(self) -> bool:
        return bool(self.api_key)

    async def list_subscriptions(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/feeds",
                    headers=self._headers(),
                )
                if resp.status_code != 200:
                    logger.error("miniflux_list_error", status=resp.status_code)
                    return []

                feeds = resp.json()
                result = []
                for feed in feeds:
                    result.append({
                        "url": feed.get("feed_url", ""),
                        "title": feed.get("title", ""),
                        "id": str(feed.get("id", "")),
                    })

                logger.info("miniflux_subscriptions", count=len(result))
                return result
        except Exception as e:
            logger.error("miniflux_list_exception", error=str(e))
            return []

    async def _get_or_create_category(self, category_name: str) -> Optional[int]:
        if category_name in self._category_cache:
            return self._category_cache[category_name]

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/categories",
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    for cat in resp.json():
                        if cat.get("title") == category_name:
                            cat_id = cat["id"]
                            self._category_cache[category_name] = cat_id
                            return cat_id

                resp = await client.post(
                    f"{self.base_url}/v1/categories",
                    headers=self._headers(),
                    json={"title": category_name},
                )
                if resp.status_code in (200, 201):
                    cat_id = resp.json()["id"]
                    self._category_cache[category_name] = cat_id
                    return cat_id
        except Exception as e:
            logger.error("miniflux_category_error", error=str(e))

        return None

    async def add_subscription(self, feed_url: str, title: str = "",
                               category: str = "Translated") -> bool:
        cat_id = await self._get_or_create_category(category)

        payload = {"feed_url": feed_url}
        if cat_id:
            payload["category_id"] = cat_id

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/feeds",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code in (200, 201):
                    logger.info("miniflux_sub_added", url=feed_url)
                    return True
                logger.info("miniflux_sub_exists_or_error",
                            url=feed_url, status=resp.status_code)
                return False
        except Exception as e:
            logger.error("miniflux_add_exception", error=str(e))
            return False
