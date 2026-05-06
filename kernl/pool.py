import http.client
import queue
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from kernl.bundle import extract_to
from kernl.run import _FC, _fc_available, _tap_down, _tap_up


@dataclass
class _Worker:
    vm: _FC
    tap: str
    index: int
    reqs: int = 0
    born: float = field(default_factory=time.monotonic)

    def alive(self) -> bool:
        return self.vm._p is not None and self.vm._p.poll() is None


class VMPool:
    def __init__(
        self,
        krn: str | Path,
        size: int = 4,
        max_requests: int = 100,
        memory_mib: int = 128,
        timeout: int = 30,
    ) -> None:
        self.krn = Path(krn)
        self.size = size
        self.max_requests = max_requests
        self.memory_mib = memory_mib
        self.timeout = timeout
        self._q: queue.Queue[_Worker] = queue.Queue()
        self._workers: list[_Worker] = []
        self._staging: str | None = None
        self._rootfs: str | None = None
        self._kernel = __import__("os").environ.get("KERNL_KERNEL", "/opt/kernl/vmlinux")
        self._total = self._ok = self._err = 0
        self._latencies: list[float] = []

    def start(self) -> None:
        self._staging = tempfile.mkdtemp(prefix="kernl-pool-")
        extract_to(self.krn, Path(self._staging))
        rootfs = Path(self._staging) / "rootfs.img"
        self._rootfs = str(rootfs) if rootfs.exists() else None

        events = [threading.Event() for _ in range(self.size)]
        spawned: list[_Worker | None] = [None] * self.size

        def go(i: int, ev: threading.Event) -> None:
            spawned[i] = self._spawn(i)
            ev.set()

        for i, ev in enumerate(events):
            threading.Thread(target=go, args=(i, ev), daemon=True).start()
        for ev in events:
            ev.wait()

        for w in spawned:
            if w:
                self._workers.append(w)
                self._q.put(w)

    def submit(self, input_data: dict, dry_run: bool = False) -> dict:
        t0 = time.monotonic()
        self._total += 1

        if not self._workers:
            return self._submit_proc(input_data, dry_run, t0)

        try:
            w = self._q.get(timeout=self.timeout)
        except queue.Empty:
            self._err += 1
            return {"status": "error", "output": "pool exhausted", "steps": 0, "tool_calls": []}

        try:
            if not w.alive():
                new = self._replace(w)
                if new is None:
                    self._err += 1
                    return {"status": "error", "output": "worker dead", "steps": 0, "tool_calls": []}
                w = new

            result = w.vm.call(input_data, dry_run, self.timeout)
            w.reqs += 1
            ms = (time.monotonic() - t0) * 1000
            self._latencies.append(ms)
            if len(self._latencies) > 2000:
                self._latencies = self._latencies[-1000:]
            self._ok += 1

            if w.reqs >= self.max_requests:
                new = self._replace(w)
                if new:
                    w = new
            return result
        except Exception as e:
            self._err += 1
            return {"status": "error", "output": str(e), "steps": 0, "tool_calls": []}
        finally:
            self._q.put(w)

    def _submit_proc(self, input_data: dict, dry_run: bool, t0: float) -> dict:
        from kernl.run import run
        result = run(self.krn, input_data, dry_run=dry_run, mode="process")
        ms = (time.monotonic() - t0) * 1000
        self._latencies.append(ms)
        if result.get("status") == "complete":
            self._ok += 1
        else:
            self._err += 1
        return result

    def health(self) -> dict:
        alive = sum(1 for w in self._workers if w.alive())
        err_rate = self._err / max(self._total, 1)
        score = max(0, int(100 - err_rate * 400 - (self.size - alive) / max(self.size, 1) * 20))
        return {
            "score": score,
            "status": "healthy" if score >= 80 else "degraded" if score >= 50 else "unhealthy",
            "workers": {"alive": alive, "total": self.size},
            "requests": {"total": self._total, "ok": self._ok, "errors": self._err},
            "latency_p50": _pct(self._latencies, 50),
            "latency_p95": _pct(self._latencies, 95),
        }

    def shutdown(self) -> None:
        for w in self._workers:
            try:
                w.vm.stop()
                _tap_down(w.tap)
            except Exception:
                pass
        if self._staging:
            shutil.rmtree(self._staging, ignore_errors=True)

    def _spawn(self, i: int) -> _Worker | None:
        if not _fc_available() or not self._rootfs:
            return None
        vid = f"kernl-{uuid.uuid4().hex[:8]}"
        tap = f"tap{i}"
        host = f"172.{16 + i // 256}.{i % 256}.1"
        guest = f"172.{16 + i // 256}.{i % 256}.2"
        _tap_up(tap, host)
        vm = _FC(vid, f"/tmp/fc-{vid}.sock", tap, guest)
        try:
            vm.start(self._kernel, self._rootfs, self.memory_mib)
            end = time.monotonic() + 15
            while time.monotonic() < end:
                try:
                    c = http.client.HTTPConnection(guest, 8080, timeout=1)
                    c.request("GET", "/health")
                    if c.getresponse().status == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.1)
            return _Worker(vm=vm, tap=tap, index=i)
        except Exception:
            vm.stop()
            _tap_down(tap)
            return None

    def _replace(self, old: _Worker) -> _Worker | None:
        old.vm.stop()
        _tap_down(old.tap)
        new = self._spawn(old.index)
        if new and old in self._workers:
            self._workers[self._workers.index(old)] = new
        return new


def _pct(s: list[float], p: int) -> float:
    if not s:
        return 0.0
    xs = sorted(s)
    return xs[min(int(len(xs) * p / 100), len(xs) - 1)]
