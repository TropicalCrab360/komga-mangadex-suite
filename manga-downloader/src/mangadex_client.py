import asyncio
import httpx
from aiolimiter import AsyncLimiter
from typing import Any, Dict, Optional
import logging

API_RPS_LIMIT = 4
API_RPM_LIMIT = 100

class MangaDexClient:
    def __init__(self, base_url: str, user_agent: str, username: Optional[str] = None, password: Optional[str] = None, token: Optional[str] = None, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0))
        self.api_rps = AsyncLimiter(API_RPS_LIMIT, 1)
        self.api_rpm = AsyncLimiter(API_RPM_LIMIT, 60)
        self._username = username
        self._password = password
        self._access_token = token
        self._refresh_token = None
        self._client_id = client_id
        self._client_secret = client_secret
        self.logger = logging.getLogger("downloader")

    async def _ensure_auth(self):
        # If token present, assume valid; otherwise login
        if self._access_token:
            self.headers["Authorization"] = f"Bearer {self._access_token}"
            return
        if self._username and self._password and self._client_id and self._client_secret:
            # Personal client OIDC password grant
            url = "https://auth.mangadex.org/realms/mangadex/protocol/openid-connect/token"
            form = {
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
            r = await self.client.post(url, data=form, headers={"User-Agent": self.headers["User-Agent"]})
            r.raise_for_status()
            data = r.json()
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            if self._access_token:
                self.headers["Authorization"] = f"Bearer {self._access_token}"
                self.logger.info("auth success: password grant")

    async def _refresh_auth(self):
        if not self._refresh_token:
            return False
        # OIDC refresh token grant
        url = "https://auth.mangadex.org/realms/mangadex/protocol/openid-connect/token"
        form = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        r = await self.client.post(url, data=form, headers={"User-Agent": self.headers["User-Agent"]})
        if r.status_code == 200:
            data = r.json()
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token") or self._refresh_token
            if self._access_token:
                self.headers["Authorization"] = f"Bearer {self._access_token}"
                self.logger.info("auth success: refresh grant")
                return True
        return False

    async def _limited_get(self, url: str, params: Optional[Dict[str, Any]] = None) -> httpx.Response:
        await self._ensure_auth()
        async with self.api_rpm:
            async with self.api_rps:
                r = await self.client.get(url, params=params, headers=self.headers)
        self.logger.debug("GET %s params=%s status=%s", url, params, r.status_code)
        if r.status_code == 429:
            await asyncio.sleep(float(r.headers.get("Retry-After", 2)))
            self.logger.debug("retry-after 429; retrying GET %s", url)
            return await self._limited_get(url, params)
        if 500 <= r.status_code < 600:
            await asyncio.sleep(1)
            self.logger.debug("server error %s; retrying GET %s", r.status_code, url)
            return await self._limited_get(url, params)
        if r.status_code == 401 and await self._refresh_auth():
            # Retry once after refresh
            async with self.api_rpm:
                async with self.api_rps:
                    r = await self.client.get(url, params=params, headers=self.headers)
            self.logger.debug("retried after refresh GET %s status=%s", url, r.status_code)
        r.raise_for_status()
        return r

    async def search_manga(self, title: str) -> Dict[str, Any]:
        url = f"{self.base_url}/manga"
        resp = await self._limited_get(url, params={"title": title, "limit": 10})
        return resp.json()

    async def get_manga(self, manga_id: str) -> Dict[str, Any]:
        """Fetch manga details including cover_art relationship attributes (fileName)."""
        url = f"{self.base_url}/manga/{manga_id}"
        # includes cover_art so we can get fileName for cover download
        resp = await self._limited_get(url, params={"includes[]": "cover_art"})
        return resp.json()

    async def get_manga_chapters(self, manga_id: str, language: str, *, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        url = f"{self.base_url}/chapter"
        params = {
            "manga": manga_id,
            "translatedLanguage[]": language,
            "order[chapter]": "asc",
            "limit": max(1, min(limit, 500)),
            "offset": max(0, offset),
        }
        resp = await self._limited_get(url, params=params)
        return resp.json()

    async def auth_logout(self) -> bool:
        # OIDC logout isn't required for personal clients; clear local tokens
        self._access_token = None
        self.headers.pop("Authorization", None)
        return True

    async def get_at_home(self, chapter_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/at-home/server/{chapter_id}"
        resp = await self._limited_get(url)
        return resp.json()

    async def close(self):
        await self.client.aclose()
