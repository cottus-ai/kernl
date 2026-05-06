from kernl.compile import compile, Image
from kernl.run import run
from kernl.deploy import deploy
from kernl.bundle import inspect
from kernl.pool import VMPool

__version__ = "0.1.0"


def agent(**kwargs):
    def _wrap(cls):
        cls._kernl = kwargs
        return cls
    return _wrap


def tool(fn):
    fn._kernl_tool = True
    return fn


__all__ = ["compile", "run", "deploy", "inspect", "agent", "tool", "Image", "VMPool"]
