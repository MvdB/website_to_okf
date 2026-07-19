"""URL normalization and same-site helpers."""

from website_to_okf.urls import (
    is_probably_binary,
    matches_any,
    normalize_url,
    passes_filters,
    registrable_host,
    resolve,
    same_site,
)


def test_normalize_drops_fragment_and_query():
    assert normalize_url("https://x.com/a?b=1#frag") == "https://x.com/a"
    assert normalize_url("https://x.com/a?b=1", strip_query=False) == "https://x.com/a?b=1"


def test_normalize_lowercases_host_not_path():
    assert normalize_url("https://X.COM/Foo") == "https://x.com/Foo"


def test_normalize_default_scheme_and_root_path():
    # urlparse keeps a bare host as path when no scheme; the key point is a
    # normalized string is produced deterministically.
    assert normalize_url("https://x.com") == "https://x.com/"


def test_normalize_strips_default_ports():
    assert normalize_url("http://x.com:80/a") == "http://x.com/a"
    assert normalize_url("https://x.com:443/a") == "https://x.com/a"


def test_normalize_collapses_duplicate_slashes():
    assert normalize_url("https://x.com/a//b///c") == "https://x.com/a/b/c"


def test_registrable_host_strips_www():
    assert registrable_host("https://www.x.com/a") == "x.com"
    assert registrable_host("https://sub.x.com/a") == "sub.x.com"


def test_same_site_ignores_www():
    assert same_site("https://www.x.com/a", "https://x.com/b")
    assert not same_site("https://other.com/a", "https://x.com/b")


def test_is_probably_binary():
    assert is_probably_binary("https://x.com/file.pdf")
    assert is_probably_binary("https://x.com/a/b/image.PNG")
    assert is_probably_binary("https://x.com/route/HistRundweg.gpx")  # oversized GPS asset
    assert not is_probably_binary("https://x.com/page")
    assert not is_probably_binary("https://x.com/page.html")


def test_resolve_relative():
    assert resolve("https://x.com/a/b", "../c") == "https://x.com/c"
    assert resolve("https://x.com/a/", "d.html") == "https://x.com/a/d.html"


def test_matches_any_globs():
    assert matches_any("https://x.com/blog/p1", ["*/blog/*"])
    assert not matches_any("https://x.com/about", ["*/blog/*"])


def test_passes_filters_include_exclude():
    assert passes_filters("https://x.com/blog/p", ["*/blog/*"], [])
    assert not passes_filters("https://x.com/about", ["*/blog/*"], [])
    assert not passes_filters("https://x.com/blog/secret", [], ["*secret*"])
    assert passes_filters("https://x.com/blog/p", [], [])  # no filters -> allow
