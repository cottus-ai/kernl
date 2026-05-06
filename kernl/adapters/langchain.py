import ast
import textwrap
from typing import Any

from kernl.agent import AgentManifest, ToolDef, _doc, _params


def parse(src: str, tree: ast.Module) -> AgentManifest | None:
    tools: list[ToolDef] = []
    lines: list[str] = src.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = [_name(b) for b in node.bases]
            if any("BaseTool" in b or "StructuredTool" in b for b in bases):
                t = _from_class(node, src, lines)
                if t:
                    tools.append(t)

    if not tools:
        return None

    return AgentManifest(
        name="langchain_agent",
        model="claude-sonnet-4-20250514",
        system_prompt="",
        tools=tools,
        state_fields={},
        framework="langchain",
    )


def from_tools(tools: list) -> list[ToolDef]:
    import inspect

    out: list[ToolDef] = []
    for t in tools:
        name = getattr(t, "name", getattr(t, "__name__", str(t)))
        desc = getattr(t, "description", "")
        fn = getattr(t, "func", getattr(t, "_run", None))
        params, req = _from_signature(fn) if fn else ({}, [])
        src = inspect.getsource(fn) if fn else f"def {name}(): pass"
        out.append(ToolDef(name=name, description=desc, parameters=params, required=req, source=src))
    return out


def _from_class(node: ast.ClassDef, src: str, lines: list[str]) -> ToolDef | None:
    name = desc = None
    for item in node.body:
        if isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name) and isinstance(item.value, ast.Constant):
                    if t.id == "name":
                        name = str(item.value.value)
                    elif t.id == "description":
                        desc = str(item.value.value)

    run = next((i for i in node.body if isinstance(i, ast.FunctionDef) and i.name in ("_run", "run")), None)
    if not run:
        return None

    params, req = _params(run)
    raw = "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return ToolDef(
        name=str(name or node.name.lower()),
        description=str(desc or _doc(node)),
        parameters=params,
        required=req,
        source=textwrap.dedent(raw),
    )


def _from_signature(fn: Any) -> tuple[dict, list]:
    import inspect

    sig = inspect.signature(fn)
    tmap = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
    params, req = {}, []
    for name, p in sig.parameters.items():
        if name == "self":
            continue
        t = tmap.get(p.annotation.__name__ if p.annotation is not inspect.Parameter.empty else "str", "string")
        params[name] = {"type": t}
        req.append(name)
    return params, req


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""
