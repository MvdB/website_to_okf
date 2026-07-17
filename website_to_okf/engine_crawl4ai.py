"""crawl4ai-based fetch+extract engine.

Uses crawl4ai's AsyncWebCrawler (headless browser) with a PruningContentFilter to
produce boilerplate-stripped ``fit_markdown``. We keep our own discovery and OKF
writer; this engine just turns a URL into an :class:`Extracted`.
"""

from __future__ import annotations

import logging

from .config import Settings
from .engine import Engine
from .models import Extracted, UrlEntry
from .urls import is_probably_binary, normalize_url, same_site

log = logging.getLogger("website_to_okf.engine.crawl4ai")

# Metadata keys crawl4ai may expose for a publish date, in priority order.
_DATE_KEYS = ("article:published_time", "publishedTime", "date", "dcterms.date")


class Crawl4aiEngine(Engine):
    def __init__(self, settings: Settings):
        self.settings = settings
        self._crawler = None
        self._run_cfg = None

    async def setup(self) -> None:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        md_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=self.settings.prune_threshold, threshold_type="fixed"
            )
        )
        cfg_kwargs = dict(
            markdown_generator=md_generator,
            cache_mode=CacheMode.BYPASS,
            page_timeout=int(self.settings.timeout * 1000),
            verbose=False,
        )
        if self.settings.respect_robots:
            cfg_kwargs["check_robots_txt"] = True
        try:
            self._run_cfg = CrawlerRunConfig(**cfg_kwargs)
        except TypeError:
            # Older/newer signature: drop the optional robots flag.
            cfg_kwargs.pop("check_robots_txt", None)
            self._run_cfg = CrawlerRunConfig(**cfg_kwargs)

        self._crawler = AsyncWebCrawler(
            config=BrowserConfig(
                headless=True, user_agent=self.settings.user_agent, verbose=False
            )
        )
        await self._crawler.start()

    async def close(self) -> None:
        if self._crawler is not None:
            await self._crawler.close()

    def _collect_links(self, result, source_url: str) -> list[str]:
        out: dict[str, None] = {}
        internal = (getattr(result, "links", None) or {}).get("internal") or []
        for link in internal:
            href = link.get("href") if isinstance(link, dict) else None
            if not href:
                continue
            if not same_site(href, self.settings.site) or is_probably_binary(href):
                continue
            out.setdefault(normalize_url(href, strip_query=self.settings.strip_query), None)
        return list(out.keys())

    async def fetch_extract(self, entry: UrlEntry) -> Extracted | None:
        try:
            result = await self._crawler.arun(url=entry.url, config=self._run_cfg)
        except Exception as exc:  # noqa: BLE001 - a single URL must never kill the run
            log.warning("crawl4ai failed for %s: %s", entry.url, exc)
            return None
        if result is None or not getattr(result, "success", False):
            log.info("crawl4ai unsuccessful for %s", entry.url)
            return None

        md = getattr(result, "markdown", None)
        body = ""
        if md is not None:
            body = (getattr(md, "fit_markdown", "") or "").strip()
            if not body:
                body = (getattr(md, "raw_markdown", "") or "").strip()
        if not body and isinstance(md, str):
            body = md.strip()

        meta = getattr(result, "metadata", None) or {}
        title = meta.get("title")
        description = meta.get("description")
        date = next((meta[k] for k in _DATE_KEYS if meta.get(k)), None)

        return Extracted(
            url=entry.url,
            markdown=body,
            links=self._collect_links(result, entry.url),
            title=title,
            description=description,
            date=date,
            thin=len(body) < self.settings.thin_threshold,
        )
