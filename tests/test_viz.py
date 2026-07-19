"""Graph building + self-contained viz rendering."""

from website_to_okf.config import Settings
from website_to_okf.models import Concept
from website_to_okf.viz import build_graph, render_viz


def _concepts(pm, specs):
    """Build concepts from (url, markdown[, links]) specs, with paths assigned.

    The OKF writer sets each concept's .path before rendering the graph, so the
    tests mirror that: node ids and edge endpoints are bundle paths.
    """
    out = []
    for url, markdown, *rest in specs:
        links = rest[0] if rest else []
        c = Concept(
            url=url, title="T", description="d", tags=[], markdown=markdown,
            type="Web Page", links=links, distilled=True,
        )
        c.path = pm[url]
        out.append(c)
    return out


def test_edges_come_from_content_links_not_raw_link_set():
    """Regression: edges must derive from distilled-markdown links, not the raw
    same-site link set (which is dominated by nav/footer boilerplate)."""
    s = Settings(site="https://x.com")
    pm = {
        "https://x.com/a": "a.md",
        "https://x.com/b": "b.md",
        "https://x.com/nav": "nav.md",
    }
    concepts = _concepts(pm, [
        # markdown links to /b; 'nav' appears only in the raw links list.
        ("https://x.com/a", "Read [the B page](/b).", ["https://x.com/nav"]),
        ("https://x.com/b", "Back to start."),
        ("https://x.com/nav", "Menu."),
    ])
    graph = build_graph(concepts, pm, s)
    edges = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("a.md", "b.md") in edges              # content link -> edge
    assert ("a.md", "nav.md") not in edges        # boilerplate link -> no edge


def test_edges_dedupe_and_skip_external_and_self():
    s = Settings(site="https://x.com")
    pm = {"https://x.com/a": "a.md", "https://x.com/b": "b.md"}
    md = "[b](/b) again [b](/b), [self](/a), and [ext](https://other.com)."
    concepts = _concepts(pm, [("https://x.com/a", md), ("https://x.com/b", "")])
    graph = build_graph(concepts, pm, s)
    edges = [(e["source"], e["target"]) for e in graph["edges"]]
    assert edges == [("a.md", "b.md")]  # deduped, no self-edge, no external


def test_render_viz_is_self_contained():
    s = Settings(site="https://x.com")
    pm = {"https://x.com/a": "a.md"}
    html = render_viz(_concepts(pm, [("https://x.com/a", "hi")]), pm, s)
    # No external resource loads of any kind.
    assert "<script src" not in html
    assert "<link " not in html
    assert "cdn." not in html
    assert 'src="http' not in html


def test_render_viz_escapes_closing_tags():
    s = Settings(site="https://x.com")
    pm = {"https://x.com/a": "a.md"}
    # A hostile title must not be able to close the inline <script>.
    concepts = _concepts(pm, [("https://x.com/a", "hi")])
    concepts[0].title = "</script><b>x"
    html = render_viz(concepts, pm, s)
    assert "</script><b>x" not in html
    assert "<\\/script>" in html
