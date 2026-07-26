# Contributing to Larz

Thanks for your interest! Larz is a **zero-dependency** money-native Python web
framework, and keeping it dependency-free is a core design constraint.

## Ground rules

- **No runtime dependencies.** Everything ships on the Python standard library.
  A PR that adds a third-party runtime dependency will not be merged. (Dev-only
  tools are fine, but the test suite itself uses no pytest — see below.)
- **Python 3.7+.** Code must import on 3.7 (use lazy imports for 3.8+ features).
- **Tests, no pytest.** The suite is plain `python3` files in `tests/` that run an
  in-process WSGI/ASGI client (`larz.testing.Client`). Add checks to the relevant
  `tests/test_*.py`; every file must exit 0.
- **Match the surrounding style.** Small, readable, standard-library idioms.

## Getting set up

```bash
git clone https://github.com/larz-scripter/larz
cd larz
python3 tests/test_core.py          # run any suite directly
for t in tests/test_*.py; do python3 "$t"; done
```

## Making a change

1. Open an issue first for anything non-trivial.
2. Keep PRs focused; add or update tests.
3. Run the full suite locally (all `tests/test_*.py` exit 0).
4. Update the docs/changelog if you change behavior.

## What we love

- Bug fixes with a failing-then-passing test.
- New payment providers (a ~40-line adapter: `create_checkout` + `parse_webhook`).
- Docs, examples, and real-world apps built on Larz.

## Releases

Tagged `vX.Y.Z` → CI publishes to PyPI via trusted publishing. Maintainers cut
releases; you don't need to.
