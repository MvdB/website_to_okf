"""Orchestrate discovery -> fetch -> extract -> distill -> write."""

from __future__ import annotations

import asyncio
import logging

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from .buffer import StageBuffer
from .config import Settings
from .discover import discover_sitemap
from .distill import Distiller, heuristic_concept
from .engine import build_engine
from .fetch import Fetcher
from .models import Concept, Extracted, UrlEntry
from .okf import OkfWriter
from .urls import (
    is_probably_binary,
    normalize_url,
    passes_filters,
    same_site,
)

log = logging.getLogger("website_to_okf.pipeline")
console = Console(legacy_windows=False)


class Pipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.fetcher = Fetcher(settings)  # used for discovery (sitemap/robots)
        self.engine = build_engine(settings, self.fetcher)
        self.buffer = StageBuffer(settings.output_dir, fresh=settings.fresh)

    async def run(self) -> dict:
        s = self.settings
        stats = {"discovered": 0, "fetched": 0, "distilled": 0, "written": 0}
        console.print(f"Engine: [magenta]{s.engine}[/]")
        try:
            await self.engine.setup()
            extracted = await self._discover_and_extract(stats)
            concepts = await self._distill_all(extracted, stats)
            writer = OkfWriter(s)
            writer.write(concepts, stats)
            stats["written"] = len(concepts)
        finally:
            await self.engine.close()
            await self.fetcher.close()
        return stats

    # ------------------------------------------------------------------
    async def _discover_and_extract(self, stats: dict) -> list[Extracted]:
        s = self.settings
        seed = normalize_url(s.site, strip_query=s.strip_query)

        # --- Stage 1: discovery (buffered) ---------------------------
        frontier = self.buffer.load_discovered()
        if frontier is not None:
            console.print(
                f"[bold]Discovery[/] resumed from buffer: [cyan]{len(frontier)}[/] URL(s)"
            )
        else:
            console.print(f"[bold]Discovering[/] {s.site} ...")
            sitemap_entries = await discover_sitemap(self.fetcher, s)
            console.print(f"  sitemap yielded [cyan]{len(sitemap_entries)}[/] URL(s)")
            if sitemap_entries:
                frontier = sitemap_entries
            else:
                frontier = [UrlEntry(url=seed, source="seed", depth=0)]
            self.buffer.save_discovered(frontier)

        if s.follow_links == "always":
            expand = True
        elif s.follow_links == "never":
            expand = False
        else:  # auto -> crawl only when discovery found no sitemap URLs
            expand = not any(e.source == "sitemap" for e in frontier)
        if expand:
            console.print("  crawl expansion [green]enabled[/]")

        # --- Stage 2: fetch + extract (buffered per page) ------------
        buffered = self.buffer.load_all_extracted()
        results: list[Extracted] = list(buffered.values())
        seen: set[str] = {e.url for e in frontier} | set(buffered)
        if buffered:
            console.print(
                f"  fetch resumed from buffer: [cyan]{len(buffered)}[/] page(s) already extracted"
            )
        # Only crawl URLs we have not already extracted.
        frontier = [e for e in frontier if e.url not in buffered]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
        ) as progress:
            total = min(len(results) + len(frontier), s.max_pages)
            task = progress.add_task("Fetching", total=total, completed=len(results))
            while frontier and len(results) < s.max_pages:
                remaining = s.max_pages - len(results)
                batch = frontier[:remaining]
                frontier = frontier[remaining:]

                sem = asyncio.Semaphore(s.concurrency)

                async def worker(entry: UrlEntry):
                    async with sem:
                        return entry, await self._fetch_and_extract(entry)

                pairs = await asyncio.gather(*(worker(e) for e in batch))

                next_frontier: list[UrlEntry] = []
                new_urls: list[UrlEntry] = []
                for entry, ext in pairs:
                    progress.update(task, advance=1)
                    if ext is None:
                        continue
                    stats["fetched"] += 1
                    self.buffer.save_extracted(ext)  # checkpoint immediately
                    results.append(ext)
                    if expand and entry.depth < s.max_depth:
                        for link in ext.links:
                            if link in seen:
                                continue
                            if not same_site(link, s.site) or is_probably_binary(link):
                                continue
                            if not passes_filters(link, s.include, s.exclude):
                                continue
                            seen.add(link)
                            new_entry = UrlEntry(url=link, source="crawl", depth=entry.depth + 1)
                            next_frontier.append(new_entry)
                            new_urls.append(new_entry)
                frontier = frontier + next_frontier
                # Persist newly discovered URLs so an expansion crawl is resumable too.
                if new_urls:
                    self._append_discovered(new_urls, seen)
                progress.update(
                    task, total=min(len(results) + len(frontier), s.max_pages)
                )

        stats["discovered"] = len(seen)
        console.print(f"  extracted content from [cyan]{len(results)}[/] page(s)")
        return results

    def _append_discovered(self, new_urls: list[UrlEntry], seen: set[str]) -> None:
        """Grow the discovered buffer during an expansion crawl (best-effort)."""
        existing = self.buffer.load_discovered() or []
        known = {e.url for e in existing}
        merged = existing + [e for e in new_urls if e.url not in known]
        self.buffer.save_discovered(merged)

    async def _fetch_and_extract(self, entry: UrlEntry) -> Extracted | None:
        ext = await self.engine.fetch_extract(entry)
        if ext is None:
            return None
        # Preserve sitemap lastmod as a timestamp fallback.
        if entry.lastmod and not ext.date:
            ext.date = entry.lastmod.isoformat()
        return ext

    # ------------------------------------------------------------------
    async def _distill_all(self, extracted: list[Extracted], stats: dict) -> list[Concept]:
        s = self.settings
        if not s.use_llm:
            concepts = [heuristic_concept(e, s) for e in extracted]
            console.print("LLM distillation [yellow]disabled[/] (--no-llm)")
            return concepts

        distiller = Distiller(s)
        sem = asyncio.Semaphore(s.llm_concurrency)
        console.print(
            f"Distilling [cyan]{len(extracted)}[/] page(s) with model "
            f"[magenta]{s.openai_model}[/] ..."
        )

        async def one(e: Extracted) -> Concept:
            async with sem:
                c = await distiller.distill(e)
                if c.distilled:
                    stats["distilled"] += 1
                return c

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Distilling", total=len(extracted))

            async def tracked(e):
                c = await one(e)
                progress.update(task, advance=1)
                return c

            concepts = await asyncio.gather(*(tracked(e) for e in extracted))
        return list(concepts)


async def run_async(settings: Settings) -> dict:
    return await Pipeline(settings).run()
