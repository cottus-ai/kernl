"""
Build a .kb (Kernl Bundle) from an agent definition.

A .kb is a tar.gz containing:
  manifest.json   — agent metadata, tool schemas, memory budget
  agent.py        — the original agent source
  runtime.py      — the minimal executor (injected by Kernl)
"""
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile

from src.manifest import parse_agent_file


RUNTIME_PATH = os.path.join(os.path.dirname(__file__), "runtime.py")


def compute_hash(manifest_json: str, agent_source: bytes) -> str:
    h = hashlib.sha256()
    h.update(manifest_json.encode())
    h.update(agent_source)
    return h.hexdigest()[:16]


def build(agent_path: str, output_path: str | None = None) -> str:
    """Build a .kb bundle from an agent file. Returns output path."""
    agent_path = os.path.abspath(agent_path)

    # 1. Parse the agent definition (static analysis, no execution)
    manifest = parse_agent_file(agent_path)

    with open(agent_path, "rb") as f:
        agent_source = f.read()

    # 2. Compute content hash
    manifest_json = manifest.to_json()
    content_hash = compute_hash(manifest_json, agent_source)

    # 3. Compute memory budget
    # Stack: fixed 256KB
    # Heap: max_steps * 64KB (estimated per-step state) + 1MB base
    heap_budget = manifest.max_steps * 65536 + 1048576
    budget = {
        "stack_bytes": 262144,
        "heap_bytes": heap_budget,
        "total_bytes": 262144 + heap_budget,
        "max_fds": 8,
        "max_net_connections": 2,
    }

    # 4. Build the bundle manifest (superset of agent manifest)
    bundle_manifest = {
        "kernl_version": 1,
        "content_hash": content_hash,
        "agent": json.loads(manifest_json),
        "memory_budget": budget,
        "entry_point": "runtime.py",
        "files": ["manifest.json", "agent.py", "runtime.py"],
    }

    # 5. Package into .kb (tar.gz)
    if output_path is None:
        output_path = f"{manifest.name}.kb"

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write manifest
        manifest_path = os.path.join(tmpdir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(bundle_manifest, f, indent=2)

        # Copy agent source
        agent_dst = os.path.join(tmpdir, "agent.py")
        with open(agent_dst, "wb") as f:
            f.write(agent_source)

        # Copy runtime
        runtime_dst = os.path.join(tmpdir, "runtime.py")
        shutil.copy2(RUNTIME_PATH, runtime_dst)

        # Create tar.gz
        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(manifest_path, arcname="manifest.json")
            tar.add(agent_dst, arcname="agent.py")
            tar.add(runtime_dst, arcname="runtime.py")

    size = os.path.getsize(output_path)
    return output_path, size, content_hash, budget


def main():
    if len(sys.argv) < 2:
        print("Usage: kernl build <agent.py> [--output <path>]", file=sys.stderr)
        sys.exit(1)

    agent_path = sys.argv[1]
    output = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        output = sys.argv[idx + 1]

    path, size, hash_, budget = build(agent_path, output)
    print(f"  built: {path}")
    print(f"  size:  {size} bytes ({size / 1024:.1f} KB)")
    print(f"  hash:  {hash_}")
    print(f"  memory budget: {budget['total_bytes'] / 1024:.0f} KB")


if __name__ == "__main__":
    main()
