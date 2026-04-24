"""
Kernl Runtime — minimal agent executor.

This file runs INSIDE the sandbox. It has no external dependencies.
It implements the agent loop using only the Python standard library:
  json, urllib, ssl, sys, os

This replaces: LangChain (~50MB), httpx (~5MB), pydantic (~10MB), etc.
The entire agent framework is ~200 lines.
"""
import json
import os
import sys
import time


# ---------------------------------------------------------------------------
# Minimal LLM client — replaces the Anthropic SDK (~5MB)
# ---------------------------------------------------------------------------

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
def _is_dry_run() -> bool:
    """Check dry-run mode per-call, not at import time.

    In pool workers, runtime is imported once but KERNL_DRY_RUN may
    differ per request (set via worker env restore). Evaluating at
    call time allows workers to switch between mock and real API.
    """
    return os.environ.get("KERNL_DRY_RUN", "") == "1"


def _mock_llm_call(messages: list[dict], tools: list[dict]) -> dict:
    """
    Simulate a multi-step LLM interaction for testing.

    Step 1: If tools available and input looks like a math question, call calculate
    Step 2: Return a text answer incorporating the tool result
    """
    last_msg = messages[-1]

    # If the last message contains tool_result, we're in step 2 — return final answer
    if isinstance(last_msg.get("content"), list):
        for block in last_msg["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_output = block.get("content", "")
                return {
                    "id": "msg_mock_002",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"The answer is {tool_output}."}],
                    "stop_reason": "end_turn",
                    "model": "mock",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                }

    # Step 1: call a tool if available
    if tools:
        # Pick the first tool and make a plausible call
        tool = tools[0]
        tool_name = tool["name"]
        # Generate a plausible input based on tool name
        if tool_name == "calculate":
            tool_input = {"expression": "2 + 2"}
        elif tool_name == "lookup_constant":
            tool_input = {"name": "pi"}
        else:
            # Generic: fill required params with placeholder
            tool_input = {}
            schema = tool.get("input_schema", {})
            for param in schema.get("required", []):
                tool_input[param] = "test"

        return {
            "id": "msg_mock_001",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"Let me use the {tool_name} tool."},
                {
                    "type": "tool_use",
                    "id": "toolu_mock_001",
                    "name": tool_name,
                    "input": tool_input,
                },
            ],
            "stop_reason": "tool_use",
            "model": "mock",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

    # No tools — just return a text answer
    return {
        "id": "msg_mock_001",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello! I'm running in dry-run mode."}],
        "stop_reason": "end_turn",
        "model": "mock",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def llm_call(
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4096,
) -> dict:
    """Make a single LLM API call. No streaming, no retries, no abstractions."""
    if _is_dry_run():
        return _mock_llm_call(messages, tools)

    # Defer heavy imports until actually needed (ssl + urllib cost ~40ms)
    import ssl
    import urllib.request
    import urllib.error

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
        },
        method="POST",
    )

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {e.code}: {error_body}") from e


# ---------------------------------------------------------------------------
# Tool execution engine — replaces agent framework tool dispatch
# ---------------------------------------------------------------------------

def build_tool_executor(agent_source: str, tool_defs: list[dict]) -> dict:
    """
    Compile tool methods from agent source into callable functions.

    We exec() only the tool method bodies, not the entire agent file.
    Each tool becomes a standalone function.
    """
    executors = {}
    namespace = {"__builtins__": __builtins__}

    for tool_def in tool_defs:
        source = tool_def["source"]
        # Remove the @tool decorator line
        lines = source.split("\n")
        cleaned = "\n".join(
            line for line in lines if not line.strip().startswith("@tool")
        )
        # Dedent to top level — the method is indented inside a class
        min_indent = float("inf")
        for line in cleaned.split("\n"):
            stripped = line.lstrip()
            if stripped:
                indent = len(line) - len(stripped)
                min_indent = min(min_indent, indent)
        if min_indent == float("inf"):
            min_indent = 0
        dedented = "\n".join(line[min_indent:] for line in cleaned.split("\n"))

        # Replace 'self' parameter — tools in Kernl are standalone functions
        # Convert 'def calculate(self, expression: str)' to 'def calculate(expression: str)'
        import re
        dedented = re.sub(
            r"(def\s+\w+\s*\(\s*)self\s*,?\s*",
            r"\1",
            dedented,
            count=1,
        )

        try:
            exec(dedented, namespace)
            executors[tool_def["name"]] = namespace[tool_def["name"]]
        except Exception as e:
            # Tool failed to compile — create a stub that returns the error
            name = tool_def["name"]
            def _make_err(err, nm):
                return lambda **kw: f"TOOL ERROR ({nm}): {err}"
            executors[name] = _make_err(e, name)

    return executors


def execute_tool(executors: dict, tool_name: str, tool_input: dict) -> str:
    """Execute a tool and return its string result."""
    if tool_name not in executors:
        return f"ERROR: unknown tool '{tool_name}'"
    try:
        result = executors[tool_name](**tool_input)
        return str(result)
    except Exception as e:
        return f"ERROR executing {tool_name}: {e}"


# ---------------------------------------------------------------------------
# Agent loop — the core sense-think-act cycle
# ---------------------------------------------------------------------------

def build_anthropic_tools(tool_defs: list[dict]) -> list[dict]:
    """Convert Kernl tool definitions to Anthropic API tool format."""
    api_tools = []
    for t in tool_defs:
        api_tools.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        })
    return api_tools


def run_agent(manifest: dict, input_data: dict, agent_source: str | None = None) -> dict:
    """
    Execute the agent loop.

    Returns: {
        "status": "complete" | "error" | "max_steps",
        "output": str,
        "steps": int,
        "tool_calls": [{"tool": str, "input": dict, "output": str}],
        "elapsed_ms": float,
    }
    """
    t_start = time.monotonic()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    dry_run = os.environ.get("KERNL_DRY_RUN", "") == "1"
    if not api_key and not dry_run:
        return {"status": "error", "output": "ANTHROPIC_API_KEY not set", "steps": 0,
                "tool_calls": [], "elapsed_ms": 0}

    agent = manifest["agent"]
    model = agent["model"]
    system_prompt = agent["system_prompt"]
    max_steps = agent["max_steps"]
    tool_defs = agent["tools"]

    # Build tool executors from source
    if agent_source is None:
        with open("agent.py") as f:
            agent_source = f.read()
    executors = build_tool_executor(agent_source, tool_defs)
    api_tools = build_anthropic_tools(tool_defs)

    # Build initial message from input state fields
    user_content = ""
    for field_name, field_value in input_data.items():
        user_content += f"{field_name}: {field_value}\n"
    user_content = user_content.strip()

    messages = [{"role": "user", "content": user_content}]
    all_tool_calls = []

    # --- Agent loop ---
    for step in range(max_steps):
        # SENSE: call LLM
        try:
            response = llm_call(
                api_key=api_key,
                model=model,
                system=system_prompt,
                messages=messages,
                tools=api_tools if tool_defs else [],
            )
        except Exception as e:
            elapsed = (time.monotonic() - t_start) * 1000
            return {"status": "error", "output": f"LLM call failed: {e}",
                    "steps": step, "tool_calls": all_tool_calls, "elapsed_ms": elapsed}

        stop_reason = response.get("stop_reason", "end_turn")
        content_blocks = response.get("content", [])

        # THINK: check if done or needs tool calls
        if stop_reason == "end_turn" or stop_reason == "stop":
            # Extract text response
            text_parts = [b["text"] for b in content_blocks if b["type"] == "text"]
            output = "\n".join(text_parts)
            elapsed = (time.monotonic() - t_start) * 1000
            return {"status": "complete", "output": output, "steps": step + 1,
                    "tool_calls": all_tool_calls, "elapsed_ms": elapsed}

        if stop_reason == "tool_use":
            # Append assistant message with full content
            messages.append({"role": "assistant", "content": content_blocks})

            # ACT: execute each tool call
            tool_results = []
            for block in content_blocks:
                if block["type"] != "tool_use":
                    continue
                tool_name = block["name"]
                tool_input = block["input"]
                tool_id = block["id"]

                result = execute_tool(executors, tool_name, tool_input)
                all_tool_calls.append({
                    "tool": tool_name, "input": tool_input, "output": result
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})
        else:
            # Unknown stop reason — treat as done
            text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
            output = "\n".join(text_parts) if text_parts else str(content_blocks)
            elapsed = (time.monotonic() - t_start) * 1000
            return {"status": "complete", "output": output, "steps": step + 1,
                    "tool_calls": all_tool_calls, "elapsed_ms": elapsed}

    # Max steps reached
    elapsed = (time.monotonic() - t_start) * 1000
    text_parts = []
    for block in content_blocks:
        if block.get("type") == "text":
            text_parts.append(block["text"])
    return {"status": "max_steps", "output": "\n".join(text_parts),
            "steps": max_steps, "tool_calls": all_tool_calls, "elapsed_ms": elapsed}


# ---------------------------------------------------------------------------
# Entry point — invoked by the Kernl runner inside the sandbox
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"status": "error", "output": "Usage: runtime.py <manifest.json> <input_json>"}))
        sys.exit(1)

    # Load manifest
    with open(sys.argv[1]) as f:
        manifest = json.load(f)

    # Parse input
    input_data = json.loads(sys.argv[2])

    # Run
    result = run_agent(manifest, input_data)

    # Output as JSON on stdout — the only communication channel
    print(json.dumps(result))


if __name__ == "__main__":
    main()
