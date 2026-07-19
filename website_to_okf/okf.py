"""Map distilled concepts onto an OKF bundle and write it to disk.

OKF rules honored here (SPEC v0.1):
  * every non-reserved .md is a concept with YAML frontmatter and a non-empty ``type``;
  * ``index.md`` / ``log.md`` are reserved -- concepts are never named these;
  * links are bundle-relative absolute (``/a/b.md``); broken links are tolerated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

import yaml
from slugify import slugify

from .config import Settings
from .models import Concept
from .urls import normalize_url, resolve

log = logging.getLogger("website_to_okf.okf")

_RESERVED_STEMS = {"index", "log"}
_MD_LINK = re.compile(r"(\]\()([^)\s]+)(\))")
# Bundle format version. Per SPEC.md this may be declared in the root index.md,
# the one place frontmatter is permitted in an index.md.
OKF_VERSION = "0.1"


# --------------------------------------------------------------------------
# URL -> bundle path
# --------------------------------------------------------------------------
def _slug_segment(seg: str) -> str:
    # Keep the extension out of the slug but preserve a readable stem.
    stem = re.sub(r"\.(html?|php|aspx?|jsp)$", "", seg, flags=re.IGNORECASE)
    slug = slugify(stem) or "page"
    if slug in _RESERVED_STEMS:
        slug = f"{slug}-page"
    return slug


def _candidate_path(url: str) -> str:
    path = urlparse(url).path
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "home.md"
    slugged = [_slug_segment(s) for s in segments]
    return "/".join(slugged) + ".md"


def build_path_map(concepts: list[Concept], settings: Settings) -> dict[str, str]:
    """Assign each concept a unique bundle-relative path (reserved-name-safe)."""
    used: set[str] = set()
    mapping: dict[str, str] = {}
    # Deterministic order so re-runs are stable.
    for c in sorted(concepts, key=lambda c: c.url):
        candidate = _candidate_path(c.url)
        if candidate in used:
            stem, dot, ext = candidate.rpartition(".")
            suffix = hashlib.sha1(c.url.encode()).hexdigest()[:6]
            candidate = f"{stem}-{suffix}.{ext}"
        used.add(candidate)
        mapping[c.url] = candidate
    return mapping


# --------------------------------------------------------------------------
# Link rewriting
# --------------------------------------------------------------------------
def rewrite_links(markdown: str, source_url: str, path_map: dict[str, str], settings: Settings) -> str:
    def repl(m: re.Match) -> str:
        target = m.group(2)
        if target.startswith(("mailto:", "tel:", "#")):
            return m.group(0)
        abs_url = resolve(source_url, target)
        norm = normalize_url(abs_url, strip_query=settings.strip_query)
        if norm in path_map:
            return f"{m.group(1)}/{path_map[norm]}{m.group(3)}"
        return m.group(0)

    return _MD_LINK.sub(repl, markdown)


# --------------------------------------------------------------------------
# Concept file rendering
# --------------------------------------------------------------------------
def render_concept(c: Concept, path_map: dict[str, str], settings: Settings) -> str:
    fm: dict[str, object] = {"type": c.type or settings.concept_type}
    if c.title:
        fm["title"] = c.title
    if c.description:
        fm["description"] = c.description
    fm["resource"] = c.url
    if c.tags:
        fm["tags"] = c.tags
    if c.timestamp:
        fm["timestamp"] = c.timestamp

    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = rewrite_links(c.markdown, c.url, path_map, settings).strip()
    # Avoid a duplicate H1 when the extracted body already starts with one.
    heading = "" if body.lstrip().startswith("# ") else f"# {c.title}\n\n"
    # Provenance: append a Citations section pointing at the source page.
    # Added AFTER link rewriting so this URL stays the real external source.
    citation = ""
    if settings.add_citations:
        label = c.title or c.url
        citation = f"\n\n# Citations\n\n[1] [{label}]({c.url})"
    return f"---\n{front}\n---\n\n{heading}{body}{citation}\n"


# --------------------------------------------------------------------------
# index.md generation
# --------------------------------------------------------------------------
def _build_dir_tree(paths: list[str]):
    dirs: dict[str, dict] = defaultdict(lambda: {"files": [], "subdirs": set()})
    for p in paths:
        parts = p.split("/")
        dpath = "/".join(parts[:-1])
        dirs[dpath]["files"].append(parts[-1])
        for i in range(len(parts) - 1):
            ancestor = "/".join(parts[:i])
            dirs[ancestor]["subdirs"].add(parts[i])
    return dirs


def _index_for_dir(
    dpath: str,
    info: dict,
    concept_by_path: dict[str, Concept],
    bundle_title: str,
) -> str:
    lines: list[str] = []
    heading = bundle_title if dpath == "" else f"{dpath}/"
    lines.append(f"# {heading}\n")
    for sub in sorted(info["subdirs"]):
        lines.append(f"* [{sub}/]({sub}/index.md)")
    for fname in sorted(info["files"]):
        full = f"{dpath}/{fname}" if dpath else fname
        c = concept_by_path.get(full)
        title = c.title if c else fname
        desc = f" - {c.description}" if c and c.description else ""
        lines.append(f"* [{title}]({fname}){desc}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------
class OkfWriter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.output_dir

    def write(self, concepts: list[Concept], stats: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        path_map = build_path_map(concepts, self.settings)

        concept_by_path: dict[str, Concept] = {}
        manifest_entries = []
        for c in concepts:
            c.path = path_map[c.url]
            concept_by_path[c.path] = c
            target = self.root / c.path
            target.parent.mkdir(parents=True, exist_ok=True)
            content = render_concept(c, path_map, self.settings)
            target.write_text(content, encoding="utf-8")
            manifest_entries.append(
                {
                    "url": c.url,
                    "path": c.path,
                    "title": c.title,
                    "distilled": c.distilled,
                    "hash": hashlib.sha256(c.markdown.encode("utf-8")).hexdigest()[:16],
                    "timestamp": c.timestamp,
                }
            )

        # Set before log/manifest so both record the real written count.
        stats["written"] = len(concepts)
        self._write_indexes(concept_by_path)
        self._write_log(stats)
        self._write_manifest(manifest_entries, stats)
        if self.settings.write_viz:
            self._write_viz(concepts, path_map)
        return path_map

    def _write_viz(self, concepts: list[Concept], path_map: dict[str, str]) -> None:
        from .viz import render_viz

        html = render_viz(concepts, path_map, self.settings)
        (self.root / "viz.html").write_text(html, encoding="utf-8")

    def _bundle_title(self) -> str:
        if self.settings.bundle_title:
            return self.settings.bundle_title
        host = urlparse(self.settings.site).netloc or "Bundle"
        return host

    def _write_indexes(self, concept_by_path: dict[str, Concept]) -> None:
        dirs = _build_dir_tree(list(concept_by_path.keys()))
        title = self._bundle_title()
        for dpath, info in dirs.items():
            content = _index_for_dir(dpath, info, concept_by_path, title)
            if dpath == "":
                # SPEC: the root index.md may declare the bundle's target version;
                # this is the only frontmatter permitted in an index.md.
                content = f'---\nokf_version: "{OKF_VERSION}"\n---\n\n{content}'
            index_path = self.root / dpath / "index.md" if dpath else self.root / "index.md"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(content, encoding="utf-8")

    def _write_log(self, stats: dict) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        line = (
            f"## {today}\n\n"
            f"Generated OKF bundle from {self.settings.site}. "
            f"Discovered {stats.get('discovered', 0)}, fetched {stats.get('fetched', 0)}, "
            f"distilled {stats.get('distilled', 0)}, wrote {stats.get('written', 0)} concepts.\n"
        )
        log_path = self.root / "log.md"
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        log_path.write_text(line + ("\n" + existing if existing else ""), encoding="utf-8")

    def _write_manifest(self, entries: list[dict], stats: dict) -> None:
        manifest = {
            "site": self.settings.site,
            "generated": datetime.now(timezone.utc).isoformat(),
            "model": self.settings.openai_model if self.settings.use_llm else None,
            "stats": stats,
            "concepts": entries,
        }
        (self.root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
