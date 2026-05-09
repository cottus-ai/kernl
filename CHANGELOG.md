# Changelog

## v0.1.0 — 2026-05-09

Initial open-source release.

- AST-based agent parser — `@agent` / `@tool` decorators, LangChain, LlamaIndex
- `.krn` bundle format — reproducible, content-addressed
- Firecracker microVM execution (falls back to subprocess without Firecracker)
- Firecracker VM pool with health scoring, recycling, and concurrent dispatch
- CLI: `akernl compile`, `akernl run`, `akernl deploy`, `akernl inspect`, `akernl exec`
- Python API: `from akernl import compile, run, deploy`
- `--dry-run` mode — no API key needed for testing
- LangChain adapter (AST + runtime)
- LlamaIndex adapter (AST + runtime, `FunctionTool.from_defaults` detection)
- 23 tests covering compile, run, pool, adapters, and error cases
