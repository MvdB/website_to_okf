"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console

from .config import Settings
from .pipeline import run_async

console = Console(legacy_windows=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="website-to-okf",
        description="Scrape a website and distill it into an OKF (Open Knowledge Format) bundle.",
    )
    p.add_argument("site", nargs="?", help="Seed URL, e.g. https://example.com")
    p.add_argument("-o", "--output", type=Path, help="Output bundle directory (default ./bundle)")

    # Discovery / crawl
    p.add_argument("--max-pages", type=int, help="Maximum pages to process")
    p.add_argument("--max-depth", type=int, help="Maximum crawl depth")
    p.add_argument(
        "--follow-links",
        choices=["auto", "always", "never"],
        help="Crawl expansion: auto (fallback), always, or never",
    )
    p.add_argument("--include", action="append", help="Glob URL must match (repeatable)")
    p.add_argument("--exclude", action="append", help="Glob URL must not match (repeatable)")
    p.add_argument("--keep-query", action="store_true", help="Do not strip ?query from URLs")

    # Fetching
    p.add_argument(
        "--engine",
        choices=["crawl4ai", "trafilatura"],
        help="Fetch/extract engine (default crawl4ai: browser + fit_markdown; "
        "trafilatura: lightweight static-first)",
    )
    p.add_argument(
        "--prune-threshold",
        type=float,
        help="crawl4ai PruningContentFilter threshold (0-1, higher = more aggressive)",
    )
    p.add_argument(
        "--render",
        choices=["auto", "static", "browser"],
        help="Rendering mode (trafilatura engine only)",
    )
    p.add_argument("--concurrency", type=int, help="Concurrent fetches")
    p.add_argument("--delay", type=float, help="Seconds between requests to the same host")
    p.add_argument("--timeout", type=float, help="Per-request timeout (seconds)")
    p.add_argument("--no-robots", action="store_true", help="Do not respect robots.txt")
    p.add_argument("--user-agent", help="Override the User-Agent header")

    # Distillation
    p.add_argument("--no-llm", action="store_true", help="Skip the LLM cleanup pass")
    p.add_argument("--model", help="OpenAI-compatible model name")
    p.add_argument("--base-url", help="OpenAI-compatible base URL (e.g. local server)")
    p.add_argument("--api-key", help="API key (or set OPENAI_API_KEY)")
    p.add_argument("--llm-concurrency", type=int, help="Concurrent LLM calls")

    p.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")
    return p


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings()  # defaults + env + .env

    overrides: dict = {}
    if args.site:
        overrides["site"] = args.site
    if args.output is not None:
        overrides["output_dir"] = args.output
    if args.max_pages is not None:
        overrides["max_pages"] = args.max_pages
    if args.max_depth is not None:
        overrides["max_depth"] = args.max_depth
    if args.follow_links is not None:
        overrides["follow_links"] = args.follow_links
    if args.include:
        overrides["include"] = args.include
    if args.exclude:
        overrides["exclude"] = args.exclude
    if args.keep_query:
        overrides["strip_query"] = False
    if args.engine is not None:
        overrides["engine"] = args.engine
    if args.prune_threshold is not None:
        overrides["prune_threshold"] = args.prune_threshold
    if args.render is not None:
        overrides["render"] = args.render
    if args.concurrency is not None:
        overrides["concurrency"] = args.concurrency
    if args.delay is not None:
        overrides["request_delay"] = args.delay
    if args.timeout is not None:
        overrides["timeout"] = args.timeout
    if args.no_robots:
        overrides["respect_robots"] = False
    if args.user_agent is not None:
        overrides["user_agent"] = args.user_agent
    if args.no_llm:
        overrides["use_llm"] = False
    if args.model is not None:
        overrides["openai_model"] = args.model
    if args.base_url is not None:
        overrides["openai_base_url"] = args.base_url
    if args.api_key is not None:
        overrides["openai_api_key"] = args.api_key
    if args.llm_concurrency is not None:
        overrides["llm_concurrency"] = args.llm_concurrency

    return settings.model_copy(update=overrides)


def main(argv: list[str] | None = None) -> int:
    # Make output robust on legacy Windows code pages (cp1252 pipes/consoles).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    args = build_parser().parse_args(argv)
    level = logging.WARNING
    if args.verbose == 1:
        level = logging.INFO
    elif args.verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    settings = settings_from_args(args)
    if not settings.site:
        console.print("[red]error:[/] no site given (pass a URL or set W2OKF_SITE)")
        return 2
    if not settings.site.startswith(("http://", "https://")):
        settings = settings.model_copy(update={"site": "https://" + settings.site})

    console.print(f"[bold green]website-to-okf[/] -> {settings.output_dir}")
    stats = asyncio.run(run_async(settings))

    console.print(
        f"\n[bold]Done.[/] discovered={stats['discovered']} fetched={stats['fetched']} "
        f"distilled={stats['distilled']} written={stats['written']}"
    )
    console.print(f"Bundle: [cyan]{settings.output_dir.resolve()}[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
