import json
import os
import ssl
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def run_agent(manifest: dict, input_data: dict, dry_run: bool = False) -> dict:
    tools_def = manifest.get("tools", [])
    executors = _build_executors(tools_def)
    messages: list[dict] = [{"role": "user", "content": json.dumps(input_data)}]
    tool_calls: list[dict] = []
    steps = 0

    while steps < manifest.get("max_steps", 10):
        if dry_run:
            resp = _mock(messages, tools_def, steps)
        else:
            resp = _llm(messages, manifest["model"], manifest.get("system_prompt", ""), _api_tools(tools_def))

        stop = resp.get("stop_reason", "end_turn")
        content = resp.get("content", [])

        if stop == "end_turn":
            return {"status": "complete", "output": _text(content), "steps": steps + 1, "tool_calls": tool_calls}

        if stop == "tool_use":
            messages.append({"role": "assistant", "content": content})
            results = []
            for blk in content:
                if blk.get("type") != "tool_use":
                    continue
                name, inp, tid = blk["name"], blk.get("input", {}), blk["id"]
                td = next((t for t in tools_def if t["name"] == name), None)
                out = _call_tool(td, inp, executors) if td else f"unknown tool: {name}"
                tool_calls.append({"tool": name, "input": inp, "result": out})
                results.append({"type": "tool_result", "tool_use_id": tid, "content": out})
            messages.append({"role": "user", "content": results})
            steps += 1
            continue

        return {"status": "complete", "output": _text(content), "steps": steps + 1, "tool_calls": tool_calls}

    return {"status": "max_steps", "output": "", "steps": steps, "tool_calls": tool_calls}


def _llm(messages: list[dict], model: str, system: str, tools: list[dict]) -> dict:
    body: dict[str, Any] = {"model": model, "max_tokens": 4096, "system": system, "messages": messages}
    if tools:
        body["tools"] = tools
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=60) as r:
        return json.loads(r.read())


def _mock(messages: list[dict], tools: list[Any], step: int) -> dict:
    if step == 0 and tools:
        t = tools[0]
        return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "m0", "name": t["name"], "input": {k: "test" for k in t.get("parameters", {})}}]}
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "done")
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": f"Result: {last if isinstance(last, str) else json.dumps(last)}"[:200]}]}


def _build_executors(tools: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for t in tools:
        try:
            name = t["name"]
            src = t["source"]
            indented = "\n".join("    " + line for line in src.splitlines())
            ns: dict[str, Any] = {}
            exec(compile(f"class _T:\n{indented}\n_inst = _T()", "<tool>", "exec"), ns)
            fn = getattr(ns.get("_inst"), name, None)
            if fn is None:
                ns2: dict[str, Any] = {}
                exec(compile(src, "<tool>", "exec"), ns2)
                fn = next((v for v in ns2.values() if callable(v) and getattr(v, "__name__", None) == name), None)
            if fn:
                out[name] = fn
        except Exception:
            pass
    return out


def _call_tool(td: dict, inp: dict, executors: dict) -> str:
    fn = executors.get(td["name"])
    if not fn:
        return f"tool not available: {td['name']}"
    try:
        return str(fn(**inp))
    except Exception as e:
        return f"error: {e}"


def _api_tools(tools: list[dict]) -> list[dict]:
    return [{"name": t["name"], "description": t.get("description", ""), "input_schema": {"type": "object", "properties": t.get("parameters", {}), "required": t.get("required", [])}} for t in tools]


def _text(content: list[dict]) -> str:
    return next((b["text"] for b in content if b.get("type") == "text"), "")


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/run":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        dry = body.get("dry_run", False) or os.environ.get("KERNL_DRY_RUN") == "1"
        resp = json.dumps(run_agent(self.server.manifest, body.get("input", {}), dry)).encode()  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_error(404)

    def log_message(self, *_: Any) -> None:
        pass


def main() -> None:
    mp = os.environ.get("KERNL_MANIFEST", sys.argv[1] if len(sys.argv) > 1 else "")
    manifest = json.load(open(mp)) if mp else json.loads(sys.stdin.readline())

    if os.environ.get("KERNL_MODE") == "server":
        srv = HTTPServer(("0.0.0.0", int(os.environ.get("KERNL_PORT", "8080"))), _Handler)
        srv.manifest = manifest  # type: ignore[attr-defined]
        srv.serve_forever()
    else:
        body = json.loads(sys.stdin.readline())
        dry = body.get("dry_run", False) or os.environ.get("KERNL_DRY_RUN") == "1"
        sys.stdout.write(json.dumps(run_agent(manifest, body.get("input", {}), dry)) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
