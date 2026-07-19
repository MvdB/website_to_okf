# Contributing

Thanks for your interest in improving **website-to-okf**. This is a small,
focused tool; contributions that keep it simple and dependency-light are very
welcome.

## Development setup

```bash
git clone https://github.com/MvdB/website_to_okf
cd website_to_okf
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Unix: source .venv/bin/activate
pip install -e ".[dev]"
# The default crawl4ai engine needs a browser once:
playwright install chromium
```

## Running the checks

CI runs the same two commands on every push and pull request:

```bash
ruff check website_to_okf tests   # lint
pytest -q                         # unit tests
```

The unit tests are pure and offline — no network, no browser, no LLM — so they
run in a second or two. Please keep them that way: anything that needs a live
site or model belongs behind a manual/integration marker, not in the default
suite.

## Guidelines

- **Add a test** for any behavior change to the pure logic (URL handling, OKF
  writing, buffering, distillation helpers, viz graph building).
- **Match the surrounding style** — the code favors small, readable functions
  and explains *why*, not *what*, in comments.
- **Keep the dependency footprint small.** New runtime dependencies should earn
  their place; prefer the standard library where practical.
- **Graceful degradation matters.** A single bad page, a flaky model, or a
  restarted server must never abort a whole run — mirror the existing
  fallback/buffering patterns.

## Submitting changes

1. Branch off `master`.
2. Make the change with a focused commit and a clear message.
3. Ensure `ruff` and `pytest` are green.
4. Open a pull request describing the *why* and any trade-offs.

By contributing, you agree that your contributions are licensed under the
project's [Apache-2.0 License](LICENSE).
