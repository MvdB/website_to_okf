"""Fetching: async httpx with robots/rate-limit/retries, plus a Playwright fallback."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import Settings
from .models import RawPage

log = logging.getLogger("website_to_okf.fetch")

_RETRYABLE = (httpx.TransportError, httpx.RemoteProtocolError)


class Fetcher:
    """Shared async HTTP client with politeness and an optional browser fallback."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=settings.timeout,
            headers={"User-Agent": settings.user_agent},
            http2=True,
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()
        # Per-host rate limiting.
        self._host_next: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        # Lazy browser fallback.
        self._browser = None
        self._playwright = None
        self._browser_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    # --- politeness -------------------------------------------------------
    async def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            nxt = self._host_next.get(host, 0.0)
            if now < nxt:
                await asyncio.sleep(nxt - now)
            self._host_next[host] = max(now, nxt) + self.settings.request_delay

    async def allowed(self, url: str) -> bool:
        if not self.settings.respect_robots:
            return True
        host = urlparse(url).netloc
        async with self._robots_lock:
            if host not in self._robots:
                self._robots[host] = await self._load_robots(url)
        rp = self._robots[host]
        if rp is None:
            return True
        return rp.can_fetch(self.settings.user_agent, url)

    async def _load_robots(self, url: str) -> RobotFileParser | None:
        robots_url = urljoin(url, "/robots.txt")
        text = await self.get_text(robots_url)
        if text is None:
            return None
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        return rp

    # --- low-level requests ----------------------------------------------
    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    async def _request(self, url: str) -> httpx.Response:
        await self._throttle(url)
        return await self._client.get(url)

    async def get_bytes(self, url: str) -> bytes | None:
        try:
            resp = await self._request(url)
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("get_bytes failed %s: %s", url, exc)
            return None
        if resp.status_code >= 400:
            return None
        return resp.content

    async def get_text(self, url: str) -> str | None:
        data = await self.get_bytes(url)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")

    async def fetch_page(self, url: str) -> RawPage | None:
        """Fetch an HTML page (static). Returns None for non-HTML or errors."""
        if not await self.allowed(url):
            log.info("robots.txt disallows %s", url)
            return None
        try:
            resp = await self._request(url)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("fetch failed %s: %s", url, exc)
            return None
        ctype = resp.headers.get("content-type", "")
        if resp.status_code >= 400:
            log.info("HTTP %s for %s", resp.status_code, url)
            return None
        if "html" not in ctype.lower():
            log.debug("skipping non-html %s (%s)", url, ctype)
            return None
        return RawPage(
            url=url,
            final_url=str(resp.url),
            html=resp.text,
            status=resp.status_code,
            content_type=ctype,
            fetched_at=datetime.now(timezone.utc),
        )

    # --- browser fallback -------------------------------------------------
    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        async with self._browser_lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "Browser rendering requested but Playwright is not installed. "
                    "Install with: pip install 'website-to-okf[browser]' && playwright install chromium"
                ) from exc
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            return self._browser

    async def render_page(self, url: str) -> RawPage | None:
        """Render a page in headless Chromium and return its DOM HTML."""
        if not await self.allowed(url):
            return None
        browser = await self._ensure_browser()
        context = await browser.new_context(user_agent=self.settings.user_agent)
        try:
            page = await context.new_page()
            resp = await page.goto(url, wait_until="networkidle", timeout=self.settings.timeout * 1000)
            html = await page.content()
            status = resp.status if resp else 200
            final_url = page.url
        except Exception as exc:  # noqa: BLE001 - playwright raises broadly
            log.warning("render failed %s: %s", url, exc)
            return None
        finally:
            await context.close()
        return RawPage(
            url=url,
            final_url=final_url,
            html=html,
            status=status,
            content_type="text/html",
            fetched_at=datetime.now(timezone.utc),
            rendered=True,
        )
