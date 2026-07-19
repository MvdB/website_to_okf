"""Distillation helpers that don't require a live model."""

from website_to_okf.config import Settings
from website_to_okf.distill import (
    _chunk,
    _extract_json,
    _first_sentence,
    _title_from_url,
    heuristic_concept,
)
from website_to_okf.models import Extracted


def test_title_from_url():
    assert _title_from_url("https://x.com/foo-bar-baz/") == "Foo Bar Baz"
    assert _title_from_url("https://x.com/a/my_page.html") == "My Page"
    # Root path falls back to the host.
    assert _title_from_url("https://x.com/") == "x.com"


def test_first_sentence():
    assert _first_sentence("Hello world. More text here.") == "Hello world."
    # No sentence terminator -> the whole (cleaned) text, capped.
    assert _first_sentence("Single line no period") == "Single line no period"
    assert _first_sentence("   ") == ""


def test_extract_json_plain_fenced_and_embedded():
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('noise before {"a": 1} noise after') == {"a": 1}
    assert _extract_json("not json at all") is None


def test_chunk_splits_on_paragraph_boundaries():
    assert _chunk("short", 100) == ["short"]
    text = "\n\n".join(["para " + str(i) * 50 for i in range(10)])
    chunks = _chunk(text, 200)
    assert len(chunks) > 1
    # Reassembly preserves all paragraphs.
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_heuristic_concept_fields():
    s = Settings(concept_type="Web Page")
    ext = Extracted(
        url="https://x.com/a",
        markdown="Some content sentence. And more.",
        links=["https://x.com/b"],
        title="",
        description="",
    )
    c = heuristic_concept(ext, s)
    assert c.distilled is False
    assert c.type == "Web Page"
    assert c.title == "A"  # derived from the URL slug
    assert c.description == "Some content sentence."
    assert c.links == ["https://x.com/b"]
