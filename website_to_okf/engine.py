"""Fetch+extract engines.

The pipeline owns discovery and the OKF writer; the *engine* is the swappable
part that turns a URL into an :class:`Extracted` (main-content markdown + links +
metadata). Two engines are available:

* ``trafilatura`` -- lightweight, static-first httpx with a Playwright fallback;
* ``crawl4ai``   -- browser-based crawl4ai with fit_markdown (see engine_crawl4ai).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from rich.console import Console

from .config import Settings
from .extract import extract
from .fetch import Fetcher
from .models import Extracted, RawPage, UrlEntry

log = logging.getLogger("website_to_okf.engine")
console = Console(legacy_windows=False)


class Engine(ABC):
    """Turns a discovered URL into extracted main content."""

    async def setup(self) -> None:  # noqa: B027 - optional hook
        pass

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass

    @abstractmethod
    async def fetch_extract(self, entry: UrlEntry) -> Extracted | None:
        ...


class TrafilaturaEngine(Engine):
    """Static-first httpx fetch + trafilatura extraction, with a browser fallback."""

    def __init__(self, settings: Settings, fetcher: Fetcher):
        self.settings = settings
        self.fetcher = fetcher
        self._render_disabled = False

    async def close(self) -> None:
        # The Fetcher is owned by the pipeline (also used for discovery).
        pass

    async def fetch_extract(self, entry: UrlEntry) -> Extracted | None:
        s = self.settings
        page: RawPage | None = None

        if s.render == "browser":
            page = await self._render(entry.url) or await self.fetcher.fetch_page(entry.url)
        else:
            page = await self.fetcher.fetch_page(entry.url)

        if page is None and s.render in ("auto", "browser"):
            page = await self._render(entry.url)
        if page is None:
            return None

        ext = extract(page, s)
        if ext is None:
            return None

        # Browser fallback for thin static content.
        if ext.thin and not page.rendered and s.render == "auto":
            rendered = await self._render(entry.url)
            if rendered is not None:
                rext = extract(rendered, s)
                if rext and len(rext.markdown) > len(ext.markdown):
                    ext = rext
        return ext

    async def _render(self, url: str):
        if self._render_disabled:
            return None
        try:
            return await self.fetcher.render_page(url)
        except RuntimeError as exc:
            log.warning("%s", exc)
            console.print("[yellow]Browser rendering unavailable; continuing static-only.[/]")
            self._render_disabled = True
            return None


def build_engine(settings: Settings, fetcher: Fetcher) -> Engine:
    if settings.engine == "crawl4ai":
        from .engine_crawl4ai import Crawl4aiEngine

        return Crawl4aiEngine(settings)
    return TrafilaturaEngine(settings, fetcher)
