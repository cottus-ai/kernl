from pathlib import Path

import pytest

from kernl.bundle import extract_to
from kernl.compile import compile
from kernl.run import run

BENCH_AGENT = """\
from kernl import agent, tool

@agent(name="bench", model="claude-sonnet-4-20250514", max_steps=3)
class BenchAgent:
    input_data: str

    @tool
    def compute(self, n: str) -> str:
        \"\"\"Sum squares up to n.\"\"\"
        return str(sum(i * i for i in range(int(n))))

    @tool
    def echo(self, message: str) -> str:
        \"\"\"Echo a message.\"\"\"
        return message
"""

BROKEN_AGENT = """\
from kernl import agent, tool

@agent(name="broken", model="claude-sonnet-4-20250514", max_steps=3)
class BrokenAgent:
    x: str

    @tool
    def fail(self, msg: str) -> str:
        \"\"\"Always raises.\"\"\"
        raise RuntimeError("intentional failure")
"""

NO_TOOLS_AGENT = """\
from kernl import agent, tool

@agent(name="notool", model="claude-sonnet-4-20250514", max_steps=2)
class NoToolAgent:
    question: str
"""


@pytest.fixture(scope="module")
def bench_krn(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("bench")
    af = tmp / "bench.py"
    af.write_text(BENCH_AGENT)
    return compile(af, tmp / "bench.krn").path


@pytest.fixture(scope="module")
def broken_krn(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("broken")
    af = tmp / "broken.py"
    af.write_text(BROKEN_AGENT)
    return compile(af, tmp / "broken.krn").path


class TestRunDryRun:
    def test_status_complete(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True)
        assert r["status"] == "complete"

    def test_steps_positive(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True)
        assert r["steps"] >= 1

    def test_output_is_str(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True)
        assert isinstance(r["output"], str)

    def test_tool_calls_list(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True)
        assert isinstance(r["tool_calls"], list)

    def test_tool_called(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True)
        assert len(r["tool_calls"]) >= 1

    def test_tool_call_shape(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True)
        tc = r["tool_calls"][0]
        assert "tool" in tc and "input" in tc and "result" in tc

    def test_elapsed_ms(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True)
        assert r["elapsed_ms"] > 0

    def test_tool_result_not_self_error(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "5"}, dry_run=True)
        for tc in r["tool_calls"]:
            assert "missing 1 required positional" not in tc["result"]


class TestRunModes:
    def test_mode_process(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True, mode="process")
        assert r["status"] == "complete"

    def test_mode_auto_falls_back(self, bench_krn: Path) -> None:
        r = run(bench_krn, {"input_data": "hello"}, dry_run=True, mode="auto")
        assert r["status"] == "complete"

    def test_sequential_runs(self, bench_krn: Path) -> None:
        results = [run(bench_krn, {"input_data": str(i)}, dry_run=True) for i in range(5)]
        assert all(r["status"] == "complete" for r in results)


class TestRunErrors:
    def test_nonexistent_krn(self) -> None:
        r = run("/does/not/exist.krn", {}, dry_run=True)
        assert r["status"] == "error"

    def test_corrupted_krn(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.krn"
        bad.write_bytes(b"garbage data")
        r = run(bad, {}, dry_run=True)
        assert r["status"] == "error"

    def test_broken_tool_returns_error_result(self, broken_krn: Path) -> None:
        r = run(broken_krn, {"x": "go"}, dry_run=True)
        assert r["status"] == "complete"
        if r["tool_calls"]:
            assert (
                "error" in r["tool_calls"][0]["result"].lower()
                or "intentional" in r["tool_calls"][0]["result"].lower()
            )

    def test_timeout_returns_timeout_or_error(self, tmp_path: Path) -> None:
        src = """\
from kernl import agent, tool

@agent(name="slow", model="claude-sonnet-4-20250514", max_steps=1)
class SlowAgent:
    x: str

    @tool
    def block(self, t: str) -> str:
        \"\"\"Blocks forever.\"\"\"
        import time; time.sleep(999); return "done"
"""
        af = tmp_path / "slow.py"
        af.write_text(src)
        img = compile(af, tmp_path / "slow.krn")
        r = run(img.path, {"x": "go"}, dry_run=False, timeout=1)
        assert r["status"] in ("timeout", "error")


class TestRunProcess:
    def test_proc_extracts_and_runs(self, bench_krn: Path, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        extract_to(bench_krn, staging)
        from kernl.run import _run_proc

        r = _run_proc(staging, {"input_data": "hello"}, dry_run=True, timeout=30)
        assert r["status"] == "complete"

    def test_proc_dry_run_env(self, bench_krn: Path, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        extract_to(bench_krn, staging)
        from kernl.run import _run_proc

        r = _run_proc(staging, {"input_data": "hello"}, dry_run=True, timeout=30)
        assert r["status"] == "complete"


class TestNoTools:
    def test_no_tools_agent_compiles(self, tmp_path: Path) -> None:
        af = tmp_path / "notool.py"
        af.write_text(NO_TOOLS_AGENT)
        img = compile(af, tmp_path / "notool.krn")
        assert img.path.exists()

    def test_no_tools_agent_runs(self, tmp_path: Path) -> None:
        af = tmp_path / "notool.py"
        af.write_text(NO_TOOLS_AGENT)
        img = compile(af, tmp_path / "notool.krn")
        r = run(img.path, {"question": "hello"}, dry_run=True)
        assert r["status"] == "complete"
        assert r["tool_calls"] == []
