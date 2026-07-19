"""On-disk buffering of pipeline stages so a run can resume where it stopped.

The pipeline has four stages: discover -> fetch+extract -> distill -> write.
Distillation is already resumable (content-hash cache in ``distill.py``). This
module buffers the two earlier stages so a crash, restart, or deliberate re-run
never re-pays for work already done:

  * ``.cache/discovered.json``      -- the discovered URL frontier (one write).
  * ``.cache/extracted/<sha>.json`` -- one file per extracted page, written the
    instant a page is extracted, so an interrupted crawl keeps every page it
    already fetched.

Everything lives under ``<output_dir>/.cache`` (git-ignored). Pass ``fresh=True``
to ignore the discover/extract buffers and re-crawl (the distill cache, keyed by
content hash, stays valid and is still reused).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from .models import Extracted, UrlEntry

log = logging.getLogger("website_to_okf.buffer")


def _sha(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


class StageBuffer:
    """Reads/writes the discover and fetch+extract stage checkpoints."""

    def __init__(self, output_dir: Path, fresh: bool = False):
        self.cache_dir = output_dir / ".cache"
        self.extracted_dir = self.cache_dir / "extracted"
        self.discovered_path = self.cache_dir / "discovered.json"
        self.fresh = fresh

    # -- discovery -----------------------------------------------------
    def load_discovered(self) -> list[UrlEntry] | None:
        if self.fresh or not self.discovered_path.exists():
            return None
        try:
            raw = json.loads(self.discovered_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        entries: list[UrlEntry] = []
        for d in raw:
            lastmod = d.get("lastmod")
            entries.append(
                UrlEntry(
                    url=d["url"],
                    lastmod=datetime.fromisoformat(lastmod) if lastmod else None,
                    source=d.get("source", "sitemap"),
                    depth=d.get("depth", 0),
                )
            )
        return entries

    def save_discovered(self, entries: list[UrlEntry]) -> None:
        payload = [
            {
                "url": e.url,
                "lastmod": e.lastmod.isoformat() if e.lastmod else None,
                "source": e.source,
                "depth": e.depth,
            }
            for e in entries
        ]
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.discovered_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # noqa: BLE001 - buffering is best-effort
            log.warning("could not write discovered buffer: %s", exc)

    # -- fetch + extract ----------------------------------------------
    def _extracted_path(self, url: str) -> Path:
        return self.extracted_dir / f"{_sha(url)}.json"

    def has_extracted(self, url: str) -> bool:
        return not self.fresh and self._extracted_path(url).exists()

    def save_extracted(self, ext: Extracted) -> None:
        payload = {
            "url": ext.url,
            "markdown": ext.markdown,
            "links": ext.links,
            "title": ext.title,
            "description": ext.description,
            "date": ext.date,
            "thin": ext.thin,
        }
        try:
            self.extracted_dir.mkdir(parents=True, exist_ok=True)
            self._extracted_path(ext.url).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # noqa: BLE001 - buffering is best-effort
            log.warning("could not buffer extracted page %s: %s", ext.url, exc)

    def load_all_extracted(self) -> dict[str, Extracted]:
        """Return every buffered extracted page, keyed by URL."""
        out: dict[str, Extracted] = {}
        if self.fresh or not self.extracted_dir.exists():
            return out
        for path in self.extracted_dir.glob("*.json"):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            out[d["url"]] = Extracted(
                url=d["url"],
                markdown=d.get("markdown", ""),
                links=d.get("links", []),
                title=d.get("title"),
                description=d.get("description"),
                date=d.get("date"),
                thin=d.get("thin", False),
            )
        return out
