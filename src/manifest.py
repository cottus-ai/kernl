"""
Parse a .agent.py file and extract the agent manifest using AST analysis.

No code execution — we read the structure statically.
"""
import ast
import json
import sys
from dataclasses import dataclass, field, asdict


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema for parameters
    source: str       # raw Python source of the tool method


@dataclass
class AgentManifest:
    name: str
    model: str
    max_steps: int
    system_prompt: str
    state_fields: dict[str, str]  # field_name -> type annotation as string
    tools: list[ToolDef]
    agent_class_name: str
    source_file: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def from_json(data: str) -> "AgentManifest":
        d = json.loads(data)
        d["tools"] = [ToolDef(**t) for t in d["tools"]]
        return AgentManifest(**d)


def _extract_type_str(node: ast.expr) -> str:
    """Convert an AST annotation node to a string."""
    return ast.unparse(node)


def _extract_docstring(node: ast.FunctionDef) -> str:
    """Extract docstring from a function definition."""
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        return node.body[0].value.value.strip()
    return ""


def _extract_tool_params(node: ast.FunctionDef) -> dict:
    """Build a JSON Schema for tool parameters from the function signature."""
    properties = {}
    required = []
    for arg in node.args.args:
        if arg.arg == "self":
            continue
        type_str = _extract_type_str(arg.annotation) if arg.annotation else "str"
        json_type = "string"
        if type_str in ("int", "float"):
            json_type = "number"
        elif type_str == "bool":
            json_type = "boolean"
        properties[arg.arg] = {"type": json_type, "description": ""}
        required.append(arg.arg)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def parse_agent_file(path: str) -> AgentManifest:
    """Parse a .agent.py file and return a structured manifest."""
    with open(path) as f:
        source = f.read()

    tree = ast.parse(source, filename=path)

    # Find the @agent(...) decorated class
    agent_class = None
    agent_kwargs = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                func = decorator.func
                if isinstance(func, ast.Name) and func.id == "agent":
                    agent_class = node
                    for kw in decorator.keywords:
                        val = ast.literal_eval(kw.value)
                        agent_kwargs[kw.arg] = val
                    break
        if agent_class:
            break

    if agent_class is None:
        print("ERROR: no @agent decorated class found", file=sys.stderr)
        sys.exit(1)

    # Extract state fields (annotated class attributes without @tool)
    state_fields = {}
    for item in agent_class.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            state_fields[item.target.id] = _extract_type_str(item.annotation)

    # Extract @tool methods
    tools = []
    for item in agent_class.body:
        if not isinstance(item, ast.FunctionDef):
            continue
        is_tool = False
        for dec in item.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "tool":
                is_tool = True
        if not is_tool:
            continue

        tools.append(ToolDef(
            name=item.name,
            description=_extract_docstring(item),
            parameters=_extract_tool_params(item),
            source=ast.get_source_segment(source, item),
        ))

    return AgentManifest(
        name=agent_kwargs.get("name", agent_class.name.lower()),
        model=agent_kwargs.get("model", "claude-sonnet-4-20250514"),
        max_steps=agent_kwargs.get("max_steps", 10),
        system_prompt=agent_kwargs.get("system_prompt", "You are a helpful assistant."),
        state_fields=state_fields,
        tools=tools,
        agent_class_name=agent_class.name,
        source_file=path,
    )


if __name__ == "__main__":
    manifest = parse_agent_file(sys.argv[1])
    print(manifest.to_json())
