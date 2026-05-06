# Contributing

## Setup

```bash
git clone https://github.com/unikernel-ai/kernl
cd kernl
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Running tests

```bash
pytest tests/ -v
```

## Linting

```bash
ruff check kernl/
```

## Making changes

- Keep it minimal. If a function fits in 10 lines, don't write 30.
- No docstrings on obvious functions.
- No unnecessary comments.
- All tests must pass before opening a PR.
- Add tests for new behavior.

## Submitting a PR

1. Fork the repo and create a branch from `main`.
2. Make your changes.
3. Run `pytest` and `ruff check kernl/`.
4. Open a PR with a clear description of what and why.

## Style

- Python 3.11+, type hints throughout.
- `ruff` for linting — config is in `pyproject.toml`.
- Line length 100.
