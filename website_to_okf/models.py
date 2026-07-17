"""Data structures passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class UrlEntry:
    """A URL discovered for scraping, plus optional sitemap metadata."""

    url: str
    lastmod: Optional[datetime] = None
    source: str = "sitemap"  # "sitemap" | "crawl" | "seed"
    depth: int = 0


@dataclass
class RawPage:
    """A fetched HTML page."""

    url: str
    final_url: str
    html: str
    status: int
    content_type: str
    fetched_at: datetime
    rendered: bool = False  # True if fetched via the browser fallback


@dataclass
class Extracted:
    """Main content extracted from a page by the heuristic (trafilatura) stage."""

    url: str
    markdown: str
    links: list[str] = field(default_factory=list)  # absolute, same-site links
    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[str] = None  # ISO 8601 if trafilatura found one
    thin: bool = False  # True if content looked too short / JS-gated


@dataclass
class Concept:
    """A distilled page ready to be written as an OKF concept file."""

    url: str
    title: str
    description: str
    tags: list[str]
    markdown: str
    timestamp: Optional[str] = None  # ISO 8601
    type: str = "Web Page"
    links: list[str] = field(default_factory=list)  # same-site links (absolute)
    distilled: bool = False  # True if the LLM cleanup pass ran

    # Filled in by the OKF writer:
    path: Optional[str] = None  # bundle-relative path, e.g. "blog/post.md"
