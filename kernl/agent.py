from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]
    required: list[str]
    source: str


@dataclass
class AgentManifest:
    name: str
    model: str
    system_prompt: str
    tools: list[ToolDef]
    state_fields: dict[str, str]
    framework: str = "native"
    max_steps: int = 10
    allow_network: bool = False


def parse(path: str | Path) -> AgentManifest:
    src = Path(path).read_text()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _has_deco(node, "agent"):
            return _native_agent(node, src)

    from kernl.adapters import langchain, llama_index

    if "langchain" in src:
        m = langchain.parse(src, tree)
        if m:
            return m
    if "llama_index" in src:
        m = llama_index.parse(src, tree)
        if m:
            return m

    raise ValueError(f"No agent definition found in {path}")


def _native_agent(cls: ast.ClassDef, src: str) -> AgentManifest:
    kw = _deco_kwargs(cls, "agent")
    tools = [_tool(n, src) for n in cls.body if isinstance(n, ast.FunctionDef) and _has_deco(n, "tool")]
    state = {
        n.target.id: _type_str(n.annotation)
        for n in cls.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    return AgentManifest(
        name=kw.get("name", cls.name.lower()),
        model=kw.get("model", "claude-sonnet-4-20250514"),
        system_prompt=kw.get("system_prompt", ""),
        tools=tools,
        state_fields=state,
        framework="native",
        max_steps=int(kw.get("max_steps", 10)),
        allow_network=bool(kw.get("allow_network", False)),
    )


def _tool(fn: ast.FunctionDef, src: str) -> ToolDef:
    params, req = _params(fn)
    raw = "\n".join(src.splitlines()[fn.lineno - 1 : fn.end_lineno])
    return ToolDef(
        name=fn.name,
        description=_doc(fn),
        parameters=params,
        required=req,
        source=textwrap.dedent(raw),
    )


def _params(fn: ast.FunctionDef) -> tuple[dict[str, Any], list[str]]:
    tmap = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "list": "array", "dict": "object"}
    props, req = {}, []
    for arg in fn.args.args:
        if arg.arg in ("self", "cls"):
            continue
        t = tmap.get(_type_str(arg.annotation) if arg.annotation else "str", "string")
        props[arg.arg] = {"type": t}
        req.append(arg.arg)
    return props, req


def _doc(node: ast.AST) -> str:
    body = getattr(node, "body", [])
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        return body[0].value.value.strip()
    return ""


def _type_str(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _type_str(node.value)
    return "str"


def _has_deco(node: ast.AST, name: str) -> bool:
    for d in getattr(node, "decorator_list", []):
        n = d.func if isinstance(d, ast.Call) else d
        if (isinstance(n, ast.Name) and n.id == name) or (isinstance(n, ast.Attribute) and n.attr == name):
            return True
    return False


def _deco_kwargs(node: ast.ClassDef, name: str) -> dict[str, Any]:
    for d in node.decorator_list:
        if isinstance(d, ast.Call):
            n = d.func
            if (isinstance(n, ast.Name) and n.id == name) or (isinstance(n, ast.Attribute) and n.attr == name):
                return {kw.arg: kw.value.value for kw in d.keywords if isinstance(kw.value, ast.Constant)}
    return {}
