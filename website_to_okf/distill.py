"""LLM distillation via an OpenAI-compatible API.

Takes the heuristic (trafilatura) markdown and produces cleaned markdown plus a
title, one-sentence description, and tags. Results are cached by content hash so
re-runs and long crawls don't re-pay. On any error we degrade gracefully to the
heuristic extraction, so a page always lands in the bundle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config import Settings
from .models import Concept, Extracted

log = logging.getLogger("website_to_okf.distill")

_SYSTEM = (
    "You clean scraped web-page content for a knowledge base. You are given the "
    "main text of one page (already stripped of most navigation) in markdown. "
    "Return ONLY faithful content: remove any remaining navigation, breadcrumbs, "
    "cookie/consent notices, share buttons, calls-to-action, related-post lists, "
    "and boilerplate. Preserve the substantive prose, headings, lists, tables, and "
    "in-text markdown links exactly as written. Do NOT summarize, invent, or add "
    "content. Respond with a single JSON object."
)

_USER_TMPL = (
    "URL: {url}\n"
    "Existing title: {title}\n"
    "Existing description: {description}\n\n"
    "Return JSON with keys:\n"
    '  "title": concise human-readable page title,\n'
    '  "description": one factual sentence summarizing the page,\n'
    '  "tags": array of 1-6 short lowercase topical tags,\n'
    '  "markdown": the cleaned main content as markdown.\n\n'
    "PAGE MARKDOWN:\n{markdown}"
)

_CLEAN_ONLY = (
    "Clean this continuation chunk of the same page. Remove boilerplate/navigation, "
    "keep substantive content and markdown links. Respond with JSON: "
    '{{"markdown": "<cleaned markdown>"}}.\n\nCHUNK:\n{markdown}'
)


def _title_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    if not path:
        return urlparse(url).netloc
    slug = unquote(path.rsplit("/", 1)[-1])
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug, flags=re.IGNORECASE)
    slug = slug.replace("-", " ").replace("_", " ").strip()
    return slug.title() or urlparse(url).netloc


def _first_sentence(markdown: str) -> str:
    text = re.sub(r"[#*`>\-\[\]()!]", " ", markdown)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    m = re.search(r"(.+?[.!?])(\s|$)", text)
    sentence = m.group(1) if m else text[:200]
    return sentence.strip()[:300]


def heuristic_concept(ext: Extracted, settings: Settings) -> Concept:
    """Build a Concept without the LLM (used in --no-llm mode and as fallback)."""
    title = (ext.title or "").strip() or _title_from_url(ext.url)
    description = (ext.description or "").strip() or _first_sentence(ext.markdown)
    return Concept(
        url=ext.url,
        title=title,
        description=description,
        tags=[],
        markdown=ext.markdown,
        timestamp=ext.date,
        type=settings.concept_type,
        links=ext.links,
        distilled=False,
    )


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    # Strip ```json fences if the model added them.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _chunk(markdown: str, size: int) -> list[str]:
    if len(markdown) <= size:
        return [markdown]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for para in markdown.split("\n\n"):
        if length + len(para) > size and current:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(para)
        length += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


class Distiller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_dir = settings.output_dir / ".cache"
        self._client = None
        # url -> content-hash of its last SUCCESSFUL distillation. Lets a failed
        # re-distill fall back to the prior distilled result instead of heuristic,
        # so re-runs are non-destructive (they only ever improve).
        self._url_map_path = self.cache_dir / "url_to_hash.json"
        self._url_map: dict[str, str] = {}
        try:
            self._url_map = json.loads(self._url_map_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._url_map = {}

    def _remember(self, url: str, key: str) -> None:
        self._url_map[url] = key
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._url_map_path.write_text(json.dumps(self._url_map), encoding="utf-8")
        except OSError:
            pass

    def _prior_distilled(self, ext: Extracted) -> Concept | None:
        """Return a previously distilled result for this URL, if one exists."""
        prior_key = self._url_map.get(ext.url)
        if not prior_key:
            return None
        path = self._cache_path(prior_key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return self._concept_from_data(ext, data)

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=self.settings.openai_base_url,
                api_key=self.settings.openai_api_key or "not-needed",
            )
        return self._client

    def _cache_key(self, ext: Extracted) -> str:
        h = hashlib.sha256()
        h.update(self.settings.openai_model.encode())
        h.update(b"\0")
        h.update(ext.markdown.encode("utf-8"))
        return h.hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    async def _call(self, system: str, user: str) -> str:
        client = self._get_client()
        kwargs = dict(
            model=self.settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
        )
        # Prefer JSON mode; retry without it if the endpoint rejects the param.
        try:
            resp = await client.chat.completions.create(
                response_format={"type": "json_object"}, **kwargs
            )
        except Exception:  # noqa: BLE001 - many local servers lack response_format
            resp = await client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    async def distill(self, ext: Extracted) -> Concept:
        if not ext.markdown.strip():
            return heuristic_concept(ext, self.settings)

        key = self._cache_key(ext)
        cached = self._cache_path(key)
        if cached.exists():
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
                self._remember(ext.url, key)
                return self._concept_from_data(ext, data)
            except (json.JSONDecodeError, OSError):
                pass

        try:
            data = await self._distill_uncached(ext)
        except Exception as exc:  # noqa: BLE001 - never let one page kill the run
            # Non-destructive: if this URL was distilled on a prior run, keep that
            # result rather than downgrading to heuristic on a transient failure.
            prior = self._prior_distilled(ext)
            if prior is not None:
                log.warning("LLM distillation failed for %s: %s (kept prior distilled)", ext.url, exc)
                return prior
            log.warning("LLM distillation failed for %s: %s (using heuristic)", ext.url, exc)
            return heuristic_concept(ext, self.settings)

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        self._remember(ext.url, key)
        return self._concept_from_data(ext, data)

    async def _distill_uncached(self, ext: Extracted) -> dict:
        chunks = _chunk(ext.markdown, self.settings.max_chunk_chars)
        first = await self._call(
            _SYSTEM,
            _USER_TMPL.format(
                url=ext.url,
                title=ext.title or "(none)",
                description=ext.description or "(none)",
                markdown=chunks[0],
            ),
        )
        data = _extract_json(first) or {}
        body_parts = [str(data.get("markdown", "")).strip()]
        for chunk in chunks[1:]:
            more = await self._call(_SYSTEM, _CLEAN_ONLY.format(markdown=chunk))
            more_data = _extract_json(more) or {}
            body_parts.append(str(more_data.get("markdown", "")).strip())
        data["markdown"] = "\n\n".join(p for p in body_parts if p)
        return data

    def _concept_from_data(self, ext: Extracted, data: dict) -> Concept:
        base = heuristic_concept(ext, self.settings)
        title = str(data.get("title") or "").strip() or base.title
        description = str(data.get("description") or "").strip() or base.description
        markdown = str(data.get("markdown") or "").strip() or base.markdown
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        tags = [str(t).strip().lower() for t in tags if str(t).strip()]
        return Concept(
            url=ext.url,
            title=title,
            description=description,
            tags=tags,
            markdown=markdown,
            timestamp=ext.date,
            type=self.settings.concept_type,
            links=ext.links,
            distilled=True,
        )
