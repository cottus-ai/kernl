from llama_index.core.tools import FunctionTool


def search(query: str) -> str:
    """Search the knowledge base for a topic."""
    kb = {
        "firecracker": "Firecracker is a VMM by AWS for microVMs. <50ms cold start.",
        "unikernel": "Unikernels are single-address-space OS images with minimal attack surface.",
        "python": "Python is a high-level interpreted language with dynamic typing.",
    }
    for key, val in kb.items():
        if key in query.lower():
            return val
    return f"No results for: {query}"


def compute(expression: str) -> str:
    """Evaluate a Python math expression."""
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"error: {e}"


tools = [
    FunctionTool.from_defaults(fn=search),
    FunctionTool.from_defaults(fn=compute),
]
