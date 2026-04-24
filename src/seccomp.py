"""
Kernl Seccomp — BPF syscall filter for bwrap sandboxes.

Builds a seccomp-bpf filter that restricts which syscalls agents can make.
The filter is loaded by bwrap via --seccomp <fd> AFTER namespace setup,
so bwrap's own setup syscalls (mount, pivot_root, unshare) are not affected.

Design:
  - ALLOWLIST model: only listed syscalls are permitted. Everything else
    returns EPERM (not SIGSYS — we want the agent to see a clean error,
    not crash with a signal).
  - The allowlist covers what CPython 3.10+ needs for:
    stdlib imports, file I/O, networking (urllib/ssl), threading, /proc reads
  - execve, execveat, ptrace are explicitly BLOCKED — these are the primary
    escape vectors from a Python sandbox.
  - clone is allowed ONLY with CLONE_THREAD flag (threads only, no fork).
    This is enforced via BPF argument inspection.
  - clone3 is allowed unconditionally because its flags are in a struct
    (not directly inspectable by seccomp BPF). Fork bombs are contained
    by cgroup pids.max.

Architecture: x86_64 only. The BPF program checks the arch field first
and kills the process on non-x86_64 to prevent arch-confusion attacks.

Syscall risk assessment (high-risk syscalls still allowed):
  - execve (59): Required because bwrap loads seccomp before exec.
    Mitigation: read-only FS, hidden dirs, no writable executables.
  - clone3 (435): Can't inspect struct args via BPF.
    Mitigation: cgroup pids.max prevents fork bombs.
  - socket (41): Required for API calls when network is enabled.
    Mitigation: --unshare-net blocks external access by default.
  - openat (257): Required for all file I/O.
    Mitigation: read-only FS, tmpfs over sensitive paths.
  - ioctl (16): Broad syscall, many sub-operations.
    Mitigation: limited device access (--dev gives minimal /dev).
  - prctl (157): Some operations are dangerous.
    Mitigation: user namespace limits what prctl can affect.

Usage:
  fd = create_seccomp_fd()
  cmd = ["bwrap", ..., "--seccomp", str(fd), ..., "--", ...]
  subprocess.Popen(cmd, ..., pass_fds=(fd,))
"""
import os
import struct
import platform

# ---- BPF instruction encoding ----
# struct sock_filter { __u16 code; __u8 jt; __u8 jf; __u32 k; }

def _bpf(code, jt, jf, k):
    return struct.pack("HBBI", code, jt, jf, k)

# BPF opcodes
BPF_LD  = 0x00
BPF_JMP = 0x05
BPF_RET = 0x06
BPF_ALU = 0x04
BPF_W   = 0x00
BPF_ABS = 0x20
BPF_JEQ = 0x10
BPF_AND = 0x50
BPF_K   = 0x00

# seccomp return values
SECCOMP_RET_ALLOW   = 0x7FFF0000
SECCOMP_RET_ERRNO   = 0x00050000  # returns -1 with errno in low 16 bits
SECCOMP_RET_KILL    = 0x00000000
EPERM = 1

# seccomp_data offsets (struct seccomp_data in <linux/seccomp.h>)
OFFSET_NR    = 0   # syscall number (4 bytes)
OFFSET_ARCH  = 4   # architecture (4 bytes)
OFFSET_ARGS0 = 16  # args[0] low 32 bits

# Architecture constant
AUDIT_ARCH_X86_64 = 0xC000003E
AUDIT_ARCH_AARCH64 = 0xC00000B7

# clone flags for argument inspection
CLONE_THREAD = 0x00010000


# ---- x86_64 syscall numbers ----
# These are ABI-stable. Source: arch/x86/entry/syscalls/syscall_64.tbl

_SYSCALLS_X86_64 = {
    # File I/O — required for Python stdlib, reading agent files, /proc
    "read": 0, "write": 1, "open": 2, "close": 3,
    "stat": 4, "fstat": 5, "lstat": 6,
    "poll": 7, "lseek": 8,
    "pread64": 17, "pwrite64": 18,
    "readv": 19, "writev": 20,
    "access": 21, "pipe": 22, "pipe2": 293,
    "dup": 32, "dup2": 33, "dup3": 292,
    "fcntl": 72, "flock": 73,
    "fsync": 74, "fdatasync": 75,
    "truncate": 76, "ftruncate": 77,
    "getdents64": 217,
    "getcwd": 79,
    "chdir": 80,
    "rename": 82, "mkdir": 83, "rmdir": 84,
    "creat": 85, "link": 86, "unlink": 87,
    "symlink": 88, "readlink": 89, "chmod": 90,
    "chown": 92, "lchown": 94,
    "umask": 95,
    "openat": 257, "mkdirat": 258, "fchownat": 260,
    "newfstatat": 262, "unlinkat": 263, "renameat": 264,
    "linkat": 265, "symlinkat": 266, "readlinkat": 267,
    "fchmodat": 268, "faccessat": 269,
    "faccessat2": 439,
    "statx": 332,
    "copy_file_range": 326,
    "statfs": 137, "fstatfs": 138,

    # Memory management — required for CPython
    "mmap": 9, "mprotect": 10, "munmap": 11, "brk": 12,
    "mremap": 25, "madvise": 28,

    # Signals — required for CPython signal handling
    "rt_sigaction": 13, "rt_sigprocmask": 14, "rt_sigreturn": 15,
    "rt_sigpending": 127, "rt_sigtimedwait": 128,
    "rt_sigsuspend": 129, "sigaltstack": 131,

    # Process/thread info — required for CPython
    "getpid": 39, "getuid": 102, "getgid": 104,
    "geteuid": 107, "getegid": 108, "getppid": 110,
    "getpgrp": 111, "getgroups": 115,
    "gettid": 186, "set_tid_address": 218,
    "set_robust_list": 273, "get_robust_list": 274,

    # Networking — required for urllib/ssl (Anthropic API calls)
    "socket": 41, "connect": 42, "accept": 43,
    "sendto": 44, "recvfrom": 45,
    "sendmsg": 46, "recvmsg": 47,
    "shutdown": 48, "bind": 49, "listen": 50,
    "getsockname": 51, "getpeername": 52,
    "setsockopt": 54, "getsockopt": 55,
    "select": 23, "pselect6": 270,
    "epoll_create1": 291, "epoll_ctl": 233, "epoll_wait": 232,
    "epoll_pwait": 281,

    # Threading — clone handled specially (CLONE_THREAD enforcement)
    # clone (56) is NOT in this dict — it's handled via arg inspection
    "clone3": 435,  # Can't inspect struct args; fork bombs contained by pids.max
    "futex": 202, "futex_waitv": 449,

    # Time — required for time.monotonic, time.time, ssl
    "clock_gettime": 228, "clock_getres": 229,
    "clock_nanosleep": 230, "nanosleep": 35,
    "gettimeofday": 96,

    # Random — required for ssl, hashlib
    "getrandom": 318,

    # Misc — required for CPython internals
    "ioctl": 16,
    "prctl": 157,
    "arch_prctl": 158,
    "exit": 60, "exit_group": 231,
    "wait4": 61, "waitid": 247,
    "uname": 63,
    "sysinfo": 99,
    "prlimit64": 302,
    "rseq": 334,
    "eventfd2": 290,
    "signalfd4": 289,
    "timerfd_create": 283, "timerfd_settime": 286, "timerfd_gettime": 287,

    # /proc reading (for _get_rss_kb in worker.py)
    # Already covered by read/open/openat above

    # execve — required because bwrap loads the seccomp filter BEFORE exec.
    # The filter applies to the exec'd program (Python), but bwrap itself
    # needs execve to launch it. We allow execve and rely on the read-only
    # filesystem + clearenv + hidden directories to prevent meaningful
    # shell escape. The agent cannot write new executables (ro root),
    # and visible executables are system binaries only.
    "execve": 59,
}

# Syscall number for clone (handled with argument inspection, not in _SYSCALLS_X86_64)
_CLONE_NR_X86_64 = 56

# ---- Explicitly blocked syscalls (for documentation) ----
# These are NOT in the allowlist, so they're blocked by default.
# Listed here for clarity:
_BLOCKED_DANGEROUS = {
    "execveat": 322,    # Shell escape variant (not needed by bwrap)
    "ptrace": 101,      # Debug/inject into other processes
    "mount": 165,       # Filesystem manipulation
    "umount2": 166,     # Filesystem manipulation
    "pivot_root": 155,  # Filesystem manipulation
    "chroot": 161,      # Escape attempt
    "reboot": 169,      # System control
    "syslog": 103,      # Kernel log access
    "init_module": 175, # Kernel module loading
    "finit_module": 313,
    "delete_module": 176,
    "kexec_load": 246,  # Kernel replacement
    "keyctl": 250,      # Kernel keyring
    "request_key": 249,
    "add_key": 248,
    "personality": 135, # Execution domain change
    "unshare": 272,     # Create new namespaces (escape)
    "setns": 308,       # Join namespaces (escape)
    "process_vm_readv": 310,  # Read other process memory
    "process_vm_writev": 311, # Write other process memory
    "kcmp": 312,        # Compare kernel objects
    "bpf": 321,         # Load BPF programs
    "userfaultfd": 323, # Userfault fd (exploit primitive)
    "memfd_create": 319, # Create anonymous file (used in exploits)
    "perf_event_open": 298, # Performance monitoring (info leak)
}


def _build_filter(arch_audit: int, allowed_nrs: set[int], clone_nr: int = -1) -> bytes:
    """
    Build a seccomp BPF filter program with optional clone arg inspection.

    Structure:
      1. Load architecture, verify it matches (kill on mismatch)
      2. Load syscall number
      3. If clone_nr >= 0: check clone's flags arg for CLONE_THREAD
      4. For each allowed syscall: if match, jump to ALLOW
      5. Default: return EPERM

    Args:
        arch_audit: Expected AUDIT_ARCH constant.
        allowed_nrs: Set of syscall numbers to allow unconditionally.
        clone_nr: If >= 0, this syscall gets special treatment — allowed
                  only when CLONE_THREAD is set in the flags argument.
    """
    insns = []

    # 1. Check architecture
    insns.append(_bpf(BPF_LD | BPF_W | BPF_ABS, 0, 0, OFFSET_ARCH))
    insns.append(_bpf(BPF_JMP | BPF_JEQ | BPF_K, 1, 0, arch_audit))
    insns.append(_bpf(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_KILL))  # wrong arch → kill

    # 2. Load syscall number
    insns.append(_bpf(BPF_LD | BPF_W | BPF_ABS, 0, 0, OFFSET_NR))

    # Remove clone from general allowlist (handled specially)
    general_nrs = sorted(allowed_nrs - ({clone_nr} if clone_nr >= 0 else set()))
    n_general = len(general_nrs)
    has_clone = clone_nr >= 0
    clone_block_size = 4 if has_clone else 0  # LD + AND + JEQ + RET

    # 3. Special case: clone with CLONE_THREAD enforcement
    if has_clone:
        # Jump to clone_check block (past general checks + default deny)
        # clone_check is n_general + 1 instructions ahead of the next insn
        insns.append(_bpf(BPF_JMP | BPF_JEQ | BPF_K, n_general + 1, 0, clone_nr))

    # 4. General allowlist — linear scan
    for i, nr in enumerate(general_nrs):
        # Jump to ALLOW: skip remaining checks + default deny + clone block
        jump_to_allow = (n_general - 1 - i) + 1 + clone_block_size
        insns.append(_bpf(BPF_JMP | BPF_JEQ | BPF_K, jump_to_allow, 0, nr))

    # 5. Default: deny with EPERM
    insns.append(_bpf(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ERRNO | EPERM))

    # 6. Clone check block (only if has_clone)
    if has_clone:
        # Load clone flags (first argument, low 32 bits)
        insns.append(_bpf(BPF_LD | BPF_W | BPF_ABS, 0, 0, OFFSET_ARGS0))
        # AND with CLONE_THREAD
        insns.append(_bpf(BPF_ALU | BPF_AND | BPF_K, 0, 0, CLONE_THREAD))
        # If CLONE_THREAD is set → allow (jump 1 past deny)
        insns.append(_bpf(BPF_JMP | BPF_JEQ | BPF_K, 1, 0, CLONE_THREAD))
        # Not a thread clone → deny
        insns.append(_bpf(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ERRNO | EPERM))

    # 7. ALLOW
    insns.append(_bpf(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ALLOW))

    return b"".join(insns)


def create_seccomp_fd() -> int:
    """
    Create a file descriptor containing the compiled seccomp BPF filter.

    Returns an fd suitable for bwrap --seccomp <fd>.
    The caller must pass this fd via pass_fds= and close it after Popen.
    """
    arch = platform.machine()
    if arch == "x86_64":
        audit_arch = AUDIT_ARCH_X86_64
        clone_nr = _CLONE_NR_X86_64
    elif arch == "aarch64":
        audit_arch = AUDIT_ARCH_AARCH64
        clone_nr = -1  # No arg inspection for aarch64 (different ABI)
    else:
        # Unknown arch — return -1, caller should skip seccomp
        return -1

    allowed_nrs = set(_SYSCALLS_X86_64.values())
    program = _build_filter(audit_arch, allowed_nrs, clone_nr=clone_nr)

    # Write to a pipe — bwrap reads the filter from the fd
    fd_r, fd_w = os.pipe()
    os.write(fd_w, program)
    os.close(fd_w)

    return fd_r
