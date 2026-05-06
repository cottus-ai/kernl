from collections.abc import Callable
from typing import Any, TypeVar

from kernl.bundle import inspect
from kernl.compile import Image, compile
from kernl.deploy import deploy
from kernl.pool import VMPool
from kernl.run import run

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
