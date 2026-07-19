"""OKF path mapping, link rewriting, concept rendering, and bundle writing."""

import json

import yaml

from website_to_okf.config import Settings
from website_to_okf.models import Concept
from website_to_okf.okf import (
    OKF_VERSION,
    OkfWriter,
    build_path_map,
    render_concept,
    rewrite_links,
)


def _concept(url, title="T", desc="d", markdown="body", tags=None, links=None):
    return Concept(
        url=url,
        title=title,
        description=desc,
        tags=tags or [],
        markdown=markdown,
        type="Web Page",
        links=links or [],
        distilled=True,
    )


def test_path_map_home_and_tree():
    s = Settings(site="https://x.com")
    concepts = [_concept("https://x.com/"), _concept("https://x.com/blog/post")]
    pm = build_path_map(concepts, s)
    assert pm["https://x.com/"] == "home.md"
    assert pm["https://x.com/blog/post"] == "blog/post.md"


def test_path_map_avoids_reserved_names():
    s = Settings(site="https://x.com")
    pm = build_path_map([_concept("https://x.com/index")], s)
    # index.md / log.md are reserved -> a concept must not claim them.
    assert pm["https://x.com/index"] not in ("index.md", "log.md")
    assert pm["https://x.com/index"] == "index-page.md"


def test_path_map_disambiguates_collisions():
    s = Settings(site="https://x.com")
    # Different URLs that slug to the same candidate path.
    concepts = [_concept("https://x.com/Foo"), _concept("https://x.com/foo")]
    pm = build_path_map(concepts, s)
    assert pm["https://x.com/Foo"] != pm["https://x.com/foo"]
    assert len(set(pm.values())) == 2


def test_rewrite_links_internal_vs_external():
    s = Settings(site="https://x.com")
    pm = {"https://x.com/a": "a.md", "https://x.com/b": "b.md"}
    md = "See [B](/b) and [ext](https://other.com/x) and [mail](mailto:a@b.c)."
    out = rewrite_links(md, "https://x.com/a", pm, s)
    assert "](/b.md)" in out            # internal -> bundle-relative absolute
    assert "https://other.com/x" in out  # external untouched
    assert "mailto:a@b.c" in out         # mailto untouched


def test_render_concept_frontmatter_and_citation():
    s = Settings(site="https://x.com")
    pm = {"https://x.com/a": "a.md"}
    c = _concept("https://x.com/a", title="Hello", markdown="Some text.")
    out = render_concept(c, pm, s)
    assert out.startswith("---\n")
    fm = yaml.safe_load(out.split("---", 2)[1])
    assert fm["type"] == "Web Page"
    assert fm["resource"] == "https://x.com/a"
    # Citation keeps the real external source URL (added after link rewriting).
    assert "# Citations" in out
    assert "(https://x.com/a)" in out


def test_render_concept_no_duplicate_h1():
    s = Settings(site="https://x.com")
    c = _concept("https://x.com/a", title="Hello", markdown="# Hello\n\nbody")
    out = render_concept(c, {"https://x.com/a": "a.md"}, s)
    assert out.count("# Hello") == 1


def test_render_concept_can_disable_citations():
    s = Settings(site="https://x.com", add_citations=False)
    c = _concept("https://x.com/a")
    assert "# Citations" not in render_concept(c, {"https://x.com/a": "a.md"}, s)


def test_writer_end_to_end(tmp_path):
    s = Settings(site="https://x.com", output_dir=tmp_path, use_llm=False)
    concepts = [
        _concept("https://x.com/", title="Home", markdown="Welcome"),
        _concept("https://x.com/blog/post", title="Post", markdown="Body"),
    ]
    stats = {"discovered": 2, "fetched": 2, "distilled": 0, "written": 0}
    OkfWriter(s).write(concepts, stats)

    # written stat is set before log/manifest are produced.
    assert stats["written"] == 2

    root_index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert root_index.startswith(f'---\nokf_version: "{OKF_VERSION}"\n---')

    # Subdirectory index carries NO frontmatter (spec rule).
    sub_index = (tmp_path / "blog" / "index.md").read_text(encoding="utf-8")
    assert not sub_index.startswith("---")
    assert "* [Post](post.md) - d" in sub_index

    # log.md records the real count and uses an ISO date heading.
    log = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert "wrote 2 concepts" in log
    assert log.lstrip().startswith("## ")

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stats"]["written"] == 2
    assert len(manifest["concepts"]) == 2
