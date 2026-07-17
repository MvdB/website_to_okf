"""Heuristic content extraction with trafilatura.

Strips headers/footers/nav/banners and returns the main content as markdown,
the set of same-site links, and page metadata (title/description/date).
"""

from __future__ import annotations

import logging
import re

import trafilatura
from trafilatura.metadata import extract_metadata

from .config import Settings
from .models import Extracted, RawPage
from .urls import is_probably_binary, normalize_url, resolve, same_site

log = logging.getLogger("website_to_okf.extract")

_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _collect_links(page: RawPage, settings: Settings) -> list[str]:
    """Same-site links from the raw HTML (used for linkage + crawl fallback)."""
    out: dict[str, None] = {}
    for m in _HREF.finditer(page.html):
        href = m.group(1)
        if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        abs_url = resolve(page.final_url, href)
        if not same_site(abs_url, settings.site):
            continue
        if is_probably_binary(abs_url):
            continue
        norm = normalize_url(abs_url, strip_query=settings.strip_query)
        out.setdefault(norm, None)
    return list(out.keys())


def extract(page: RawPage, settings: Settings) -> Extracted | None:
    """Extract main-content markdown + metadata from a fetched page."""
    markdown = trafilatura.extract(
        page.html,
        url=page.final_url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        favor_precision=True,
        with_metadata=False,
    )
    if markdown is None:
        markdown = ""

    title = description = date = None
    try:
        meta = extract_metadata(page.html, default_url=page.final_url)
        if meta is not None:
            title = meta.title
            description = meta.description
            date = meta.date
    except Exception as exc:  # noqa: BLE001 - trafilatura metadata can be fragile
        log.debug("metadata extraction failed for %s: %s", page.url, exc)

    links = _collect_links(page, settings)
    thin = len(markdown.strip()) < settings.thin_threshold

    return Extracted(
        url=page.url,
        markdown=markdown.strip(),
        links=links,
        title=title,
        description=description,
        date=date,
        thin=thin,
    )
