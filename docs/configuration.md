# Configuration

All configuration is via environment variables. No config file required.

## Quick start

```bash
export AKERNL_ROOT_KEY=my-secret-key
export AKERNL_PORT=8080
akernl serve
```

## Production

```bash
export AKERNL_ROOT_KEY=$(openssl rand -hex 32)
export AKERNL_AUTH_REQUIRED=true
export AKERNL_SECURITY_MODE=restricted
export AKERNL_ISOLATION_LEVEL=microvm
export AKERNL_POOL_MIN_IDLE=4
export AKERNL_POOL_MAX_SIZE=50
export AKERNL_SANDBOX_TTL=600
export AKERNL_FIRECRACKER_PATH=/usr/local/bin/firecracker
export AKERNL_KERNEL_PATH=/opt/akernl/vmlinux
export AKERNL_ROOTFS_PATH=/opt/akernl/rootfs.ext4
export AKERNL_DB_PATH=/var/lib/akernl/akernl.db
export AKERNL_LOG_LEVEL=info
akernl serve
```

---

## Reference

### Server

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `AKERNL_HOST` | `0.0.0.0` | string | Bind address |
| `AKERNL_PORT` | `8080` | int | Bind port |
| `AKERNL_LOG_LEVEL` | `info` | string | `debug` · `info` · `warn` · `error` |

### Auth

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `AKERNL_AUTH_REQUIRED` | `true` | bool | Require `x-api-key` for sandbox operations |
| `AKERNL_ROOT_KEY` | — | string | Initial root key for creating API keys. Auto-generated and printed on first start if not set |

### Security

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `AKERNL_SECURITY_MODE` | `restricted` | string | Default mode: `restricted` · `standard` · `full` |
| `AKERNL_ALLOW_MODE_OVERRIDE` | `false` | bool | Allow per-sandbox `security_mode` override in request body |

### Isolation & pool

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `AKERNL_ISOLATION_LEVEL` | `process` | string | `process` · `microvm` · `dedicated` |
| `AKERNL_POOL_MIN_IDLE` | `2` | int | Minimum idle VMs to keep warm per tenant |
| `AKERNL_POOL_MAX_SIZE` | `20` | int | Maximum concurrent VMs per tenant |
| `AKERNL_POOL_IDLE_TIMEOUT` | `300` | int | Seconds before an idle VM is destroyed |

### Execution limits

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `AKERNL_SANDBOX_TTL` | `900` | int | Maximum sandbox lifetime in seconds |
| `AKERNL_MAX_CODE_SIZE` | `65536` | int | Maximum code payload in bytes |
| `AKERNL_DEFAULT_TIMEOUT` | `5000` | int | Default execution timeout in ms |
| `AKERNL_MAX_TIMEOUT` | `30000` | int | Maximum execution timeout in ms |
| `AKERNL_MAX_FILE_SIZE` | `10485760` | int | Maximum file upload size in bytes (10MB) |
| `AKERNL_MAX_SANDBOXES` | `100` | int | Maximum concurrent sandboxes per tenant |

### Rate limiting

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `AKERNL_RATE_PER_MINUTE` | `500` | int | Requests allowed per minute per API key |
| `AKERNL_RATE_PER_DAY` | `10000` | int | Requests allowed per day per API key |

### Paths

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `AKERNL_DB_PATH` | `./akernl.db` | string | SQLite database path |
| `AKERNL_SANDBOX_DIR` | `./sandboxes` | string | Sandbox filesystem root |
| `AKERNL_CACHE_DIR` | `./cache` | string | Dependency cache directory |
| `AKERNL_FIRECRACKER_PATH` | `/usr/local/bin/firecracker` | string | Path to Firecracker binary |
| `AKERNL_KERNEL_PATH` | `./vmlinux` | string | Path to guest kernel image |
| `AKERNL_ROOTFS_PATH` | `./rootfs.ext4` | string | Path to rootfs image |
