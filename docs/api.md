# API Reference

Base URL: `http://your-host:8080`

## Authentication

Sandbox operations require `x-api-key: <key>` header.
Key management operations require `Authorization: Bearer <jwt>` header.

## Error format

```json
{
  "error": "sandbox not found",
  "code": "sandbox_not_found",
  "request_id": "req_01jx..."
}
```

## Rate limit headers

Every response includes:

```
X-RateLimit-Limit: 500
X-RateLimit-Remaining: 497
X-RateLimit-Reset: 1746787200
```

## Error codes

| Code | HTTP | Meaning |
|------|------|---------|
| `invalid_payload` | 400 | Malformed or missing request fields |
| `invalid_path` | 400 | Path traversal (`../`) detected |
| `unsupported_language` | 422 | Language not available in this runtime |
| `disallowed_operation` | 403 | Blocked by security mode |
| `timeout_exceeded` | 408 | Execution exceeded `timeout_ms` |
| `code_too_large` | 413 | Payload exceeds `AKERNL_MAX_CODE_SIZE` |
| `sandbox_not_found` | 404 | No sandbox with that ID |
| `sandbox_expired` | 410 | Sandbox TTL elapsed |
| `unauthorized` | 401 | Missing or invalid API key |
| `forbidden` | 403 | Key lacks permission for this operation |
| `rate_limited` | 429 | Rate limit exceeded |
| `pool_exhausted` | 503 | No VMs available; retry after back-off |
| `internal_error` | 500 | Unexpected server error |

---

## Sandboxes

### POST /api/v1/sandboxes

Create a sandbox. Optionally execute initial code before returning.

**Auth:** `x-api-key`

**Request**

```json
{
  "initial_code": "print('hello')",
  "initial_language": "python",
  "initial_command": null,
  "initial_timeout_ms": 5000,
  "security_mode": "restricted",
  "metadata": {}
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `initial_code` | string | — | Code to run immediately after creation |
| `initial_language` | string | — | `python` · `nodejs` · `shell` · `go` |
| `initial_command` | string | — | Shell command to run instead of code |
| `initial_timeout_ms` | int | 5000 | Execution timeout (max 30000) |
| `security_mode` | string | server default | `restricted` · `standard` · `full` |
| `metadata` | object | `{}` | Arbitrary key-value pairs, returned as-is |

**Response — 201**

```json
{
  "id": "sbx_01jx4k...",
  "state": "active",
  "security_mode": "restricted",
  "isolation": "process",
  "created_at": "2026-05-09T12:00:00Z",
  "expires_at": "2026-05-09T12:15:00Z",
  "execution_count": 1,
  "metadata": {},
  "initial_result": {
    "status": "success",
    "stdout": "hello\n",
    "stderr": "",
    "exit_code": 0,
    "execution_time_ms": 38
  }
}
```

`initial_result` is `null` when no initial code was provided.

**Errors:** `invalid_payload` · `unsupported_language` · `pool_exhausted`

---

### GET /api/v1/sandboxes/:id

Get sandbox metadata.

**Auth:** `x-api-key`

**Response — 200**

```json
{
  "id": "sbx_01jx4k...",
  "state": "active",
  "security_mode": "restricted",
  "isolation": "process",
  "created_at": "2026-05-09T12:00:00Z",
  "expires_at": "2026-05-09T12:15:00Z",
  "execution_count": 4,
  "metadata": {}
}
```

**Errors:** `sandbox_not_found` · `sandbox_expired`

---

### DELETE /api/v1/sandboxes/:id

Destroy a sandbox immediately.

**Auth:** `x-api-key`

**Response — 200**

```json
{
  "id": "sbx_01jx4k...",
  "state": "deleted"
}
```

**Errors:** `sandbox_not_found`

---

### POST /api/v1/sandboxes/:id/execute

Execute code inside an existing sandbox. State (variables, installed packages, files) is retained from previous executions.

**Auth:** `x-api-key`

**Request**

```json
{
  "language": "python",
  "code": "print(x + 1)",
  "timeout_ms": 5000
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `language` | string | yes | `python` · `nodejs` · `shell` · `go` |
| `code` | string | yes | Code to execute |
| `timeout_ms` | int | no | Override default timeout |

**Response — 200**

```json
{
  "status": "success",
  "stdout": "43\n",
  "stderr": "",
  "exit_code": 0,
  "execution_time_ms": 12
}
```

`status` values: `success` · `error` · `timeout` · `disallowed_operation`

**Errors:** `sandbox_not_found` · `sandbox_expired` · `unsupported_language` · `timeout_exceeded` · `disallowed_operation` · `code_too_large`

---

### POST /api/v1/sandboxes/:id/command

Run a shell command inside the sandbox.

**Auth:** `x-api-key`

**Request**

```json
{
  "command": "ls /workspace",
  "timeout_ms": 5000
}
```

**Response — 200** — same shape as `/execute`

**Errors:** `sandbox_not_found` · `sandbox_expired` · `timeout_exceeded` · `disallowed_operation`

---

## Files

### PUT /api/v1/sandboxes/:id/files

Upload a file into the sandbox filesystem.

**Auth:** `x-api-key`

**JSON request**

```json
{
  "path": "/workspace/data.csv",
  "content_base64": "YSxiCjEsMgo="
}
```

**Multipart request** — fields: `file` (binary) + `path` (string)

Path traversal (`../`) returns `invalid_path` (400).

**Response — 200**

```json
{
  "path": "/workspace/data.csv",
  "size": 12
}
```

**Errors:** `sandbox_not_found` · `invalid_path` · `invalid_payload`

---

### GET /api/v1/sandboxes/:id/files?path=...

Download a file from the sandbox.

**Auth:** `x-api-key`

**Response — 200**

```json
{
  "path": "/workspace/data.csv",
  "content_base64": "YSxiCjEsMgo=",
  "size": 12
}
```

**Errors:** `sandbox_not_found` · `invalid_path` · `sandbox_not_found` (file missing)

---

### GET /api/v1/sandboxes/:id/files/list?path=...

List files at a path inside the sandbox.

**Auth:** `x-api-key`

**Response — 200**

```json
{
  "path": "/workspace",
  "files": [
    {"name": "data.csv", "type": "file", "size": 12},
    {"name": "outputs", "type": "directory", "size": 0}
  ]
}
```

**Errors:** `sandbox_not_found` · `invalid_path`

---

### DELETE /api/v1/sandboxes/:id/files?path=...

Delete a file or directory inside the sandbox.

**Auth:** `x-api-key`

**Response — 200**

```json
{
  "path": "/workspace/data.csv",
  "deleted": true
}
```

**Errors:** `sandbox_not_found` · `invalid_path`

---

## Dependencies

### POST /api/v1/sandboxes/:id/deps/install

Install packages inside the sandbox. Blocked in `restricted` mode.

**Auth:** `x-api-key`

**Request**

```json
{
  "language": "python",
  "packages": [
    {"name": "pandas"},
    {"name": "numpy", "version": "1.26.0"}
  ]
}
```

**Response — 200**

```json
{
  "installed": [
    {"name": "pandas", "version": "2.2.1"},
    {"name": "numpy", "version": "1.26.0"}
  ],
  "failed": []
}
```

When some packages fail:

```json
{
  "installed": [{"name": "pandas", "version": "2.2.1"}],
  "failed": [{"name": "nonexistent-pkg", "error": "package not found"}]
}
```

**Errors:** `sandbox_not_found` · `disallowed_operation` (restricted mode) · `unsupported_language`

---

### GET /api/v1/sandboxes/:id/deps?language=...

List installed packages.

**Auth:** `x-api-key`

**Response — 200**

```json
{
  "language": "python",
  "packages": [
    {"name": "pip", "version": "24.0"},
    {"name": "pandas", "version": "2.2.1"}
  ]
}
```

**Errors:** `sandbox_not_found` · `unsupported_language`

---

## API Keys

### POST /api/v1/keys

Create an API key. The full key is returned **once** — it cannot be retrieved again.

**Auth:** `Authorization: Bearer <jwt>`

**Request**

```json
{"label": "production-agent"}
```

**Response — 201**

```json
{
  "id": "key_01jx...",
  "key": "sk-akernl-...",
  "prefix": "sk-akernl-abc1",
  "label": "production-agent",
  "tenant_id": "ten_01jx...",
  "created_at": "2026-05-09T12:00:00Z"
}
```

**Errors:** `invalid_payload` · `unauthorized` · `forbidden`

---

### GET /api/v1/keys

List API keys. Keys are masked; only the prefix is shown.

**Auth:** `Authorization: Bearer <jwt>`

**Response — 200**

```json
{
  "keys": [
    {
      "id": "key_01jx...",
      "prefix": "sk-akernl-abc1",
      "label": "production-agent",
      "created_at": "2026-05-09T12:00:00Z",
      "last_used_at": "2026-05-09T12:04:12Z"
    }
  ]
}
```

**Errors:** `unauthorized` · `forbidden`

---

### DELETE /api/v1/keys/:id

Revoke an API key. Immediately invalid.

**Auth:** `Authorization: Bearer <jwt>`

**Response — 200**

```json
{"id": "key_01jx...", "revoked": true}
```

**Errors:** `unauthorized` · `forbidden` · `sandbox_not_found`

---

## Health

### GET /healthz

Liveness probe. No auth.

**Response — 200**

```json
{"status": "ok"}
```

---

### GET /readyz

Readiness probe. No auth.

**Response — 200**

```json
{
  "status": "ready",
  "pool": {"idle": 4, "active": 2, "max": 20},
  "db": "ok"
}
```

Returns 503 when the pool has no idle VMs or the database is unreachable.

---

### GET /api/v1/capabilities

Server capabilities. No auth.

**Response — 200**

```json
{
  "version": "0.1.0",
  "runtimes": ["python", "nodejs", "shell", "go"],
  "security_modes": ["restricted", "standard", "full"],
  "isolation_levels": ["process", "microvm", "dedicated"],
  "limits": {
    "max_code_size": 65536,
    "max_file_size": 10485760,
    "max_timeout_ms": 30000,
    "max_sandboxes": 100
  },
  "rate_limits": {
    "per_minute": 500,
    "per_day": 10000
  }
}
```
