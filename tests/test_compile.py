from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from kernl.agent import parse, AgentManifest, ToolDef
from kernl.bundle import pack, inspect, unpack
from kernl.compile import compile

NATIVE_AGENT = """\
from kernl import agent, tool

@agent(name="calc", model="claude-sonnet-4-20250514", max_steps=5)
class CalcAgent:
    question: str
    precision: int

    @tool
    def add(self, a: str, b: str) -> str:
        \"\"\"Add two numbers.\"\"\"
        return str(int(a) + int(b))

    @tool
    def multiply(self, a: str, b: str) -> str:
        \"\"\"Multiply two numbers.\"\"\"
        return str(int(a) * int(b))
"""

LANGCHAIN_AGENT = """\
from langchain.tools import BaseTool

class SearchTool(BaseTool):
    name = "search"
    description = "Search the knowledge base"

    def _run(self, query: str) -> str:
        return f"results: {query}"

class EchoTool(BaseTool):
    name = "echo"
    description = "Echo back input"

    def _run(self, text: str) -> str:
        return text
"""

LLAMAINDEX_AGENT = """\
from llama_index.core.tools import FunctionTool

def search(query: str) -> str:
    \"\"\"Search documents.\"\"\"
    return f"found: {query}"

def compute(expression: str) -> str:
    \"\"\"Evaluate math.\"\"\"
    return str(eval(expression, {}, {}))

tools = [FunctionTool.from_defaults(fn=search), FunctionTool.from_defaults(fn=compute)]
"""


@pytest.fixture()
def agent_file(tmp_path: Path) -> Path:
    p = tmp_path / "calc.agent.py"
    p.write_text(NATIVE_AGENT)
    return p


@pytest.fixture()
def lc_file(tmp_path: Path) -> Path:
    p = tmp_path / "lc.py"
    p.write_text(LANGCHAIN_AGENT)
    return p


@pytest.fixture()
def li_file(tmp_path: Path) -> Path:
    p = tmp_path / "li.py"
    p.write_text(LLAMAINDEX_AGENT)
    return p


class TestParser:
    def test_native_name_and_model(self, agent_file: Path) -> None:
        m = parse(agent_file)
        assert m.name == "calc"
        assert m.model == "claude-sonnet-4-20250514"

    def test_native_max_steps(self, agent_file: Path) -> None:
        m = parse(agent_file)
        assert m.max_steps == 5

    def test_native_framework(self, agent_file: Path) -> None:
        m = parse(agent_file)
        assert m.framework == "native"

    def test_native_tools(self, agent_file: Path) -> None:
        m = parse(agent_file)
        assert len(m.tools) == 2
        assert m.tools[0].name == "add"
        assert m.tools[1].name == "multiply"

    def test_native_tool_params(self, agent_file: Path) -> None:
        m = parse(agent_file)
        t = m.tools[0]
        assert "a" in t.parameters and "b" in t.parameters
        assert t.parameters["a"]["type"] == "string"
        assert set(t.required) == {"a", "b"}

    def test_native_tool_description(self, agent_file: Path) -> None:
        m = parse(agent_file)
        assert "add" in m.tools[0].description.lower() or "number" in m.tools[0].description.lower()

    def test_native_tool_source(self, agent_file: Path) -> None:
        m = parse(agent_file)
        assert "int(a)" in m.tools[0].source

    def test_native_state_fields(self, agent_file: Path) -> None:
        m = parse(agent_file)
        assert m.state_fields == {"question": "str", "precision": "int"}

    def test_self_excluded_from_params(self, agent_file: Path) -> None:
        m = parse(agent_file)
        for t in m.tools:
            assert "self" not in t.parameters
            assert "self" not in t.required

    def test_unknown_agent_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "plain.py"
        p.write_text("x = 1\n")
        with pytest.raises(ValueError, match="No agent definition"):
            parse(p)

    def test_nonexistent_file_raises(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            parse("/does/not/exist.py")

    def test_langchain_framework(self, lc_file: Path) -> None:
        m = parse(lc_file)
        assert m.framework == "langchain"

    def test_langchain_tools(self, lc_file: Path) -> None:
        m = parse(lc_file)
        assert len(m.tools) == 2
        names = [t.name for t in m.tools]
        assert "search" in names
        assert "echo" in names

    def test_langchain_tool_description(self, lc_file: Path) -> None:
        m = parse(lc_file)
        t = next(t for t in m.tools if t.name == "search")
        assert "knowledge" in t.description.lower() or "search" in t.description.lower()

    def test_llamaindex_framework(self, li_file: Path) -> None:
        m = parse(li_file)
        assert m.framework == "llamaindex"

    def test_llamaindex_tools(self, li_file: Path) -> None:
        m = parse(li_file)
        names = [t.name for t in m.tools]
        assert "search" in names
        assert "compute" in names

    def test_llamaindex_tool_docstring(self, li_file: Path) -> None:
        m = parse(li_file)
        t = next(t for t in m.tools if t.name == "search")
        assert "search" in t.description.lower() or "document" in t.description.lower()


class TestBundle:
    def test_pack_creates_file(self, agent_file: Path, tmp_path: Path) -> None:
        m = parse(agent_file)
        out = tmp_path / "calc.krn"
        path, h = pack(m, agent_file.read_text(), out)
        assert path.exists()
        assert path.stat().st_size > 0

    def test_pack_hash_is_hex(self, agent_file: Path, tmp_path: Path) -> None:
        m = parse(agent_file)
        _, h = pack(m, agent_file.read_text(), tmp_path / "calc.krn")
        assert len(h) == 16
        int(h, 16)

    def test_pack_deterministic(self, agent_file: Path, tmp_path: Path) -> None:
        m = parse(agent_file)
        src = agent_file.read_text()
        _, h1 = pack(m, src, tmp_path / "a.krn")
        _, h2 = pack(m, src, tmp_path / "b.krn")
        assert h1 == h2

    def test_unpack_roundtrip(self, agent_file: Path, tmp_path: Path) -> None:
        m = parse(agent_file)
        out = tmp_path / "calc.krn"
        pack(m, agent_file.read_text(), out)
        manifest, agent_src, runtime_src = unpack(out)
        assert manifest["name"] == "calc"
        assert "add" in agent_src
        assert "run_agent" in runtime_src

    def test_inspect_metadata(self, agent_file: Path, tmp_path: Path) -> None:
        m = parse(agent_file)
        out = tmp_path / "calc.krn"
        pack(m, agent_file.read_text(), out)
        info = inspect(out)
        assert info["name"] == "calc"
        assert info["image_type"] == "portable"
        assert "add" in info["tools"]
        assert info["hash"]

    def test_inspect_corrupted_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.krn"
        bad.write_bytes(b"not a tar file")
        with pytest.raises(Exception):
            inspect(bad)

    def test_inspect_size_bytes(self, agent_file: Path, tmp_path: Path) -> None:
        m = parse(agent_file)
        out = tmp_path / "calc.krn"
        pack(m, agent_file.read_text(), out)
        info = inspect(out)
        assert info["size_bytes"] == out.stat().st_size


class TestCompile:
    def test_compile_produces_krn(self, agent_file: Path, tmp_path: Path) -> None:
        img = compile(agent_file, tmp_path / "calc.krn")
        assert img.path.exists()

    def test_compile_default_output_path(self, agent_file: Path) -> None:
        img = compile(agent_file)
        assert img.path.suffix == ".krn"
        assert img.path.parent == agent_file.parent
        img.path.unlink(missing_ok=True)

    def test_compile_portable_type(self, agent_file: Path, tmp_path: Path) -> None:
        img = compile(agent_file, tmp_path / "calc.krn")
        assert img.image_type == "portable"

    def test_compile_hash_length(self, agent_file: Path, tmp_path: Path) -> None:
        img = compile(agent_file, tmp_path / "calc.krn")
        assert len(img.hash) == 16

    def test_compile_idempotent(self, agent_file: Path, tmp_path: Path) -> None:
        img1 = compile(agent_file, tmp_path / "a.krn")
        img2 = compile(agent_file, tmp_path / "b.krn")
        assert img1.hash == img2.hash

    def test_compile_langchain(self, lc_file: Path, tmp_path: Path) -> None:
        img = compile(lc_file, tmp_path / "lc.krn")
        assert img.path.exists()
        info = inspect(img.path)
        assert info["framework"] == "langchain"

    def test_compile_llamaindex(self, li_file: Path, tmp_path: Path) -> None:
        img = compile(li_file, tmp_path / "li.krn")
        assert img.path.exists()
        info = inspect(img.path)
        assert info["framework"] == "llamaindex"
