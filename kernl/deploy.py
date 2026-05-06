from __future__ import annotations

from pathlib import Path

from kernl.pool import VMPool


def deploy(krn: str | Path, remote: str | None = None, pool_size: int = 4, **kwargs) -> VMPool:
    if remote:
        raise NotImplementedError("remote deployment coming in 0.2.0 — use unikernel.ai cloud")
    pool = VMPool(krn, size=pool_size, **kwargs)
    pool.start()
    return pool
