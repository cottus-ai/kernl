import hashlib
import io
import json
import tarfile
import tempfile
from pathlib import Path

from kernl.agent import AgentManifest

RUNTIME_PATH = Path(__file__).parent / "runtime.py"


def _safe_extractall(tar: tarfile.TarFile, dest: Path) -> None:
    try:
        tar.extractall(dest, filter="data")
    except TypeError:
        safe = [m for m in tar.getmembers() if _tar_member_is_safe(m)]
        tar.extractall(dest, members=safe)


def _tar_member_is_safe(m: tarfile.TarInfo) -> bool:
    if not m.name or m.name.startswith("/"):
        return False
    if ".." in Path(m.name).parts:
        return False
    return True


def pack(
    manifest: AgentManifest, agent_src: str, out: Path, image_type: str = "portable"
) -> tuple[Path, str]:
    manifest_json = json.dumps(_manifest_dict(manifest), indent=2).encode()
    agent_bytes = agent_src.encode()
    runtime_bytes = RUNTIME_PATH.read_bytes()

    content_hash = hashlib.sha256(manifest_json + agent_bytes).hexdigest()[:16]

    with tarfile.open(out, "w:gz") as tar:
        _add(tar, "manifest.json", manifest_json)
        _add(tar, "agent.py", agent_bytes)
        _add(tar, "runtime.py", runtime_bytes)
        _add(
            tar, "meta.json", json.dumps({"image_type": image_type, "hash": content_hash}).encode()
        )

    return out, content_hash


def pack_unikernel(
    manifest: AgentManifest, agent_src: str, vmlinux: Path, rootfs: Path, out: Path
) -> tuple[Path, str]:
    manifest_json = json.dumps(_manifest_dict(manifest), indent=2).encode()
    content_hash = hashlib.sha256(manifest_json + agent_src.encode()).hexdigest()[:16]

    with tarfile.open(out, "w:gz") as tar:
        _add(tar, "manifest.json", manifest_json)
        _add(tar, "agent.py", agent_src.encode())
        _add(tar, "runtime.py", RUNTIME_PATH.read_bytes())
        tar.add(vmlinux, arcname="vmlinux")
        tar.add(rootfs, arcname="rootfs.img")
        _add(
            tar, "meta.json", json.dumps({"image_type": "unikernel", "hash": content_hash}).encode()
        )

    return out, content_hash


def _read_tar_text(tar: tarfile.TarFile, name: str) -> str:
    f = tar.extractfile(name)
    if f is None:
        raise KeyError(name)
    return f.read().decode()


def unpack(krn: Path) -> tuple[dict, str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        with tarfile.open(krn, "r:gz") as tar:
            _safe_extractall(tar, staging)
        manifest = json.loads((staging / "manifest.json").read_text())
        agent_src = (staging / "agent.py").read_text()
        runtime_src = (staging / "runtime.py").read_text()
    return manifest, agent_src, runtime_src


def extract_to(krn: Path, dest: Path) -> dict:
    with tarfile.open(krn, "r:gz") as tar:
        _safe_extractall(tar, dest)
    meta_path = dest / "meta.json"
    return json.loads(meta_path.read_text()) if meta_path.exists() else {}


def inspect(krn: str | Path) -> dict:
    krn = Path(krn)
    size_bytes = krn.stat().st_size
    with tarfile.open(krn, "r:gz") as tar:
        names = tar.getnames()
        manifest = json.loads(_read_tar_text(tar, "manifest.json"))
        meta: dict = {}
        if "meta.json" in names:
            f = tar.extractfile("meta.json")
            if f is not None:
                meta = json.loads(f.read())
    return {
        "name": manifest.get("name"),
        "model": manifest.get("model"),
        "framework": manifest.get("framework", "native"),
        "max_steps": manifest.get("max_steps", 10),
        "tools": [t["name"] for t in manifest.get("tools", [])],
        "image_type": meta.get("image_type", "portable"),
        "hash": meta.get("hash"),
        "size_bytes": size_bytes,
        "files": names,
    }


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _manifest_dict(m: AgentManifest) -> dict:
    return {
        "name": m.name,
        "model": m.model,
        "system_prompt": m.system_prompt,
        "framework": m.framework,
        "max_steps": m.max_steps,
        "allow_network": m.allow_network,
        "state_fields": m.state_fields,
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "required": t.required,
                "source": t.source,
            }
            for t in m.tools
        ],
    }
