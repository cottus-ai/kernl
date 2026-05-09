import threading
from pathlib import Path

import pytest

from akernl.compile import compile
from akernl.pool import VMPool

AGENT = """\
from akernl import agent, tool

@agent(name="pool_test", model="claude-sonnet-4-20250514", max_steps=2)
class PoolTestAgent:
    input_data: str

    @tool
    def echo(self, message: str) -> str:
        \"\"\"Echo a message.\"\"\"
        return message
"""


@pytest.fixture(scope="module")
def krn(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("pool")
    af = tmp / "pool_test.py"
    af.write_text(AGENT)
    return compile(af, tmp / "pool_test.krn").path


class TestVMPool:
    def test_pool_health_shape(self, krn: Path) -> None:
        pool = VMPool(krn, size=2)
        pool.start()
        try:
            h = pool.health()
            assert "score" in h
            assert "status" in h
            assert "workers" in h
            assert "requests" in h
            assert h["score"] >= 0
        finally:
            pool.shutdown()

    def test_pool_status_values(self, krn: Path) -> None:
        pool = VMPool(krn, size=2)
        pool.start()
        try:
            h = pool.health()
            assert h["status"] in ("healthy", "degraded", "unhealthy")
        finally:
            pool.shutdown()

    def test_pool_submit_returns_dict(self, krn: Path) -> None:
        pool = VMPool(krn, size=2)
        pool.start()
        try:
            r = pool.submit({"input_data": "hello"}, dry_run=True)
            assert isinstance(r, dict)
            assert "status" in r
        finally:
            pool.shutdown()

    def test_pool_submit_dry_run(self, krn: Path) -> None:
        pool = VMPool(krn, size=2)
        pool.start()
        try:
            r = pool.submit({"input_data": "hello"}, dry_run=True)
            assert r["status"] in ("complete", "error")
        finally:
            pool.shutdown()

    def test_pool_sequential_requests(self, krn: Path) -> None:
        pool = VMPool(krn, size=2)
        pool.start()
        try:
            results = [pool.submit({"input_data": str(i)}, dry_run=True) for i in range(5)]
            assert len(results) == 5
        finally:
            pool.shutdown()

    def test_pool_concurrent_requests(self, krn: Path) -> None:
        pool = VMPool(krn, size=4)
        pool.start()
        results: list[dict] = []
        lock = threading.Lock()

        def worker() -> None:
            r = pool.submit({"input_data": "x"}, dry_run=True)
            with lock:
                results.append(r)

        try:
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

            assert len(results) == 8
            assert all("status" in r for r in results)
        finally:
            pool.shutdown()

    def test_pool_shutdown_clean(self, krn: Path) -> None:
        pool = VMPool(krn, size=2)
        pool.start()
        pool.shutdown()

    def test_pool_health_after_requests(self, krn: Path) -> None:
        pool = VMPool(krn, size=2)
        pool.start()
        try:
            for _ in range(3):
                pool.submit({"input_data": "test"}, dry_run=True)
            h = pool.health()
            assert h["requests"]["total"] == 3
        finally:
            pool.shutdown()

    def test_pool_latency_in_health(self, krn: Path) -> None:
        pool = VMPool(krn, size=2)
        pool.start()
        try:
            pool.submit({"input_data": "test"}, dry_run=True)
            h = pool.health()
            assert "latency_p50" in h
            assert "latency_p95" in h
        finally:
            pool.shutdown()
