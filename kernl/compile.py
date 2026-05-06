import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kernl.agent import AgentManifest, parse
from kernl.bundle import RUNTIME_PATH, pack, pack_unikernel


@dataclass
class Image:
    path: Path
    hash: str
    image_type: str
    size: int


def compile(agent_path: str | Path, output: str | Path | None = None) -> Image:
    agent_path = Path(agent_path)
    manifest = parse(agent_path)
    agent_src = agent_path.read_text()

    stem = agent_path.stem.replace(".agent", "")
    out = Path(output) if output else agent_path.parent / f"{stem}.krn"

    if _ops_available():
        try:
            return _compile_unikernel(manifest, agent_src, out)
        except (FileNotFoundError, OSError):
            pass
        except Exception as e:
            print(f"kernl: unikernel build skipped: {e}", file=sys.stderr)

    _, h = pack(manifest, agent_src, out)
    return Image(path=out, hash=h, image_type="portable", size=out.stat().st_size)


def _compile_unikernel(manifest: AgentManifest, agent_src: str, out: Path) -> Image:
    output_stem = out.stem

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "manifest.json").write_text(json.dumps({"name": manifest.name}))
        (staging / "agent.py").write_text(agent_src)
        shutil.copy(RUNTIME_PATH, staging / "runtime.py")
        (staging / "config.json").write_text(json.dumps(_ops_config(manifest)))

        result = subprocess.run(
            [
                "ops",
                "build",
                "--config",
                "config.json",
                "--target",
                "firecracker",
                "-o",
                output_stem,
            ],
            cwd=staging,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)

        vmlinux = next(staging.glob("*kernel*"), None) or next(staging.glob("vmlinux*"), None)
        rootfs = next(staging.glob("*.img"), None)

        if not rootfs:
            raise RuntimeError("ops produced no rootfs image")

        _, h = pack_unikernel(manifest, agent_src, vmlinux or staging / "vmlinux", rootfs, out)

    return Image(path=out, hash=h, image_type="unikernel", size=out.stat().st_size)


def _ops_config(manifest: AgentManifest) -> dict:
    return {
        "Language": "python",
        "Args": ["/runtime.py"],
        "Files": ["agent.py", "runtime.py", "manifest.json"],
        "Env": {"KERNL_MANIFEST": "/manifest.json", "KERNL_MODE": "server"},
        "RunConfig": {"Memory": "128m", "Ports": ["8080"]},
    }


def _ops_available() -> bool:
    return shutil.which("ops") is not None
