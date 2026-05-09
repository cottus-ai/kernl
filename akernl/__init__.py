from collections.abc import Callable
from typing import Any, TypeVar

from akernl.bundle import inspect
from akernl.compile import Image, compile
from akernl.deploy import deploy
from akernl.pool import VMPool
from akernl.run import run

__version__ = "0.1.0"

_F = TypeVar("_F", bound=Callable[..., Any])


def agent(**kwargs: Any) -> Callable[[type], type]:
    def _wrap(cls: type) -> type:
        setattr(cls, "_akernl", kwargs)
        return cls

    return _wrap


def tool(fn: _F) -> _F:
    setattr(fn, "_akernl_tool", True)
    return fn


__all__ = ["compile", "run", "deploy", "inspect", "agent", "tool", "Image", "VMPool"]
