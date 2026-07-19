"""Discover the set of URLs to scrape.

Strategy: sitemap-first. We look for sitemaps via ``robots.txt`` ``Sitemap:``
hints and the conventional ``/sitemap.xml``, following sitemap *index* files and
nested sitemaps (including gzip-compressed ones). Crawl expansion (following
links found on pages) is handled by the pipeline as a fallback when the sitemap
yields nothing.
"""

from __future__ import annotations

import gzip
import logging
from datetime import datetime
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from .config import Settings
from .fetch import Fetcher
from .models import UrlEntry
from .urls import (
    is_probably_binary,
    normalize_url,
    passes_filters,
    same_site,
)

log = logging.getLogger("website_to_okf.discover")

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _parse_lastmod(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # Try fromisoformat as a last resort (handles many ISO variants).
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _decode(content: bytes, url: str) -> str:
    if url.endswith(".gz") or content[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(content)
        except OSError:
            pass
    return content.decode("utf-8", errors="replace")


async def _sitemap_urls_from_robots(fetcher: Fetcher, base: str) -> list[str]:
    robots_url = urljoin(base, "/robots.txt")
    resp = await fetcher.get_text(robots_url)
    if resp is None:
        return []
    sitemaps = []
    for line in resp.splitlines():
        low = line.strip().lower()
        if low.startswith("sitemap:"):
            sitemaps.append(line.split(":", 1)[1].strip())
    return sitemaps


async def _walk_sitemap(
    fetcher: Fetcher, sitemap_url: str, seen: set[str], depth: int = 0
) -> list[UrlEntry]:
    """Recursively parse a sitemap or sitemap-index into URL entries."""
    if depth > 10 or sitemap_url in seen:
        return []
    seen.add(sitemap_url)

    content = await fetcher.get_bytes(sitemap_url)
    if content is None:
        return []
    try:
        root = ET.fromstring(_decode(content, sitemap_url))
    except ET.ParseError as exc:
        log.warning("Could not parse sitemap %s: %s", sitemap_url, exc)
        return []

    tag = root.tag.split("}")[-1]
    entries: list[UrlEntry] = []

    if tag == "sitemapindex":
        for sm in root.findall(f"{_SITEMAP_NS}sitemap"):
            loc = sm.findtext(f"{_SITEMAP_NS}loc")
            if loc:
                entries.extend(await _walk_sitemap(fetcher, loc.strip(), seen, depth + 1))
    else:  # urlset
        for u in root.findall(f"{_SITEMAP_NS}url"):
            loc = u.findtext(f"{_SITEMAP_NS}loc")
            if not loc:
                continue
            lastmod = _parse_lastmod(u.findtext(f"{_SITEMAP_NS}lastmod"))
            entries.append(UrlEntry(url=loc.strip(), lastmod=lastmod, source="sitemap"))
    return entries


async def discover_sitemap(fetcher: Fetcher, settings: Settings) -> list[UrlEntry]:
    """Return URL entries from the site's sitemap(s), or [] if none are found."""
    base = settings.site
    candidates = await _sitemap_urls_from_robots(fetcher, base)
    # Always also try the conventional location.
    conventional = urljoin(base, "/sitemap.xml")
    if conventional not in candidates:
        candidates.append(conventional)

    seen: set[str] = set()
    entries: list[UrlEntry] = []
    for sm in candidates:
        entries.extend(await _walk_sitemap(fetcher, sm, seen))

    return _clean(entries, settings)


def _clean(entries: list[UrlEntry], settings: Settings) -> list[UrlEntry]:
    """Normalize, same-site filter, drop binaries, apply include/exclude, dedupe."""
    out: dict[str, UrlEntry] = {}
    for e in entries:
        if not same_site(e.url, settings.site):
            continue
        if is_probably_binary(e.url):
            continue
        norm = normalize_url(e.url, strip_query=settings.strip_query)
        if not passes_filters(norm, settings.include, settings.exclude):
            continue
        if norm not in out:
            out[norm] = UrlEntry(
                url=norm, lastmod=e.lastmod, source=e.source, depth=e.depth
            )
    return list(out.values())
