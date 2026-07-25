# Releasing Larz to PyPI

Publishing is automated via GitHub Actions (`.github/workflows/publish.yml`)
using **PyPI Trusted Publishing** — no API tokens stored anywhere.

## One-time setup (you do this once, on the PyPI website)

1. Create the project owner account on <https://pypi.org> (if not already).
2. Go to **PyPI → Your projects → Publishing** (or "Add a pending publisher"
   before the first release) and add a **Trusted Publisher** with:
   - **Owner:** `larz-scripter`
   - **Repository:** `larz`
   - **Workflow name:** `publish.yml`
   - **Environment:** `pypi`
3. In the GitHub repo, create an **Environment** named `pypi`
   (Settings → Environments → New environment). No secrets needed.

That's it — no tokens to copy or rotate.

## Cutting a release

```bash
# bump the version in pyproject.toml and larz/__init__.py, commit, then:
git tag v0.2.1
git push origin v0.2.1
```

The tag push triggers the workflow: it builds the sdist + wheel, runs
`twine check`, and publishes to PyPI via OIDC. Within a minute:

```bash
pip install larz
```

## Manual fallback (if you'd rather use a token)

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine upload dist/*      # prompts for __token__ + a PyPI API token
```

## Version bump checklist

- [ ] `pyproject.toml` `version`
- [ ] `larz/__init__.py` `__version__`
- [ ] both test suites green (`python tests/test_core.py && python tests/test_features.py`)
- [ ] `git tag vX.Y.Z && git push origin vX.Y.Z`
