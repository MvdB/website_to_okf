"""Runtime configuration for website_to_okf.

Settings come from (in increasing priority): defaults, environment variables
(optionally a local ``.env`` file), and CLI flags. Environment variables are
prefixed ``W2OKF_`` except the OpenAI ones, which also accept the conventional
``OPENAI_*`` names so existing setups work unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RenderMode = Literal["auto", "static", "browser"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="W2OKF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Target & output ---
    site: str = ""  # seed URL, e.g. https://example.com
    output_dir: Path = Path("./bundle")

    # --- Discovery / crawl ---
    max_pages: int = 500
    max_depth: int = 6
    include: list[str] = Field(default_factory=list)  # glob patterns on URL
    exclude: list[str] = Field(default_factory=list)
    strip_query: bool = True  # drop ?query when normalizing URLs
    # "auto"  -> crawl-expand only when the sitemap yields nothing (true fallback)
    # "always"-> always follow discovered links (sitemap + crawl)
    # "never" -> sitemap/seed only, never follow discovered links
    follow_links: Literal["auto", "always", "never"] = "auto"

    # --- Fetching ---
    # "crawl4ai" -> browser engine with fit_markdown (robust on JS/anti-bot sites)
    # "trafilatura" -> lightweight static-first httpx + trafilatura (browser fallback)
    engine: Literal["crawl4ai", "trafilatura"] = "crawl4ai"
    render: RenderMode = "auto"  # trafilatura engine only
    prune_threshold: float = 0.48  # crawl4ai PruningContentFilter threshold
    concurrency: int = 8
    request_delay: float = 0.2  # seconds between requests to the same host
    timeout: float = 30.0
    respect_robots: bool = True
    user_agent: str = "website-to-okf/0.1 (+https://github.com/)"
    thin_threshold: int = 200  # chars of main text below which we try the browser

    # --- Distillation (LLM) ---
    use_llm: bool = True
    openai_base_url: Optional[str] = Field(default=None, validation_alias="OPENAI_BASE_URL")
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    llm_concurrency: int = 4
    max_chunk_chars: int = 12000  # split page markdown into chunks of this size

    # --- OKF ---
    concept_type: str = "Web Page"
    bundle_title: str = ""  # defaults to the site host if empty
