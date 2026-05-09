# Architecture

## Overview

```
Client (SDK / HTTP)
  ↓
REST API  (FastAPI, akernl serve)
  ↓
Sandbox Manager  —  auth, rate limits, TTL, routing
  ↓
Pool Manager  —  VM lifecycle, idle pool, health checks
  ↓  ↓  ↓
[VM 0]  [VM 1]  [Process]   ←  isolation backends
  ↓
Execution Engine  —  code runner, file I/O, dep install
  ↓
Result  →  back up the chain
```

## Security model

Three modes control what code inside a sandbox can do.

### `restricted` (default)

- Network: all outbound blocked (no socket calls)
- Filesystem: read-only except `/tmp` and `/workspace`
- Package install: blocked
- Shell commands: blocked except those explicitly allowlisted
- Use for: untrusted third-party agent code, user-submitted scripts

### `standard`

- Network: outbound TCP/UDP allowed; inbound blocked
- Filesystem: `/workspace` fully writable; host paths hidden
- Package install: allowed via pip / npm / go get
- Shell commands: allowed
- Use for: trusted agent code that needs internet access or package installs

### `full`

- Network: unrestricted
- Filesystem: unrestricted within the VM
- Package install: allowed
- Shell commands: allowed
- Use for: development, testing, fully-trusted workloads

The server default is set by `AKERNL_SECURITY_MODE`. Per-sandbox override requires `AKERNL_ALLOW_MODE_OVERRIDE=true`.

## Isolation levels

### `process`

Each sandbox is a subprocess inside a chroot jail with cgroup limits applied via systemd-run:

- CPU: configurable quota
- Memory: hard limit, swap disabled
- PID count: limited
- No new privileges via `prctl(PR_SET_NO_NEW_PRIVS)`

**When to use:** internal tooling, high-throughput workloads, trusted code. Lowest overhead (~5ms warm start).

### `microvm`

Each tenant gets a pool of Firecracker microVMs. Sandboxes are assigned to idle VMs from the pool. The VM is reset between sandboxes via snapshot/restore or reboot.

- Hardware isolation via KVM
- Separate kernel per VM
- No shared memory with host
- Pool keeps `AKERNL_POOL_MIN_IDLE` VMs warm per tenant

**When to use:** multi-tenant SaaS, untrusted user code, strong isolation requirement. ~50ms cold start, ~4ms warm (pool hit).

### `dedicated`

One Firecracker VM is created per execution and destroyed when the sandbox is deleted. No sharing, no pool.

**When to use:** compliance requirements, highest-value executions, forensic auditability.

## Pool model

```
Pool state per tenant:
  idle: [VM, VM, VM, VM]   ←  pre-warmed, ready
  active: [VM, VM]          ←  serving sandboxes

Pool config:
  AKERNL_POOL_MIN_IDLE  = 2   (keep at least 2 idle)
  AKERNL_POOL_MAX_SIZE  = 20  (never exceed 20 total)
  AKERNL_POOL_IDLE_TIMEOUT = 300s
```

**Creation flow:**
1. Request arrives for new sandbox
2. Pool dequeues an idle VM
3. Sandbox assigned to VM, execution runs
4. Sandbox deleted → VM reset → returned to idle pool
5. If idle pool drops below `POOL_MIN_IDLE` → background goroutine boots replacement

**Pool exhaustion:**
When all VMs are active and the pool is at `POOL_MAX_SIZE`, new sandbox requests return `503 pool_exhausted`. Clients should retry with exponential back-off.

**Shutdown flow:**
1. Server receives SIGTERM
2. New requests rejected with 503
3. Active sandboxes allowed to complete (up to 30s drain window)
4. All VMs stopped
5. Process exits

## Filesystem layout

```
$AKERNL_SANDBOX_DIR/
└── {tenant_id}/
    └── {sandbox_id}/
        └── workspace/        ←  writable, visible to code as /workspace
            ├── ...user files...
```

- No sandbox can access another sandbox's directory
- Host filesystem is not mounted into the sandbox
- `/tmp` is a per-sandbox tmpfs (not persisted)

## Request flow

1. Client sends `POST /api/v1/sandboxes` with `x-api-key`
2. Auth middleware validates key, resolves `tenant_id`
3. Rate limiter checks per-minute and per-day counters
4. Sandbox Manager creates sandbox record in SQLite, assigns `sandbox_id`
5. Pool Manager dequeues idle VM (or starts new one if pool is empty and below max)
6. Sandbox directory created at `$AKERNL_SANDBOX_DIR/{tenant_id}/{sandbox_id}/workspace`
7. If `initial_code` present: Execution Engine runs it inside the VM
8. Result collected (stdout, stderr, exit_code, execution_time_ms)
9. Response serialized and returned with rate-limit headers
10. Background: sandbox TTL timer started; VM stays assigned until sandbox deleted or TTL expires
