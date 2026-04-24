"""
Kernl Worker — long-lived agent executor running inside a bwrap sandbox.

This script is the entrypoint for preforked worker processes. It:
  1. Imports the runtime module once (CPython startup cost paid once)
  2. Snapshots interpreter state (sys.modules, os.environ, sys.path)
  3. Signals "ready" to the host
  4. Reads JSON commands from stdin, executes, resets state, writes results
  5. Repeats until "shutdown" or stdin EOF

State isolation between runs:
  - sys.modules restored (agent-imported modules removed)
  - os.environ restored (agent-set vars removed)
  - sys.path restored
  - /tmp cleaned (agent-written files removed)
  - Tool namespaces created fresh per run (by runtime.build_tool_executor)

Protocol (line-delimited JSON over stdin/stdout):
  Host → Worker:
    {"cmd": "run", "manifest": {...}, "input_data": {...}, "agent_source": "..."}
    {"cmd": "ping"}
    {"cmd": "shutdown"}
  Worker → Host:
    {"status": "complete", ..., "_request_count": N, "_rss_kb": N}
    {"status": "ok", "_request_count": N, "_rss_kb": N}
    (exits)

This file runs INSIDE the sandbox with zero external dependencies.
"""
import json
import os
import signal
import shutil
import sys
import time

# Pre-import runtime — this is the whole point of the worker model.
# Python startup + module import happens once, not per-request.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime

# ---------------------------------------------------------------------------
# State snapshot — taken AFTER all imports, BEFORE any agent work.
# Used to reset interpreter state between runs.
# ---------------------------------------------------------------------------

_INITIAL_MODULES = frozenset(sys.modules.keys())
_INITIAL_ENVIRON = dict(os.environ)
_INITIAL_PATH = list(sys.path)

_request_count = 0
_peak_rss_kb = 0


def _get_rss_kb() -> int:
    """Read current RSS from /proc/self/status (works inside bwrap with --proc)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _reset_state():
    """
    Reset Python interpreter state between agent runs.

    This is the correctness guarantee: no state leaks from agent A to agent B.
    The exec() namespace for tools is already fresh per run (runtime creates
    a new dict each time), but tools can pollute interpreter globals.
    """
    # 1. Remove modules loaded by agent tools during exec().
    #    Agent tools can do 'import X' which adds to sys.modules.
    #    We remove anything not present at startup.
    #    This does NOT break the runtime — its own imports are in _INITIAL_MODULES.
    #    Removed modules will be re-imported on next use (fast, from .pyc).
    added = [k for k in sys.modules if k not in _INITIAL_MODULES]
    for key in added:
        del sys.modules[key]

    # 2. Restore environment variables.
    #    Agent tools can do os.environ["X"] = "Y".
    os.environ.clear()
    os.environ.update(_INITIAL_ENVIRON)

    # 3. Restore sys.path.
    #    Agent tools can do sys.path.append(...).
    sys.path[:] = _INITIAL_PATH

    # 4. Clean /tmp (writable area) — remove anything agents may have written.
    #    /tmp/worker is our read-only bind mount (skip it).
    #    In the no-sandbox case, /tmp is shared — skip cleanup.
    try:
        for entry in os.listdir("/tmp"):
            if entry == "worker":
                continue
            path = os.path.join("/tmp", entry)
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
            except OSError:
                pass
    except OSError:
        pass


class _AgentTimeout(Exception):
    """Raised by SIGALRM when agent execution exceeds its deadline."""
    pass


def _timeout_handler(signum, frame):
    raise _AgentTimeout("agent execution timed out")


def handle_run(cmd: dict) -> dict:
    """
    Execute a single agent run, reset state, return result.

    If cmd includes a "timeout" field (seconds), SIGALRM enforces a hard
    deadline. This catches hung tool calls, infinite loops, and stuck I/O
    that subprocess-level timeout cannot reach (because the worker is
    long-lived and subprocess timeout only applies to single-shot runs).
    """
    global _request_count
    _request_count += 1
    t0 = time.monotonic()

    manifest = cmd["manifest"]
    input_data = cmd["input_data"]
    agent_source = cmd.get("agent_source", "")
    timeout = cmd.get("timeout", 0)  # 0 = no timeout

    # Per-request dry_run: set env var so runtime._is_dry_run() reads it live.
    # _reset_state() restores os.environ after each run, so this is safe.
    if cmd.get("dry_run", False):
        os.environ["KERNL_DRY_RUN"] = "1"
    else:
        os.environ.pop("KERNL_DRY_RUN", None)

    # Set hard timeout via SIGALRM (integer seconds).
    # SIGALRM interrupts most blocking syscalls with EINTR, causing Python
    # to process the signal and raise _AgentTimeout at the next bytecode.
    old_handler = None
    if timeout > 0:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout)

    try:
        result = runtime.run_agent(manifest, input_data, agent_source=agent_source)
    except _AgentTimeout:
        elapsed = (time.monotonic() - t0) * 1000
        result = {
            "status": "timeout",
            "output": f"Agent timed out after {timeout}s",
            "steps": 0, "tool_calls": [], "elapsed_ms": elapsed,
        }
    except Exception as e:
        result = {
            "status": "error", "output": str(e),
            "steps": 0, "tool_calls": [], "elapsed_ms": 0,
        }
    finally:
        if timeout > 0:
            signal.alarm(0)  # cancel pending alarm
            signal.signal(signal.SIGALRM, old_handler or signal.SIG_DFL)

    global _peak_rss_kb
    rss = _get_rss_kb()
    _peak_rss_kb = max(_peak_rss_kb, rss)

    result["_worker_ms"] = (time.monotonic() - t0) * 1000
    result["_request_count"] = _request_count
    result["_rss_kb"] = rss
    result["_peak_rss_kb"] = _peak_rss_kb

    # Reset state AFTER capturing the result, BEFORE next run
    _reset_state()

    return result


def main():
    global _peak_rss_kb
    # Signal ready — the host waits for this before dispatching work
    sys.stdout.write(json.dumps({
        "status": "ready",
        "pid": os.getpid(),
        "_rss_kb": _get_rss_kb(),
    }) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"status": "error", "output": "bad JSON"}) + "\n")
            sys.stdout.flush()
            continue

        action = cmd.get("cmd", "")

        if action == "shutdown":
            break
        elif action == "ping":
            rss = _get_rss_kb()
            _peak_rss_kb = max(_peak_rss_kb, rss)
            sys.stdout.write(json.dumps({
                "status": "ok",
                "_request_count": _request_count,
                "_rss_kb": rss,
                "_peak_rss_kb": _peak_rss_kb,
            }) + "\n")
            sys.stdout.flush()
        elif action == "run":
            result = handle_run(cmd)
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()
        else:
            sys.stdout.write(json.dumps({"status": "error", "output": f"unknown: {action}"}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
