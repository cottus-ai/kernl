from __future__ import annotations

from pathlib import Path
from typing import Any

from akernl.pool import VMPool


def deploy(krn: str | Path, pool_size: int = 4, **kwargs: Any) -> VMPool:
    pool = VMPool(krn, size=pool_size, **kwargs)
    pool.start()
    return pool
