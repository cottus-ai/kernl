# akernl

OS SDK for AI agents. Compile a Python agent into an 8MB bootable image. Run it on a Firecracker microVM in under 50ms.

```
agent.py  →  akernl compile  →  agent.krn  →  akernl run  →  Firecracker microVM
```

## Why

Containers give AI agents everything Linux has — and that's the problem. A Python agent needs a file system, a network stack, a shell, and 300+ syscalls just to make one API call. Unikernels strip all of that away: your agent runs directly on the hypervisor with ~26 syscalls, no shell, no libc, no attack surface. Cold starts drop from seconds to milliseconds. Images go from gigabytes to megabytes.

## Install

```bash
# recommended
uv tool install akernl

# or
pip install akernl
```

Optional: install [OPS](https://ops.city) for actual unikernel compilation and [Firecracker](https://firecracker-microvm.github.io) for microVM execution. Without them, akernl falls back to subprocess isolation automatically — the same CLI, same API, no code changes needed.

```bash
# OPS (Nanos unikernel compiler)
curl https://ops.city/get.sh | sh

# Firecracker (Linux only)
curl -fsSL https://github.com/firecracker-microvm/firecracker/releases/download/v1.7.0/firecracker-v1.7.0-x86_64.tgz \
  | tar xz && sudo mv release-v1.7.0-x86_64/firecracker-v1.7.0-x86_64 /usr/local/bin/firecracker
```

## Quickstart

**1. Write an agent**

```python
# agent.py
from akernl import agent, tool

@agent(name="researcher", model="claude-sonnet-4-20250514", max_steps=5)
class ResearchAgent:
    query: str

    @tool
    def search(self, topic: str) -> str:
        """Search the knowledge base."""
        kb = {"firecracker": "VMM by AWS. <50ms cold start, minimal footprint."}
        return kb.get(topic.lower(), f"No results for: {topic}")

    @tool
    def summarize(self, text: str) -> str:
        """Summarize text to its key points."""
        return " ".join(text.split()[:30])
```

**2. Compile**

```bash
akernl compile agent.py
#   agent.krn
#   8,421,376 bytes  [unikernel]  a3f9b1c2d4e5f6a7
```

**3. Run**

```bash
akernl run agent.krn '{"query": "firecracker"}' --dry-run
#   status   complete
#   output   Result: VMM by AWS. <50ms cold start...
#   steps    2  elapsed 34ms
```

Drop `--dry-run` and set `ANTHROPIC_API_KEY` to run against the real API.

**4. Inspect**

```bash
akernl inspect agent.krn
#   name        researcher
#   model       claude-sonnet-4-20250514
#   framework   native
#   image_type  unikernel
#   tools       ['search', 'summarize']
#   size        8,421,376 bytes
```

## Supported formats

**Native decorators** (recommended)

```python
from akernl import agent, tool

@agent(name="my_agent", model="claude-sonnet-4-20250514", max_steps=5)
class MyAgent:
    input: str

    @tool
    def lookup(self, query: str) -> str:
        """Look up information."""
        return f"result: {query}"
```

**LangChain**

```python
from langchain.tools import BaseTool

class SearchTool(BaseTool):
    name = "search"
    description = "Search the knowledge base"

    def _run(self, query: str) -> str:
        return f"results: {query}"
```

```bash
akernl compile langchain_agent.py   # detected automatically
```

**LlamaIndex**

```python
from llama_index.core.tools import FunctionTool

def search(query: str) -> str:
    """Search documents."""
    return f"found: {query}"

tools = [FunctionTool.from_defaults(fn=search)]
```

```bash
akernl compile llama_agent.py   # detected automatically
```

## CLI reference

```
akernl compile <agent.py> [-o out.krn]
  Compile agent to a .krn unikernel image.
  Uses OPS/Nanos if available, otherwise produces a portable bundle.

akernl run <image.krn> '<json>' [--dry-run] [--mode process|firecracker|auto]
  Run an image. Firecracker if available, subprocess otherwise.
  --dry-run: mock LLM calls, no API key needed.

akernl deploy <image.krn> [--pool-size N] [--remote <url>]
  Start a Firecracker VM pool. Default pool size: 4.

akernl inspect <image.krn>
  Show image metadata: name, model, tools, image type, size.

akernl exec <agent.py> '<json>' [--dry-run]
  Compile and run in one step. Cleans up the .krn after.
```

## Python API

```python
from akernl import compile, run, deploy

img = compile("agent.py")
result = run(img.path, {"query": "unikernels"}, dry_run=True)
print(result["output"])

pool = deploy("agent.krn", pool_size=8)
result = pool.submit({"query": "Firecracker"}, dry_run=True)
pool.shutdown()
```

## Architecture

```
agent.py
  ↓  akernl/agent.py     AST parse — no code execution
  ↓  akernl/bundle.py    pack manifest + source + runtime → .krn
  ↓  akernl/compile.py   OPS → Nanos unikernel (or portable .krn fallback)
  ↓  akernl/run.py       Firecracker boot (or subprocess fallback)
  ↓  akernl/runtime.py   agent loop: LLM → tools → repeat  [stdlib only]
  ↓  Anthropic API
```

```
akernl/
├── agent.py        AST parser — @agent/@tool, LangChain, LlamaIndex
├── bundle.py       .krn format — pack/unpack/inspect
├── compile.py      agent.py → .krn
├── runtime.py      in-VM agent loop — stdlib only, zero deps
├── run.py          Firecracker boot + HTTP dispatch
├── pool.py         Firecracker VM pool
├── deploy.py       local pool or unikernel.ai cloud
├── cli.py          CLI entry point
└── adapters/
    ├── langchain.py
    └── llama_index.py
```

## Benchmarks

| Mode | Cold start | Warm (pool) | Image size |
|---|---|---|---|
| Firecracker (unikernel) | ~43ms | ~4ms | ~8MB |
| Subprocess (portable) | ~180ms | — | ~3KB |

*Single tool call, mock LLM, c6i.large.*

## Development

```bash
git clone https://github.com/cottus-ai/akernl && cd akernl
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).
