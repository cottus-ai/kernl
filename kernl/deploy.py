from pathlib import Path
from typing import Any

from kernl.pool import VMPool


def deploy(
    krn: str | Path,
    remote: str | None = None,
    pool_size: int = 4,
    **kwargs: Any,
) -> VMPool:
    if remote:
        raise NotImplementedError("remote deployment coming in 0.2.0 — use unikernel.ai cloud")
    pool = VMPool(krn, size=pool_size, **kwargs)
    pool.start()
    return pool
