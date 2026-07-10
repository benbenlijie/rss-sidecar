import httpx
from typing import Optional
from dataclasses import dataclass
import structlog

from .config import settings

logger = structlog.get_logger()

GREADER_BASE = "/api/greader.php"


class FreshRSSClient:
    def __init__(self):
        self.base_url = settings.freshrss_url.rstrip("/")
        self.username = settings.freshrss_username
        self.api_password = settings.freshrss_api_password
        self._auth_token: Optional[str] = None

    async def login(self) -> bool:
        url = f"{self.base_url}{GREADER_BASE}/accounts/ClientLogin"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, data={
                    "Email": self.username,
                    "Passwd": self.api_password,
                })
                if resp.status_code != 200:
                    logger.error("freshrss_login_failed", status=resp.status_code)
                    return False

                for line in resp.text.split("\n"):
                    if line.startswith("Auth="):
                        self._auth_token = line[len("Auth="):].strip()
                        logger.info("freshrss_login_ok", user=self.username)
                        return True

                logger.error("freshrss_no_auth_in_response")
                return False
        except Exception as e:
            logger.error("freshrss_login_exception", error=str(e))
            return False

    def _headers(self) -> dict:
        if not self._auth_token:
            raise RuntimeError("Not logged in. Call login() first.")
        return {"Authorization": f"GoogleLogin auth={self._auth_token}"}

    async def list_subscriptions(self) -> list[dict]:
        url = f"{self.base_url}{GREADER_BASE}/reader/api/0/subscription/list"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    url, headers=self._headers(),
                    params={"output": "json"},
                )
                if resp.status_code != 200:
                    logger.error("freshrss_list_error", status=resp.status_code)
                    return []

                data = resp.json()
                subs = data.get("subscriptions", [])
                logger.info("freshrss_subscriptions", count=len(subs))
                return subs
        except Exception as e:
            logger.error("freshrss_list_exception", error=str(e))
            return []

    async def add_subscription(self, feed_url: str, title: str = "", category: str = "Translated") -> bool:
        url = f"{self.base_url}{GREADER_BASE}/reader/api/0/subscription/edit"
        data = {
            "ac": "subscribe",
            "s": f"feed/{feed_url}",
            "t": title,
            "a": f"user/-/label/{category}",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, headers=self._headers(), data=data)
                if resp.status_code == 200:
                    logger.info("freshrss_sub_added", url=feed_url, title=title)
                    return True
                else:
                    logger.info("freshrss_sub_exists_or_error",
                                url=feed_url, status=resp.status_code)
                    return False
        except Exception as e:
            logger.error("freshrss_add_exception", error=str(e))
            return False

    async def ensure_logged_in(self) -> bool:
        if self._auth_token:
            return True
        return await self.login()
