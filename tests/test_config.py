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


def test_aliased_fields_settable_by_name():
    # populate_by_name=True: the aliased OpenAI fields accept their field name,
    # not only the OPENAI_* alias, so direct construction and model_copy work.
    s = Settings(openai_model="/hf_models/x", openai_base_url="http://h/v1")
    assert s.openai_model == "/hf_models/x"
    assert s.openai_base_url == "http://h/v1"

    s2 = Settings().model_copy(update={"openai_model": "/hf_models/y"})
    assert s2.openai_model == "/hf_models/y"
