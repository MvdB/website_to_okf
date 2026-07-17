"""URL normalization and same-site helpers."""

from __future__ import annotations

import fnmatch
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

# File extensions we never treat as HTML pages.
BINARY_EXTS = {
    ".pdf", ".zip", ".gz", ".tar", ".rar", ".7z", ".doc", ".docx", ".xls",
    ".xlsx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".svg", ".ico", ".mp3", ".mp4", ".avi", ".mov", ".webm", ".wav",
    ".woff", ".woff2", ".ttf", ".eot", ".css", ".js", ".json", ".xml",
    ".rss", ".atom", ".csv", ".dmg", ".exe", ".apk", ".bin",
}


def normalize_url(url: str, *, strip_query: bool = True) -> str:
    """Canonicalize a URL for dedup: drop fragment, lowercase host, tidy path."""
    url, _frag = urldefrag(url)
    parts = urlparse(url)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    # Drop default ports.
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parts.path or "/"
    # Collapse duplicate slashes but keep a single trailing slash meaningful.
    while "//" in path:
        path = path.replace("//", "/")
    query = "" if strip_query else parts.query
    return urlunparse((scheme, netloc, path, "", query, ""))


def registrable_host(url: str) -> str:
    """Host without a leading ``www.`` — a cheap same-site key (no PSL needed)."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def same_site(url: str, base: str) -> bool:
    return registrable_host(url) == registrable_host(base)


def is_probably_binary(url: str) -> bool:
    path = urlparse(url).path.lower()
    dot = path.rfind(".")
    if dot == -1:
        return False
    return path[dot:] in BINARY_EXTS


def resolve(base: str, href: str) -> str:
    return urljoin(base, href)


def matches_any(url: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(url, p) for p in patterns)


def passes_filters(url: str, include: list[str], exclude: list[str]) -> bool:
    if include and not matches_any(url, include):
        return False
    if exclude and matches_any(url, exclude):
        return False
    return True
