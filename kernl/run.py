import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

from kernl.bundle import extract_to


def run(
    krn: str | Path,
    input_data: dict,
    dry_run: bool = False,
    timeout: int = 30,
    mode: str = "auto",
) -> dict:
    krn = Path(krn)
    t0 = time.monotonic()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            meta = extract_to(krn, staging)

            use_fc = mode == "firecracker" or (
                mode == "auto"
                and meta.get("image_type") == "unikernel"
                and _fc_available()
                and (staging / "rootfs.img").exists()
            )
            result = _run_fc(staging, input_data, dry_run, timeout) if use_fc else _run_proc(staging, input_data, dry_run, timeout)
    except Exception as e:
        result = {"status": "error", "output": str(e), "steps": 0, "tool_calls": []}

    result["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 2)
    return result


def _run_proc(staging: Path, input_data: dict, dry_run: bool, timeout: int) -> dict:
    env = {**os.environ, "KERNL_MANIFEST": str(staging / "manifest.json"), "KERNL_MODE": "single"}
    if dry_run:
        env["KERNL_DRY_RUN"] = "1"

    proc = subprocess.Popen(
        [sys.executable, str(staging / "runtime.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        out, err = proc.communicate(
            (json.dumps({"input": input_data, "dry_run": dry_run}) + "\n").encode(), timeout=timeout
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return {"status": "timeout", "output": "", "steps": 0, "tool_calls": []}

    if proc.returncode != 0:
        return {"status": "error", "output": err.decode()[:500], "steps": 0, "tool_calls": []}

    line = out.decode().strip()
    return json.loads(line) if line else {"status": "error", "output": "no output", "steps": 0, "tool_calls": []}


def _run_fc(staging: Path, input_data: dict, dry_run: bool, timeout: int) -> dict:
    vm_id = f"kernl-{os.getpid()}"
    sock = f"/tmp/fc-{vm_id}.sock"
    tap, guest_ip = "tap_krun0", "172.16.0.2"

    _tap_up(tap, "172.16.0.1")
    vm = _FC(vm_id, sock, tap, guest_ip)
    try:
        vm.start(os.environ.get("KERNL_KERNEL", "/opt/kernl/vmlinux"), str(staging / "rootfs.img"))
        return vm.call(input_data, dry_run, timeout)
    finally:
        vm.stop()
        _tap_down(tap)


class _FC:
    def __init__(self, vid: str, sock: str, tap: str, guest: str) -> None:
        self.vid, self.sock, self.tap, self.guest = vid, sock, tap, guest
        self._p: subprocess.Popen | None = None

    def start(self, kernel: str, rootfs: str, mem: int = 128) -> None:
        if os.path.exists(self.sock):
            os.unlink(self.sock)
        self._p = subprocess.Popen(
            ["firecracker", "--api-sock", self.sock, "--log-path", f"/tmp/fc-{self.vid}.log", "--level", "Error"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _wait_sock(self.sock)
        for path, body in [
            ("/machine-config", {"vcpu_count": 1, "mem_size_mib": mem}),
            ("/boot-source", {"kernel_image_path": kernel, "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"}),
            ("/drives/rootfs", {"drive_id": "rootfs", "path_on_host": rootfs, "is_root_device": True, "is_read_only": False}),
            ("/network-interfaces/eth0", {"iface_id": "eth0", "host_dev_name": self.tap, "guest_mac": _mac(self.vid)}),
            ("/actions", {"action_type": "InstanceStart"}),
        ]:
            self._put(path, cast(dict[str, Any], body))

    def call(self, inp: dict, dry: bool, timeout: int) -> dict:
        data = json.dumps({"input": inp, "dry_run": dry}).encode()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                c = http.client.HTTPConnection(self.guest, 8080, timeout=3)
                c.request("POST", "/run", body=data, headers={"Content-Type": "application/json"})
                return json.loads(c.getresponse().read())
            except Exception:
                time.sleep(0.05)
        raise TimeoutError(f"VM {self.vid} unresponsive")

    def stop(self) -> None:
        if self._p:
            self._p.terminate()
            try:
                self._p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._p.kill()
                self._p.wait()
        for p in (self.sock, f"/tmp/fc-{self.vid}.log"):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass

    def _put(self, path: str, body: dict) -> None:
        sock_path = self.sock

        class _Unix(http.client.HTTPConnection):
            def connect(self) -> None:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(sock_path)

        c = _Unix("localhost")
        d = json.dumps(body).encode()
        c.request("PUT", path, body=d, headers={"Content-Type": "application/json", "Content-Length": str(len(d))})
        r = c.getresponse()
        if r.status >= 400:
            raise RuntimeError(f"FC API {path}: {r.status} {r.read().decode()}")


def _fc_available() -> bool:
    return shutil.which("firecracker") is not None


def _tap_up(name: str, host_ip: str) -> None:
    for cmd in (
        ["ip", "tuntap", "add", "dev", name, "mode", "tap"],
        ["ip", "addr", "add", f"{host_ip}/30", "dev", name],
        ["ip", "link", "set", name, "up"],
    ):
        subprocess.run(cmd, capture_output=True)


def _tap_down(name: str) -> None:
    subprocess.run(["ip", "link", "del", name], capture_output=True)


def _wait_sock(path: str, t: float = 5.0) -> None:
    end = time.monotonic() + t
    while time.monotonic() < end:
        if os.path.exists(path):
            return
        time.sleep(0.02)
    raise TimeoutError(f"socket not ready: {path}")


def _mac(vid: str) -> str:
    h = hash(vid) & 0xFF_FFFF_FFFF
    return "02:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}".format(*((h >> s) & 0xFF for s in (32, 24, 16, 8, 0)))
