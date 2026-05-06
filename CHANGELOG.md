# Changelog

## v0.1.1 — 2026-05-06

### Security

- Hardened `.krn` extraction against path traversal (`tar.extractall` with `filter='data'` on Python 3.12+, safe member filtering on 3.11).
- Tool executor build failures are logged to stderr instead of failing silently.
- Optional HTTP auth for `/run` when `KERNL_TOKEN` is set (`Authorization: Bearer …`); `/health` remains unauthenticated.

### Bug fixes

- Unikernel compile path: only swallow `FileNotFoundError` / `OSError` from OPS; other exceptions log a stderr warning before portable fallback.
- Framework detection uses import AST (`import` / `from … import`) instead of substring search.
- `inspect()` opens the archive once instead of multiple full unpacks.
- VM pool `_replace()` serializes worker list updates with a lock.
- TAP setup uses `subprocess.run(..., check=True)` so `ip` failures surface; deterministic guest MAC from `hashlib.md5` of the VM id.

### Features

- Runtime LLM: Anthropic when `ANTHROPIC_API_KEY` is set (preferred if both are set), OpenAI Chat Completions when only `OPENAI_API_KEY` is set, mock mode with a clear stderr notice when neither is set.
- Agent schema: parameters with defaults are not listed as `required`; richer typing for `Optional`, `List`, and `Dict` in JSON-schema-like tool metadata.

### Cleanup

- Removed broken `bench/` scripts that imported non-existent `src.*` modules.

## v0.1.0 — 2025-05-05

Initial open-source release.

- AST-based agent parser — `@agent` / `@tool` decorators, LangChain, LlamaIndex
- `.krn` bundle format — reproducible, content-addressed
- Unikernel compilation via OPS/Nanos (falls back to portable mode without OPS)
- Firecracker microVM execution (falls back to subprocess without Firecracker)
- Firecracker VM pool with health scoring, recycling, and concurrent dispatch
- CLI: `kernl compile`, `kernl run`, `kernl deploy`, `kernl inspect`, `kernl exec`
- Python API: `from kernl import compile, run, deploy`
- `--dry-run` mode — no Anthropic API key needed for testing
- LangChain adapter (AST + runtime)
- LlamaIndex adapter (AST + runtime, `FunctionTool.from_defaults` detection)
- 20 tests covering compile, run, pool, adapters, and error cases
