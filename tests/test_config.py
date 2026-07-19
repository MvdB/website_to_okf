"""Settings defaults and environment overrides."""

from website_to_okf.config import Settings


def test_defaults():
    s = Settings()
    assert s.engine == "crawl4ai"
    assert s.max_pages == 500
    assert s.use_llm is True
    assert s.fresh is False
    assert s.add_citations is True
    assert s.write_viz is True


def test_env_override(monkeypatch):
    monkeypatch.setenv("W2OKF_MAX_PAGES", "7")
    monkeypatch.setenv("W2OKF_ENGINE", "trafilatura")
    monkeypatch.setenv("OPENAI_MODEL", "my-model")
    s = Settings()
    assert s.max_pages == 7
    assert s.engine == "trafilatura"
    assert s.openai_model == "my-model"
