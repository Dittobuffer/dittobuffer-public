# Contributing

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/pip install -e .
```

## Before you push

```bash
pytest -q
ruff check src tests
python -m dittobuffer --no-autostart /doctor
```

## Rules

1. One logical change per pull request; keep the diff reviewable.
2. Gameplay changes need a test that drives the RPC transaction, not just the helper.
3. Never commit `logs/`, `data/*.sqlite3`, `apk/built/` or `apk/patched/`.
4. New modules go in the matching package (`core`, `gameplay`, `world`, `admin`, `patcher`, `cli`).
5. Do not add a dependency without updating `requirements.txt` and `pyproject.toml`.

## Commit style

`area: imperative summary` — for example `gameplay: cap raid shiny rate at 100%`.

## Asset contributions

Large binaries require Git LFS. Include the manifest entry and the SHA-256 in the same commit.
