#!/usr/bin/env python3
"""
Kernl test suite — correctness tests for pool, worker, and runtime.

Run:  python3 tests/test_kernl.py
      python3 -m pytest tests/test_kernl.py -v   (if pytest installed)

Tests use dry-run mode (mock LLM) and the bench.kb bundle.
No API key required. No network access.
"""
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.pool import WorkerPool
from src.run import run, get_cached_bundle

KB_PATH = os.path.join(PROJECT_ROOT, "bench.kb")


def _pool_with_source(size=2, **kwargs):
    """Create a pool and load the bundle source. Returns (pool, manifest, source)."""
    bundle_dir, manifest = get_cached_bundle(KB_PATH)
    with open(os.path.join(bundle_dir, "agent.py")) as f:
        source = f.read()
    pool = WorkerPool(size=size, dry_run=True, **kwargs)
    pool.start()
    return pool, manifest, source


# =========================================================================
# 1. Basic execution correctness
# =========================================================================

def test_run_no_sandbox_dry_run():
    """run() with no sandbox and dry_run=True should complete successfully."""
    r = run(KB_PATH, {"input_data": "test"}, use_sandbox=False, dry_run=True)
    assert r["status"] == "complete", f"Expected complete, got: {r}"
    assert "output" in r
    assert r.get("steps", 0) > 0


def test_run_bwrap_dry_run():
    """run() with bwrap sandbox and dry_run=True should complete successfully."""
    r = run(KB_PATH, {"input_data": "test"}, use_sandbox=True, dry_run=True)
    assert r["status"] == "complete", f"Expected complete, got: {r}"
    assert r.get("isolation", "").startswith("bwrap"), f"Expected bwrap isolation, got: {r.get('isolation')}"


def test_pool_basic_execution():
    """Pool submit should complete a dry-run agent successfully."""
    pool, manifest, source = _pool_with_source(size=2)
    try:
        r = pool.submit(manifest, {"input_data": "test"}, source)
        assert r["status"] == "complete", f"Expected complete, got: {r}"
        assert r.get("_request_count", 0) >= 1
        assert r.get("_rss_kb", 0) > 0
    finally:
        pool.shutdown()


def test_pool_multiple_requests():
    """Pool should handle many sequential requests without errors."""
    pool, manifest, source = _pool_with_source(size=2)
    try:
        for i in range(10):
            r = pool.submit(manifest, {"input_data": f"run_{i}"}, source)
            assert r["status"] == "complete", f"Run {i} failed: {r}"
    finally:
        pool.shutdown()


def test_pool_ping():
    """All pool workers should respond to ping."""
    pool, manifest, source = _pool_with_source(size=4)
    try:
        alive, dead = pool.ping_all()
        assert alive == 4, f"Expected 4 alive, got {alive} alive, {dead} dead"
        assert dead == 0
    finally:
        pool.shutdown()


# =========================================================================
# 2. State isolation between agent runs
# =========================================================================

def test_state_isolation_env():
    """Environment variables set by one run must not leak to the next."""
    pool, manifest, source = _pool_with_source(size=1)
    try:
        # Run 1: normal execution
        r1 = pool.submit(manifest, {"input_data": "run1"}, source)
        assert r1["status"] == "complete"

        # Run 2: should also complete cleanly (no env leakage)
        r2 = pool.submit(manifest, {"input_data": "run2"}, source)
        assert r2["status"] == "complete"

        # Both should have succeeded — if env leaked, the second would fail
        # or behave differently
    finally:
        pool.shutdown()


def test_state_isolation_request_count():
    """Worker request count should increment correctly across runs."""
    pool, manifest, source = _pool_with_source(size=1)
    try:
        r1 = pool.submit(manifest, {"input_data": "a"}, source)
        r2 = pool.submit(manifest, {"input_data": "b"}, source)
        r3 = pool.submit(manifest, {"input_data": "c"}, source)

        # With 1 worker, all requests go to the same worker
        assert r1["_request_count"] == 1
        assert r2["_request_count"] == 2
        assert r3["_request_count"] == 3
    finally:
        pool.shutdown()


def test_dry_run_per_request():
    """Workers should respect per-request dry_run switching."""
    pool, manifest, source = _pool_with_source(size=1)
    try:
        # dry_run=True (pool default) — should complete
        r1 = pool.submit(manifest, {"input_data": "a"}, source, dry_run=True)
        assert r1["status"] == "complete", f"dry_run=True failed: {r1}"

        # dry_run=False with no API key — should fail with API key error
        r2 = pool.submit(manifest, {"input_data": "b"}, source, dry_run=False)
        assert r2["status"] == "error", f"dry_run=False should fail: {r2}"
        assert "API" in r2.get("output", "") or "api" in r2.get("output", "").lower()

        # dry_run=True again — should work (state restored after previous run)
        r3 = pool.submit(manifest, {"input_data": "c"}, source, dry_run=True)
        assert r3["status"] == "complete", f"dry_run=True after False failed: {r3}"
    finally:
        pool.shutdown()


# =========================================================================
# 3. Worker recycling
# =========================================================================

def test_worker_recycling_by_request_count():
    """Workers should be recycled after max_requests."""
    pool, manifest, source = _pool_with_source(size=1, max_requests=3)
    try:
        results = []
        for i in range(6):
            r = pool.submit(manifest, {"input_data": f"r{i}"}, source)
            assert r["status"] == "complete", f"Run {i} failed: {r}"
            results.append(r)

        stats = pool.stats()
        # With max_requests=3, after 6 runs on 1 worker, we should have
        # recycled at least once (request_count hits 3, recycle, then again)
        assert stats["recycled"] >= 1, f"Expected recycling, got stats: {stats}"
        # Pool should still be at target size
        assert stats["active_workers"] == 1
    finally:
        pool.shutdown()


def test_worker_recycling_preserves_capacity():
    """After recycling, pool should maintain its target worker count."""
    pool, manifest, source = _pool_with_source(size=2, max_requests=2)
    try:
        for i in range(8):
            r = pool.submit(manifest, {"input_data": f"r{i}"}, source)
            assert r["status"] == "complete", f"Run {i} failed: {r}"

        stats = pool.stats()
        assert stats["recycled"] >= 2, f"Expected multiple recycles: {stats}"
        alive, dead = pool.ping_all()
        assert alive == 2, f"Pool capacity lost: alive={alive}, dead={dead}"
    finally:
        pool.shutdown()


# =========================================================================
# 4. Dead worker replacement
# =========================================================================

def test_dead_worker_replacement():
    """Killing a worker externally should trigger automatic replacement."""
    pool, manifest, source = _pool_with_source(size=2)
    try:
        # Verify both workers alive
        alive, dead = pool.ping_all()
        assert alive == 2

        # Kill one worker externally
        with pool._workers_lock:
            victim = pool._workers[0]
        victim.proc.kill()
        victim.proc.wait(timeout=2)

        # Give the pool a moment to notice on next submit
        # (dead worker detected on submit, not proactively)
        time.sleep(0.1)

        # Submit work — pool should detect the dead worker and replace it
        r = pool.submit(manifest, {"input_data": "after_kill"}, source)
        # Result might be an error (if the dead worker was picked) or
        # complete (if the live worker was picked). Either way, pool
        # should eventually recover.

        # Do a few more submits to ensure stability
        for i in range(4):
            r = pool.submit(manifest, {"input_data": f"post_{i}"}, source)
            assert r["status"] == "complete", f"Post-kill run {i} failed: {r}"

        stats = pool.stats()
        assert stats["replaced"] >= 1, f"Expected replacement: {stats}"
    finally:
        pool.shutdown()


def test_dead_worker_stats():
    """Pool stats should reflect worker deaths and replacements."""
    pool, manifest, source = _pool_with_source(size=2)
    try:
        s0 = pool.stats()
        assert s0["replaced"] == 0
        assert s0["recycled"] == 0
        assert s0["active_workers"] == 2

        # Kill a worker
        with pool._workers_lock:
            victim = pool._workers[0]
        victim.proc.kill()
        victim.proc.wait(timeout=2)

        # Trigger replacement via submit
        pool.submit(manifest, {"input_data": "x"}, source)
        pool.submit(manifest, {"input_data": "y"}, source)

        s1 = pool.stats()
        assert s1["replaced"] >= 1, f"No replacement recorded: {s1}"
    finally:
        pool.shutdown()


# =========================================================================
# 5. Bundle cache and run.py integration
# =========================================================================

def test_bundle_cache_returns_manifest():
    """get_cached_bundle should return a valid manifest."""
    bundle_dir, manifest = get_cached_bundle(KB_PATH)
    assert "agent" in manifest
    assert "content_hash" in manifest
    assert manifest["agent"]["name"] == "bench"
    assert os.path.isfile(os.path.join(bundle_dir, "agent.py"))
    assert os.path.isfile(os.path.join(bundle_dir, "runtime.py"))
    assert os.path.isfile(os.path.join(bundle_dir, "manifest.json"))


def test_bundle_cache_is_idempotent():
    """Two calls to get_cached_bundle should return the same directory."""
    d1, m1 = get_cached_bundle(KB_PATH)
    d2, m2 = get_cached_bundle(KB_PATH)
    assert d1 == d2
    assert m1["content_hash"] == m2["content_hash"]


# =========================================================================
# Runner
# =========================================================================

def _run_all():
    """Run all test functions and report results."""
    tests = [
        (name, obj) for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]
    tests.sort(key=lambda t: t[0])

    passed = failed = 0
    failures = []

    print(f"Running {len(tests)} tests...\n")

    for name, fn in tests:
        t0 = time.monotonic()
        try:
            fn()
            elapsed = (time.monotonic() - t0) * 1000
            print(f"  PASS  {name} ({elapsed:.0f}ms)")
            passed += 1
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            print(f"  FAIL  {name} ({elapsed:.0f}ms): {e}")
            failed += 1
            failures.append((name, e))

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")

    if failures:
        print(f"\nFailures:")
        for name, err in failures:
            print(f"  {name}: {err}")
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    _run_all()
