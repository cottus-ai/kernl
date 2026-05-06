import pytest

from kernl.runtime import _api_tools, _build_executors, _mock


def test_build_executors_valid_tool() -> None:
    tools = [
        {
            "name": "add",
            "description": "add",
            "parameters": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"],
            "source": "def add(self, a: str, b: str) -> str:\n    return str(int(a) + int(b))\n",
        }
    ]
    ex = _build_executors(tools)
    assert "add" in ex
    assert ex["add"]("2", "3") == "5"


def test_build_executors_invalid_tool_skips(capsys: pytest.CaptureFixture[str]) -> None:
    tools = [
        {
            "name": "broken",
            "description": "x",
            "parameters": {},
            "required": [],
            "source": "this is not valid python !!!\n",
        }
    ]
    ex = _build_executors(tools)
    assert ex == {}
    err = capsys.readouterr().err
    assert "broken" in err or "skipping" in err


def test_mock_step0_tool_use() -> None:
    tools = [{"name": "echo", "parameters": {"m": {"type": "string"}}, "required": ["m"]}]
    r = _mock([{"role": "user", "content": "{}"}], tools, 0)
    assert r["stop_reason"] == "tool_use"
    assert r["content"][0]["name"] == "echo"


def test_mock_step1_end_turn() -> None:
    tools = [{"name": "echo", "parameters": {}}]
    msgs = [
        {"role": "user", "content": "{}"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "m0", "name": "echo", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "m0", "content": "ok"}],
        },
    ]
    r = _mock(msgs, tools, 1)
    assert r["stop_reason"] == "end_turn"
    assert any(b.get("type") == "text" for b in r["content"])


def test_api_tools_anthropic_shape() -> None:
    tools = [
        {
            "name": "t",
            "description": "d",
            "parameters": {"x": {"type": "string"}},
            "required": ["x"],
            "source": "",
        }
    ]
    api = _api_tools(tools)
    assert api[0]["name"] == "t"
    assert api[0]["input_schema"]["type"] == "object"
    assert "x" in api[0]["input_schema"]["properties"]
    assert api[0]["input_schema"]["required"] == ["x"]
