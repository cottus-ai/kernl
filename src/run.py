"""
Kernl Runner — execute a .kb bundle in an isolated environment.

Isolation levels (auto-detected, best available wins):

  Level 3 - firecracker : hardware-isolated microVM via KVM
  Level 2 - bwrap-full  : PID + IPC + UTS + mount namespace isolation
  Level 1 - bwrap-mount : mount namespace only (read-only root, hidden /home)
  Level 0 - process     : restricted env + resource limits (no namespace isolation)

Hardening (applied on top of bwrap namespace isolation):
  - seccomp: BPF syscall filter (blocks ptrace, mount, unshare, keyctl, etc.)
           clone restricted to CLONE_THREAD only (no fork)
  - network: --unshare-net by default (opt-in for agents that need API access)
  - uid/gid: --unshare-user with uid=65534 (nobody), defense-in-depth
  - cgroup: memory.max + cpu.max + pids.max via systemd-run (if available)
  - filesystem: sensitive paths hidden (/boot, /etc/ssh, /sys/firmware)

Each level is probed at startup. The runner picks the highest working level.
--no-sandbox forces Level 0 with no restrictions at all.
"""
import hashlib
import json
import os
import resource
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

from src.log import log, categorize_exit


# ---------------------------------------------------------------------------
# Bundle cache — extract .kb once, reuse on subsequent runs
# Merged from fast.py: eliminates ~1ms tar extraction per repeat run.
# ---------------------------------------------------------------------------

BUNDLE_CACHE_DIR = os.path.join(tempfile.gettempdir(), "kernl_bundles")


def get_cached_bundle(kb_path: str) -> tuple[str, dict]:
    """
    Return (bundle_dir, manifest) from cache, or extract and cache.

    Cache key is file basename + size + mtime. If the .kb file changes,
    a new cache entry is created automatically.
    """
    os.makedirs(BUNDLE_CACHE_DIR, exist_ok=True)
    stat = os.stat(kb_path)
    fast_key = f"{os.path.basename(kb_path)}_{stat.st_size}_{int(stat.st_mtime)}"
    cache_dir = os.path.join(BUNDLE_CACHE_DIR, fast_key)
    manifest_path = os.path.join(cache_dir, "manifest.json")

    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
        return cache_dir, manifest

    os.makedirs(cache_dir, exist_ok=True)
    with tarfile.open(kb_path, "r:gz") as tar:
        tar.extractall(cache_dir)
    with open(manifest_path) as f:
        manifest = json.load(f)
    return cache_dir, manifest


# ---------------------------------------------------------------------------
# Isolation probes — run once, cached for the process lifetime
# ---------------------------------------------------------------------------

_probe_cache: dict[str, bool] = {}


# Disk-cached probe — survives across process restarts.
# Merged from fast.py: saves ~13ms on cold start by avoiding bwrap probe.
PROBE_CACHE_DIR = os.path.join(tempfile.gettempdir(), "kernl_cache")
PROBE_CACHE_FILE = os.path.join(PROBE_CACHE_DIR, "probe_result.json")


def _bwrap_fingerprint() -> str:
    """Hash bwrap binary + kernel version to detect environment changes."""
    h = hashlib.md5()
    bwrap = shutil.which("bwrap")
    if bwrap:
        h.update(bwrap.encode())
        try:
            st = os.stat(bwrap)
            h.update(f"{st.st_size}:{st.st_mtime}".encode())
        except OSError:
            pass
    h.update(os.uname().release.encode())
    return h.hexdigest()[:12]


def detect_isolation_cached() -> str:
    """Return cached isolation level from disk, or probe and cache."""
    os.makedirs(PROBE_CACHE_DIR, exist_ok=True)
    fingerprint = _bwrap_fingerprint()
    try:
        with open(PROBE_CACHE_FILE) as f:
            cached = json.load(f)
        if cached.get("fingerprint") == fingerprint:
            return cached["level"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    _probe_cache.clear()
    level = detect_isolation_level()
    try:
        with open(PROBE_CACHE_FILE, "w") as f:
            json.dump({"fingerprint": fingerprint, "level": level,
                       "timestamp": time.time()}, f)
    except OSError:
        pass
    return level


def _probe(name: str, cmd: list[str], expect_stdout: str | None = None) -> bool:
    """Run a probe command, return True if it succeeds. Cached."""
    if name in _probe_cache:
        return _probe_cache[name]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        ok = proc.returncode == 0
        if ok and expect_stdout is not None:
            ok = expect_stdout in proc.stdout
        _probe_cache[name] = ok
        return ok
    except Exception:
        _probe_cache[name] = False
        return False


def _has_bwrap() -> bool:
    return shutil.which("bwrap") is not None


def _has_bwrap_mount() -> bool:
    """Can bwrap create a mount namespace? (needs userns on most kernels)."""
    if not _has_bwrap():
        return False
    return _probe("bwrap-mount", [
        "bwrap",
        "--ro-bind", "/", "/",
        "--tmpfs", "/tmp",
        "--", "/bin/echo", "ok",
    ], expect_stdout="ok")


def _has_bwrap_full() -> bool:
    """Can bwrap create PID + IPC + UTS + mount namespaces?"""
    if not _has_bwrap_mount():
        return False
    return _probe("bwrap-full", [
        "bwrap",
        "--ro-bind", "/", "/",
        "--tmpfs", "/tmp",
        "--proc", "/proc",
        "--dev", "/dev",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--hostname", "probe",
        "--", "/bin/echo", "ok",
    ], expect_stdout="ok")


def _has_firecracker() -> bool:
    """Is Firecracker installed and is /dev/kvm accessible?"""
    if not shutil.which("firecracker"):
        return False
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


def detect_isolation_level() -> str:
    """Return the best available isolation level name."""
    if _has_firecracker():
        return "firecracker"
    if _has_bwrap_full():
        return "bwrap-full"
    if _has_bwrap_mount():
        return "bwrap-mount"
    return "process"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _find_ssl_cert() -> str | None:
    for path in ["/etc/ssl/certs/ca-certificates.crt",
                 "/etc/pki/tls/certs/ca-bundle.crt",
                 "/etc/ssl/cert.pem"]:
        if os.path.exists(path):
            return path
    return None


def _env_vars(api_key: str, dry_run: bool = False) -> dict[str, str]:
    """The canonical set of env vars every isolation level passes."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
        "ANTHROPIC_API_KEY": api_key or "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    if dry_run or os.environ.get("KERNL_DRY_RUN") == "1":
        env["KERNL_DRY_RUN"] = "1"
    cert = _find_ssl_cert()
    if cert:
        env["SSL_CERT_FILE"] = cert
    return env


def _python_bin() -> str:
    return sys.executable


# ---------------------------------------------------------------------------
# Level 2: bwrap — full namespace isolation
# ---------------------------------------------------------------------------

def _build_bwrap_full_cmd(
    bundle_dir: str, input_json: str, api_key: str,
    memory_limit_bytes: int,
    disable_network: bool = True,
    seccomp_fd: int = -1,
) -> list[str]:
    """
    bwrap with PID + IPC + UTS + user + mount namespace isolation.

    The agent:
      - runs as nobody:nogroup (uid 65534) inside a user namespace
      - is PID 1 inside its PID namespace (can't signal host processes)
      - sees an isolated IPC namespace (no shared memory with host)
      - sees hostname "kernl" (UTS isolated)
      - has no network access by default (--unshare-net)
      - sees a read-only root filesystem with specific exceptions
      - cannot read /home, /root, /var, /run, or any user data
      - can only write to /tmp (tmpfs, RAM-backed)
      - has a separate /proc that only shows its own processes
      - is restricted to a seccomp syscall allowlist (if seccomp_fd >= 0)
    """
    py = _python_bin()
    env = _env_vars(api_key)

    cmd = ["bwrap"]

    # --- User namespace isolation ---
    # Run as nobody:nogroup inside the sandbox. The calling user's UID is
    # mapped to 65534 — the agent cannot access files restricted to the
    # host user's groups. Defense-in-depth: even if a bug allows writes
    # to the host filesystem, the agent runs as an unprivileged user.
    cmd += ["--unshare-user"]
    cmd += ["--uid", "65534"]       # nobody
    cmd += ["--gid", "65534"]       # nogroup

    # --- Namespace isolation ---
    cmd += ["--unshare-pid"]        # isolated PID namespace (agent is PID 1)
    cmd += ["--unshare-ipc"]        # isolated IPC (no shared memory)
    cmd += ["--unshare-uts"]        # isolated hostname
    cmd += ["--hostname", "kernl"]
    if disable_network:
        cmd += ["--unshare-net"]    # no network — only loopback interface

    # --- Filesystem ---
    # Start with the ENTIRE host filesystem as read-only.
    # This is the security foundation: nothing is writable unless explicitly
    # overridden below. Any path we forget to handle is still read-only.
    cmd += ["--ro-bind", "/", "/"]

    # Override specific paths with empty tmpfs to HIDE host data.
    # These become empty directories — host content is invisible.
    cmd += ["--tmpfs", "/home"]     # hide all user home directories
    cmd += ["--tmpfs", "/root"]     # hide root's home
    cmd += ["--tmpfs", "/var"]      # hide host logs, state, databases
    cmd += ["--tmpfs", "/boot"]     # hide kernel images, initramfs

    # Hide sensitive system config — defense-in-depth
    if os.path.isdir("/etc/ssh"):
        cmd += ["--tmpfs", "/etc/ssh"]      # hide SSH host keys
    if os.path.isdir("/sys/firmware"):
        cmd += ["--tmpfs", "/sys/firmware"]  # hide BIOS/ACPI/DMI data

    # /run: hide host runtime state, but preserve DNS resolver.
    # On systemd systems, /etc/resolv.conf symlinks to /run/systemd/resolve/stub-resolv.conf.
    # We must mount /run as tmpfs first, then bind the resolver file back in.
    resolv_target = os.path.realpath("/etc/resolv.conf")
    cmd += ["--tmpfs", "/run"]
    if not disable_network and resolv_target.startswith("/run/") and os.path.exists(resolv_target):
        # Only bind DNS resolver if network is enabled — pointless without it
        resolv_dir = os.path.dirname(resolv_target)
        cmd += ["--dir", resolv_dir]
        cmd += ["--ro-bind", resolv_target, resolv_target]

    # Replace /proc and /dev with isolated versions
    cmd += ["--proc", "/proc"]      # agent only sees its own PID
    cmd += ["--dev", "/dev"]        # minimal: null, zero, random, urandom only

    # /tmp is the ONLY writable path, and also hosts the agent mount point
    cmd += ["--tmpfs", "/tmp"]

    # Agent bundle — read-only inside /tmp (tmpfs is writable so mount point works)
    cmd += ["--ro-bind", bundle_dir, "/tmp/agent"]

    # --- Process control ---
    cmd += ["--die-with-parent"]    # kill agent if kernl process dies
    cmd += ["--new-session"]        # detach from controlling terminal

    # --- Seccomp syscall filter ---
    if seccomp_fd >= 0:
        cmd += ["--seccomp", str(seccomp_fd)]

    # --- Environment ---
    cmd += ["--clearenv"]
    for key, val in env.items():
        cmd += ["--setenv", key, val]

    # --- Working directory ---
    cmd += ["--chdir", "/tmp/agent"]

    # --- Entrypoint ---
    cmd += ["--"]
    cmd += [py, "/tmp/agent/runtime.py", "/tmp/agent/manifest.json", input_json]

    return cmd


# ---------------------------------------------------------------------------
# Level 1: bwrap — mount namespace only (no PID/IPC/UTS)
# ---------------------------------------------------------------------------

def _build_bwrap_mount_cmd(
    bundle_dir: str, input_json: str, api_key: str,
    memory_limit_bytes: int,
) -> list[str]:
    """
    bwrap with mount namespace isolation only.

    Used when the system supports bwrap but can't create PID/IPC/UTS namespaces
    (e.g., partial AppArmor restriction). Still provides:
      - read-only root filesystem
      - hidden /home, /root, /var, /run
      - writable /tmp on tmpfs
      - restricted environment variables
    """
    py = _python_bin()
    env = _env_vars(api_key)

    cmd = ["bwrap"]

    # Read-only bind of the entire root, then overlay sensitive paths
    cmd += ["--ro-bind", "/", "/"]
    cmd += ["--tmpfs", "/tmp"]
    cmd += ["--ro-bind", bundle_dir, "/tmp/agent"]
    cmd += ["--tmpfs", "/home"]
    cmd += ["--tmpfs", "/root"]
    cmd += ["--tmpfs", "/run"]
    cmd += ["--tmpfs", "/var"]
    cmd += ["--die-with-parent"]

    cmd += ["--clearenv"]
    for key, val in env.items():
        cmd += ["--setenv", key, val]

    cmd += ["--chdir", "/tmp/agent"]
    cmd += ["--"]
    cmd += [py, "/tmp/agent/runtime.py", "/tmp/agent/manifest.json", input_json]

    return cmd


# ---------------------------------------------------------------------------
# Level 0: process sandbox (always available)
# ---------------------------------------------------------------------------

def _build_process_cmd(
    bundle_dir: str, input_json: str
) -> list[str]:
    return [
        _python_bin(),
        os.path.join(bundle_dir, "runtime.py"),
        os.path.join(bundle_dir, "manifest.json"),
        input_json,
    ]


def _set_resource_limits(agent_memory_bytes: int = 1024 * 1024):
    """Set resource limits for the child process (called via preexec_fn).

    agent_memory_bytes is the agent's declared budget (from manifest).
    We add a fixed overhead for the Python runtime itself.
    """
    # CPython needs ~80MB base + agent budget
    PYTHON_OVERHEAD = 256 * 1024 * 1024  # 256MB for interpreter + stdlib
    total_as = PYTHON_OVERHEAD + agent_memory_bytes

    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_AS, (total_as, total_as))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))


# ---------------------------------------------------------------------------
# Level 3: Firecracker microVM (stub — design only)
# ---------------------------------------------------------------------------

def _run_firecracker(
    bundle_dir: str, input_json: str, api_key: str,
    memory_limit_bytes: int, timeout: int,
) -> subprocess.CompletedProcess:
    """
    Execute agent inside a Firecracker microVM.

    NOT YET IMPLEMENTED. This documents the exact execution path:

    1. Prepare a rootfs (ext4 image):
       - Base: minimal Alpine or scratch image with musl + Python
       - Overlay: agent bundle mounted at /agent
       - Size: ~25MB base + bundle size

    2. Configure the VM via Firecracker's REST API (Unix socket):
       PUT /boot-source      {"kernel_image_path": "vmlinux", "boot_args": "..."}
       PUT /drives/rootfs    {"path_on_host": "rootfs.ext4", "is_root_device": true}
       PUT /machine-config   {"vcpu_count": 1, "mem_size_mib": memory_limit_bytes // 1048576}
       PUT /network-interfaces/eth0  {"host_dev_name": "tap0", ...}

    3. Start the VM:
       PUT /actions           {"action_type": "InstanceStart"}

    4. The VM boots (~125ms), init runs:
       python3 /agent/runtime.py /agent/manifest.json '<input_json>'

    5. Agent output goes to serial console → captured by Firecracker

    6. VM exits → Firecracker process terminates → we parse stdout

    Prerequisites:
       - firecracker binary installed
       - /dev/kvm accessible
       - vmlinux kernel image (~5MB, built with minimal config)
       - rootfs.ext4 base image (built once, cached)
       - jailer (optional, for production use)

    To implement:
       - src/firecracker.py — VM lifecycle management
       - setup/build-rootfs.sh — builds the base ext4 image
       - setup/build-kernel.sh — builds minimal vmlinux
    """
    raise NotImplementedError(
        "Firecracker isolation is designed but not yet implemented. "
        "Run setup/install-sandbox.sh to enable bwrap namespace isolation."
    )


# ---------------------------------------------------------------------------
# Runner — the main entry point
# ---------------------------------------------------------------------------

def run(
    kb_path: str,
    input_data: dict,
    api_key: str | None = None,
    timeout: int = 120,
    use_sandbox: bool = True,
    dry_run: bool = False,
    allow_network: bool = False,
) -> dict:
    """
    Execute a .kb bundle and return the result.

    Network policy: network is DISABLED by default (--unshare-net).
    Set allow_network=True for agents that need to make API calls.
    When dry_run=True, network is never needed (mock LLM).
    """
    t_start = time.monotonic()

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not dry_run:
        return {"status": "error", "output": "ANTHROPIC_API_KEY not set"}

    # Network: disabled by default. Enabled only when the agent needs
    # real API access (not dry-run) and the caller explicitly allows it.
    needs_network = allow_network and not dry_run

    # Use cached bundle (avoids re-extracting tar on repeat runs)
    bundle_dir, manifest = get_cached_bundle(kb_path)

    agent_name = manifest["agent"]["name"]
    content_hash = manifest["content_hash"]
    memory_budget = manifest["memory_budget"]
    memory_limit = memory_budget["total_bytes"]
    input_json = json.dumps(input_data)

    seccomp_fd = -1
    pass_fds = ()

    # Select isolation level (disk-cached probe on first call)
    if not use_sandbox:
        cmd = _build_process_cmd(bundle_dir, input_json)
        env = os.environ.copy()
        preexec = None
        mode = "none"
    else:
        level = detect_isolation_cached()

        if level == "firecracker":
            try:
                proc = _run_firecracker(
                    bundle_dir, input_json, api_key, memory_limit, timeout
                )
            except NotImplementedError:
                level = "bwrap-full" if _has_bwrap_full() else \
                        "bwrap-mount" if _has_bwrap_mount() else "process"

        if level == "bwrap-full":
            # Create seccomp filter
            from src.seccomp import create_seccomp_fd
            seccomp_fd = create_seccomp_fd()
            if seccomp_fd >= 0:
                pass_fds = (seccomp_fd,)

            cmd = _build_bwrap_full_cmd(
                bundle_dir, input_json, api_key, memory_limit,
                disable_network=not needs_network,
                seccomp_fd=seccomp_fd,
            )
            env = None
            preexec = None
            mode = "bwrap-full"
        elif level == "bwrap-mount":
            cmd = _build_bwrap_mount_cmd(
                bundle_dir, input_json, api_key, memory_limit,
            )
            env = None
            preexec = None
            mode = "bwrap-mount"
        else:
            cmd = _build_process_cmd(bundle_dir, input_json)
            env = _make_process_env(api_key)
            preexec = lambda: _set_resource_limits(memory_limit)
            mode = "process"

    # Propagate dry_run to child process
    if dry_run:
        if env is not None:
            env["KERNL_DRY_RUN"] = "1"
        else:
            sep = cmd.index("--")
            cmd = cmd[:sep] + ["--setenv", "KERNL_DRY_RUN", "1"] + cmd[sep:]

    # cgroup v2 resource limits (memory cap, CPU throttle, fork bomb prevention)
    # Wraps the entire command (bwrap or bare python) in a systemd scope.
    # Falls back to rlimits if systemd-run is unavailable.
    from src.cgroup import cgroup_prefix
    PYTHON_OVERHEAD = 256 * 1024 * 1024  # 256MB for interpreter + stdlib
    cg_memory = PYTHON_OVERHEAD + memory_limit
    cg_prefix = cgroup_prefix(memory_bytes=cg_memory, cpu_percent=100, max_pids=32)
    if cg_prefix:
        cmd = cg_prefix + cmd

    # Execute
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            preexec_fn=preexec,
            cwd=bundle_dir if not mode.startswith("bwrap") else None,
            pass_fds=pass_fds,
        )
    except subprocess.TimeoutExpired:
        t_total = (time.monotonic() - t_start) * 1000
        log("agent_exec", level="warn", agent=agent_name, isolation=mode,
            status="timeout", total_ms=round(t_total, 1),
            timeout_s=timeout, dry_run=dry_run)
        return {
            "status": "timeout",
            "output": f"Agent timed out after {timeout}s",
            "agent": agent_name, "hash": content_hash,
            "isolation": mode, "total_ms": t_total,
        }
    finally:
        if seccomp_fd >= 0:
            os.close(seccomp_fd)

    t_total = (time.monotonic() - t_start) * 1000

    if proc.returncode != 0:
        cause = categorize_exit(proc.returncode)
        stderr_text = proc.stderr or f"Exit code {proc.returncode}"
        # Log isolation events at warn level
        if cause in ("oom", "seccomp"):
            log("isolation_kill", level="warn", agent=agent_name, isolation=mode,
                cause=cause, returncode=proc.returncode, total_ms=round(t_total, 1),
                stderr=stderr_text[:256])
        else:
            log("agent_exec", level="warn", agent=agent_name, isolation=mode,
                status="error", cause=cause, returncode=proc.returncode,
                total_ms=round(t_total, 1), dry_run=dry_run)
        return {
            "status": "error",
            "output": stderr_text,
            "cause": cause,
            "stdout": proc.stdout[:500] if proc.stdout else "",
            "agent": agent_name, "hash": content_hash,
            "isolation": mode, "total_ms": t_total,
        }

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log("agent_exec", level="warn", agent=agent_name, isolation=mode,
            status="error", cause="bad_output", total_ms=round(t_total, 1))
        return {
            "status": "error",
            "output": f"Bad agent output: {proc.stdout[:500]}",
            "stderr": proc.stderr[:500] if proc.stderr else "",
            "agent": agent_name, "hash": content_hash,
            "isolation": mode, "total_ms": t_total,
        }

    result["agent"] = agent_name
    result["hash"] = content_hash
    result["isolation"] = mode
    result["total_ms"] = t_total
    result["memory_budget_kb"] = memory_budget["total_bytes"] // 1024

    # Structured log for every execution
    agent_ms = result.get("elapsed_ms", 0)
    infra_ms = round(t_total - agent_ms, 1) if agent_ms else 0
    log("agent_exec", agent=agent_name, isolation=mode,
        status=result.get("status", "unknown"),
        total_ms=round(t_total, 1), agent_ms=round(agent_ms, 1),
        infra_ms=infra_ms, steps=result.get("steps", 0),
        tools=len(result.get("tool_calls", [])),
        memory_kb=memory_budget["total_bytes"] // 1024,
        dry_run=dry_run)

    return result


def _make_process_env(api_key: str) -> dict:
    """Environment for process-level sandbox (no bwrap)."""
    return _env_vars(api_key)
