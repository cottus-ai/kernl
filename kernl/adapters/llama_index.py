from __future__ import annotations

import ast
import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kernl.agent import AgentManifest, ToolDef


def parse(src: str, tree: ast.Module) -> "AgentManifest | None":
    from kernl.agent import AgentManifest, ToolDef, _doc, _params

    tool_fn_names = _find_tool_fn_names(tree)
    tools: list[ToolDef] = []
    lines = src.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue

        deco_names = [_name(d) for d in node.decorator_list]
        is_decorated = any("tool" in n.lower() for n in deco_names)
        is_referenced = node.name in tool_fn_names

        if not (is_decorated or is_referenced):
            continue

        params, req = _params(node)
        raw = "\n".join(lines[node.lineno - 1 : node.end_lineno])
        tools.append(ToolDef(
            name=node.name,
            description=_doc(node),
            parameters=params,
            required=req,
            source=textwrap.dedent(raw),
        ))

    if not tools:
        return None

    return AgentManifest(
        name="llamaindex_agent",
        model="claude-sonnet-4-20250514",
        system_prompt="",
        tools=tools,
        state_fields={},
        framework="llamaindex",
    )


def _find_tool_fn_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _name(node.func)
        if "FunctionTool" not in func_name and "Tool" not in func_name:
            continue
        for kw in node.keywords:
            if kw.arg == "fn" and isinstance(kw.value, ast.Name):
                names.add(kw.value.id)
        if node.args and isinstance(node.args[0], ast.Name):
            names.add(node.args[0].id)
    return names


def from_tools(tools: list) -> list["ToolDef"]:
    from kernl.agent import ToolDef
    import inspect

    out: list[ToolDef] = []
    for t in tools:
        fn = getattr(t, "fn", getattr(t, "_fn", None))
        meta = getattr(t, "metadata", None)
        name = (meta.name if meta else None) or (fn.__name__ if fn else str(t))
        desc = (meta.description if meta else "") or (fn.__doc__ or "")
        params, req = _from_sig(fn) if fn else ({}, [])
        src = inspect.getsource(fn) if fn else f"def {name}(): pass"
        out.append(ToolDef(name=name, description=desc.strip(), parameters=params, required=req, source=src))
    return out


def _from_sig(fn) -> tuple[dict, list]:
    import inspect
    tmap = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
    params, req = {}, []
    for n, p in inspect.signature(fn).parameters.items():
        t = tmap.get(getattr(p.annotation, "__name__", "str"), "string")
        params[n] = {"type": t}
        req.append(n)
    return params, req


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""
