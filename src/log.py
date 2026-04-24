"""
Kernl structured logger — JSON lines to stderr.

Every log entry is a single JSON object on one line, suitable for
piping to jq, shipping to a log aggregator, or grepping in a terminal.

Fields present in every entry:
  ts      — unix timestamp (float, millisecond precision)
  level   — info | warn | error
  event   — machine-readable event name (e.g. "agent_exec", "worker_death")

Additional fields are event-specific and always flat (no nested objects).

Configuration:
  KERNL_LOG=0 in env disables all logging.
  log.configure(stream=, enabled=) changes target at runtime.
"""
import json
import os
import sys
import threading
import time

_lock = threading.Lock()
_stream = sys.stderr
_enabled = os.environ.get("KERNL_LOG", "1") != "0"


def configure(stream=None, enabled=None):
    """Reconfigure the logger. Thread-safe."""
    global _stream, _enabled
    with _lock:
        if stream is not None:
            _stream = stream
        if enabled is not None:
            _enabled = enabled


def log(event: str, level: str = "info", **data):
    """
    Emit a structured log entry.

    Args:
        event: Machine-readable event name (e.g. "agent_exec").
        level: One of "info", "warn", "error".
        **data: Arbitrary key-value pairs. Values should be str/int/float/bool.
    """
    if not _enabled:
        return
    entry = {"ts": round(time.time(), 3), "level": level, "event": event}
    entry.update(data)
    line = json.dumps(entry, separators=(",", ":")) + "\n"
    with _lock:
        try:
            _stream.write(line)
            _stream.flush()
        except Exception:
            pass  # never crash the caller over a log write


def categorize_exit(returncode: int) -> str:
    """
    Categorize a process exit code into an error class.

    Returns one of: success, oom, seccomp, crash.
    """
    if returncode == 0:
        return "success"
    if returncode == -9:   # SIGKILL — cgroup OOM kill
        return "oom"
    if returncode == -31:  # SIGSYS — seccomp architecture kill
        return "seccomp"
    return "crash"
