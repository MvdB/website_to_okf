"""Stage buffering: discover + fetch checkpoints, and the fresh override."""

from datetime import datetime

from website_to_okf.buffer import StageBuffer
from website_to_okf.models import Extracted, UrlEntry


def test_discovered_round_trip(tmp_path):
    buf = StageBuffer(tmp_path)
    entries = [
        UrlEntry(url="https://x.com/a", source="sitemap"),
        UrlEntry(url="https://x.com/b", lastmod=datetime(2020, 1, 2, 3, 4, 5), source="crawl", depth=2),
    ]
    buf.save_discovered(entries)
    loaded = buf.load_discovered()
    assert [e.url for e in loaded] == ["https://x.com/a", "https://x.com/b"]
    assert loaded[1].lastmod == datetime(2020, 1, 2, 3, 4, 5)
    assert loaded[1].source == "crawl"
    assert loaded[1].depth == 2


def test_extracted_round_trip_and_has(tmp_path):
    buf = StageBuffer(tmp_path)
    ext = Extracted(
        url="https://x.com/a",
        markdown="hello",
        links=["https://x.com/b"],
        title="Title",
        description="desc",
        date="2020-01-01",
    )
    assert not buf.has_extracted("https://x.com/a")
    buf.save_extracted(ext)
    assert buf.has_extracted("https://x.com/a")

    all_ext = buf.load_all_extracted()
    assert set(all_ext) == {"https://x.com/a"}
    got = all_ext["https://x.com/a"]
    assert got.markdown == "hello"
    assert got.links == ["https://x.com/b"]
    assert got.title == "Title"


def test_fresh_ignores_existing_buffers(tmp_path):
    warm = StageBuffer(tmp_path)
    warm.save_discovered([UrlEntry(url="https://x.com/a", source="sitemap")])
    warm.save_extracted(Extracted(url="https://x.com/a", markdown="hi"))

    fresh = StageBuffer(tmp_path, fresh=True)
    assert fresh.load_discovered() is None
    assert fresh.has_extracted("https://x.com/a") is False
    assert fresh.load_all_extracted() == {}


def test_missing_buffers_return_defaults(tmp_path):
    buf = StageBuffer(tmp_path)
    assert buf.load_discovered() is None
    assert buf.load_all_extracted() == {}
    assert buf.has_extracted("https://x.com/whatever") is False
