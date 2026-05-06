from __future__ import annotations

from typing import Any, Callable, TypeVar

from kernl.compile import compile, Image
from kernl.run import run
from kernl.deploy import deploy
from kernl.bundle import inspect
from kernl.pool import VMPool

__version__ = "0.1.0"

_F = TypeVar("_F", bound=Callable[..., Any])


def agent(**kwargs: Any) -> Callable[[type], type]:
    def _wrap(cls: type) -> type:
        cls._kernl = kwargs  # type: ignore[attr-defined]
        return cls
    return _wrap


def tool(fn: _F) -> _F:
    fn._kernl_tool = True  # type: ignore[attr-defined]
    return fn


__all__ = ["compile", "run", "deploy", "inspect", "agent", "tool", "Image", "VMPool"]
