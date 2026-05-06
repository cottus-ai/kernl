import ast
import textwrap
from dataclasses import dataclass
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

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _has_deco(node, "agent"):
            return _native_agent(node, src)

    from kernl.adapters import langchain, llama_index

    if _imports_root_module(tree, "langchain"):
        m = langchain.parse(src, tree)
        if m:
            return m
    if _imports_root_module(tree, "llama_index"):
        m = llama_index.parse(src, tree)
        if m:
            return m

    raise ValueError(f"No agent definition found in {path}")


def _imports_root_module(tree: ast.AST, root: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == root or alias.name.startswith(f"{root}."):
                    return True
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == root or node.module.startswith(f"{root}."):
                return True
    return False


def _native_agent(cls: ast.ClassDef, src: str) -> AgentManifest:
    kw = _deco_kwargs(cls, "agent")
    tools = [
        _tool(n, src) for n in cls.body if isinstance(n, ast.FunctionDef) and _has_deco(n, "tool")
    ]
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
    props: dict[str, Any] = {}
    req: list[str] = []

    args_only = [a for a in fn.args.args if a.arg not in ("self", "cls")]
    ndef = len(fn.args.defaults)
    first_def = len(args_only) - ndef if ndef else len(args_only)

    for i, arg in enumerate(args_only):
        props[arg.arg] = _param_schema(arg.annotation)
        if i < first_def:
            req.append(arg.arg)

    for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
        props[arg.arg] = _param_schema(arg.annotation)
        if default is None:
            req.append(arg.arg)

    return props, req


def _doc(node: ast.AST) -> str:
    body = getattr(node, "body", [])
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        return str(body[0].value.value).strip()
    return ""


def _unwrap_optional(node: ast.expr | None) -> ast.expr | None:
    if node is None:
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        if _is_none_expr(node.right):
            return node.left
        if _is_none_expr(node.left):
            return node.right
    if isinstance(node, ast.Subscript):
        base = node.value
        if isinstance(base, ast.Name) and base.id == "Optional":
            return _subscript_arg(node)
        if isinstance(base, ast.Attribute) and base.attr == "Optional":
            return _subscript_arg(node)
    return node


def _is_none_expr(n: ast.expr) -> bool:
    if isinstance(n, ast.Constant) and n.value is None:
        return True
    return isinstance(n, ast.Name) and n.id == "None"


def _subscript_arg(node: ast.Subscript) -> ast.expr | None:
    s = node.slice
    if isinstance(s, ast.Tuple) and s.elts:
        return s.elts[0]
    return s if isinstance(s, ast.expr) else None


def _param_schema(node: ast.expr | None) -> dict[str, Any]:
    inner = _unwrap_optional(node)
    if inner is None:
        return {"type": "string"}

    if isinstance(inner, ast.Subscript):
        base = inner.value
        bid = (
            base.id
            if isinstance(base, ast.Name)
            else base.attr
            if isinstance(base, ast.Attribute)
            else ""
        )
        if bid in ("List", "list"):
            arg = _subscript_arg(inner)
            return {"type": "array", "items": _param_schema(arg)}
        if bid in ("Dict", "dict"):
            args = _subscript_tuple_elts(inner.slice)
            if len(args) >= 2:
                return {"type": "object", "additionalProperties": _param_schema(args[1])}
            return {"type": "object"}

    tname = _type_str(inner)
    tmap = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "array": "array",
        "object": "object",
    }
    return {"type": tmap.get(tname, "string")}


def _subscript_tuple_elts(slice_node: ast.expr | None) -> list[ast.expr | None]:
    if isinstance(slice_node, ast.Tuple):
        return [e if isinstance(e, ast.expr) else None for e in slice_node.elts]
    if isinstance(slice_node, ast.expr):
        return [slice_node]
    return []


def _type_str(node: ast.expr | None) -> str:
    if node is None:
        return "str"
    u = _unwrap_optional(node)
    if u is None:
        return "str"
    if isinstance(u, ast.BinOp) and isinstance(u.op, ast.BitOr):
        return _type_str(u.left)
    if isinstance(u, ast.Name):
        if u.id in ("List", "list"):
            return "array"
        if u.id in ("Dict", "dict"):
            return "object"
        return u.id
    if isinstance(u, ast.Attribute):
        if u.attr in ("List", "list"):
            return "array"
        if u.attr in ("Dict", "dict"):
            return "object"
        return u.attr
    if isinstance(u, ast.Subscript):
        base = u.value
        bid = (
            base.id
            if isinstance(base, ast.Name)
            else base.attr
            if isinstance(base, ast.Attribute)
            else ""
        )
        if bid in ("List", "list"):
            return "array"
        if bid in ("Dict", "dict"):
            return "object"
        return _type_str(_subscript_arg(u))
    return "str"


def _has_deco(node: ast.AST, name: str) -> bool:
    for d in getattr(node, "decorator_list", []):
        n = d.func if isinstance(d, ast.Call) else d
        if (isinstance(n, ast.Name) and n.id == name) or (
            isinstance(n, ast.Attribute) and n.attr == name
        ):
            return True
    return False


def _deco_kwargs(node: ast.ClassDef, name: str) -> dict[str, Any]:
    for d in node.decorator_list:
        if isinstance(d, ast.Call):
            n = d.func
            if (isinstance(n, ast.Name) and n.id == name) or (
                isinstance(n, ast.Attribute) and n.attr == name
            ):
                return {
                    kw.arg: kw.value.value
                    for kw in d.keywords
                    if kw.arg is not None and isinstance(kw.value, ast.Constant)
                }
    return {}
