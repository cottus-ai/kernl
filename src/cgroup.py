"""
Kernl cgroup v2 — resource limits for sandboxed processes.

Uses systemd-run --user --scope to create transient cgroup v2 scopes:
  - memory.max: hard memory cap (OOM-killed on breach)
  - cpu.max: CPU bandwidth limit (throttled, not killed)
  - pids.max: process/thread count limit (prevents fork bombs)

Why systemd-run:
  Direct cgroup manipulation requires write access to /sys/fs/cgroup,
  which is typically not delegated to unprivileged users. systemd-run
  creates transient scope units via the user dbus session, which IS
  permitted on systemd systems. The scope is automatically cleaned up
  when the process exits.

Fallback:
  If systemd-run is unavailable (no systemd, container, etc.), callers
  should fall back to rlimits — weaker (no hard memory cap, no pids limit)
  but always available.

Scope lifecycle:
  - Created by systemd-run at process launch
  - Lives as long as the wrapped process (bwrap/python)
  - Cleaned up by systemd when process exits (transient unit)
  - No manual cleanup required
"""
import shutil
import subprocess

_cgroup_available: bool | None = None


def _probe_cgroup() -> bool:
    """Check if systemd-run --user --scope works with resource properties."""
    global _cgroup_available
    if _cgroup_available is not None:
        return _cgroup_available

    if not shutil.which("systemd-run"):
        _cgroup_available = False
        return False

    try:
        r = subprocess.run(
            ["systemd-run", "--user", "--scope", "--quiet",
             "-p", "MemoryMax=512M", "-p", "MemorySwapMax=0",
             "-p", "TasksMax=16",
             "--", "/bin/true"],
            capture_output=True, timeout=5,
        )
        _cgroup_available = r.returncode == 0
    except Exception:
        _cgroup_available = False

    return _cgroup_available


def has_cgroup() -> bool:
    """Return True if cgroup v2 management is available."""
    return _probe_cgroup()


def cgroup_prefix(
    memory_bytes: int,
    cpu_percent: int = 100,
    max_pids: int = 32,
) -> list[str]:
    """
    Return command prefix to wrap a process in a cgroup v2 scope.

    Returns empty list if cgroup v2 is not available.

    Args:
        memory_bytes: Hard memory ceiling. Process is OOM-killed if exceeded.
        cpu_percent: CPU bandwidth as percent of one core. 100 = one full core.
        max_pids: Maximum PIDs/TIDs in the scope. Prevents fork bombs.
                  Must account for bwrap + python + agent threads (~32 is safe).
    """
    if not _probe_cgroup():
        return []

    return [
        "systemd-run", "--user", "--scope", "--quiet",
        "-p", f"MemoryMax={memory_bytes}",
        "-p", "MemorySwapMax=0",        # no swap — enforce hard memory ceiling
        "-p", f"CPUQuota={cpu_percent}%",
        "-p", f"TasksMax={max_pids}",
        "--",
    ]
