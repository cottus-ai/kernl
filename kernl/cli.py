from __future__ import annotations

import json
import os
import sys


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        return _help()

    cmd, rest = argv[0], argv[1:]
    fn = {
        "compile": _compile,
        "run": _run,
        "deploy": _deploy,
        "inspect": _inspect,
        "exec": _exec,
    }.get(cmd)

    if fn is None:
        _die(f"unknown command '{cmd}'. Run 'kernl --help'.")

    try:
        fn(rest)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def _flag(args: list[str], flag: str, default: str | None = None) -> str | None:
    if flag in args:
        i = args.index(flag)
        return args[i + 1] if i + 1 < len(args) else default
    return default


def _compile(args: list[str]) -> None:
    from kernl.compile import compile

    if not args or args[0].startswith("-"):
        _die("usage: kernl compile <agent.py> [-o <path>] [--adapter langchain|llama_index]")

    out = _flag(args, "-o") or _flag(args, "--output")
    img = compile(args[0], out)
    print(f"  {img.path}")
    print(f"  {img.size:,} bytes  [{img.image_type}]  {img.hash}")


def _run(args: list[str]) -> None:
    from kernl.run import run

    if len(args) < 2 or args[1].startswith("-"):
        _die("usage: kernl run <image.krn> '<json>' [--dry-run] [--mode process|firecracker|auto]")

    krn, inp = args[0], _parse_json(args[1])
    dry = "--dry-run" in args
    mode = _flag(args, "--mode") or "auto"

    r = run(krn, inp, dry_run=dry, mode=mode)
    print(f"  status   {r['status']}")
    print(f"  output   {r.get('output', '')[:300]}")
    print(f"  steps    {r.get('steps', 0)}  elapsed {r.get('elapsed_ms', 0):.0f}ms")
    for tc in r.get("tool_calls", []):
        print(f"  ↳ {tc['tool']}({json.dumps(tc['input'])}) → {str(tc['result'])[:80]}")


def _deploy(args: list[str]) -> None:
    from kernl.deploy import deploy

    if not args or args[0].startswith("-"):
        _die("usage: kernl deploy <image.krn> [--pool-size N] [--remote <url>]")

    remote = _flag(args, "--remote")
    size = int(_flag(args, "--pool-size") or 4)
    pool = deploy(args[0], remote=remote, pool_size=size)
    h = pool.health()
    print(f"  deployed  {args[0]}")
    print(f"  workers   {h['workers']['alive']}/{h['workers']['total']}  score={h['score']}  {h['status']}")


def _inspect(args: list[str]) -> None:
    from kernl.bundle import inspect

    if not args or args[0].startswith("-"):
        _die("usage: kernl inspect <image.krn>")

    info = inspect(args[0])
    print(f"  name        {info.get('name', 'unknown')}")
    print(f"  model       {info.get('model', 'unknown')}")
    print(f"  framework   {info.get('framework', 'native')}")
    print(f"  max_steps   {info.get('max_steps', 10)}")
    print(f"  image_type  {info.get('image_type', 'portable')}")
    print(f"  tools       {info.get('tools', [])}")
    print(f"  size        {info.get('size_bytes', 0):,} bytes")
    print(f"  hash        {info.get('hash', '')}")


def _exec(args: list[str]) -> None:
    import tempfile
    from kernl.compile import compile
    from kernl.run import run

    if len(args) < 2:
        _die("usage: kernl exec <agent.py> '<json>' [--dry-run]")

    with tempfile.NamedTemporaryFile(suffix=".krn", delete=False) as f:
        tmp = f.name
    try:
        compile(args[0], tmp)
        r = run(tmp, _parse_json(args[1]), dry_run="--dry-run" in args)
        print(f"  {r['status']}  {r.get('output', '')[:300]}  ({r.get('steps', 0)} steps)")
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _parse_json(s: str) -> dict:
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        _die(f"invalid JSON: {e}")


def _help() -> None:
    print("""kernl — Python AI agents → unikernel images → Firecracker microVMs

  kernl compile <agent.py> [-o out.krn]      compile to .krn image
  kernl run <image.krn> '<json>'             run (Firecracker or process fallback)
  kernl deploy <image.krn> [--pool-size N]   start Firecracker VM pool
  kernl inspect <image.krn>                  show image metadata
  kernl exec <agent.py> '<json>'             compile + run in one step

flags:
  --dry-run                     mock LLM calls, no API key needed
  --mode process|firecracker    force execution mode (default: auto)
  --pool-size N                 VMs in pool (default: 4)
  --remote <url>                deploy to unikernel.ai cloud (v0.2)
  --adapter langchain|llama_index  force adapter detection (default: auto)
""")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
