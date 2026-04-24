#!/usr/bin/env python3
"""
Kernl observability tests — covers the metrics, health(), percentile,
per-agent, request_id, timeline, and anomaly-detection surfaces.

Run:  KERNL_LOG=0 python3 tests/test_observability.py

All tests run in dry-run mode (no API key, no network).
"""
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.pool import WorkerPool
from src.run import get_cached_bundle

KB_PATH = os.path.join(PROJECT_ROOT, "bench.kb")


def _pool(size=2, **kwargs):
    bundle_dir, manifest = get_cached_bundle(KB_PATH)
    with open(os.path.join(bundle_dir, "agent.py")) as f:
        source = f.read()
    pool = WorkerPool(size=size, dry_run=True, **kwargs)
    pool.start()
    return pool, manifest, source


# =========================================================================
# health() shape + score
# =========================================================================

def test_health_shape():
    """health() returns all documented top-level keys."""
    pool, m, s = _pool(size=2)
    try:
        pool.submit(m, {"input_data": "x"}, s)
        h = pool.health()
        for key in ("status", "score", "uptime_s", "workers", "metrics",
                    "percentiles", "per_worker", "per_agent", "timelines",
                    "replacement_reasons", "anomalies", "top_causes",
                    "thresholds", "isolation"):
            assert key in h, f"health() missing key: {key}"
    finally:
        pool.shutdown()


def test_health_score_bounds():
    """Health score stays in [0, 100]."""
    pool, m, s = _pool(size=2)
    try:
        for i in range(4):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        h = pool.health()
        assert 0 <= h["score"] <= 100, f"score out of range: {h['score']}"
        # No errors, all workers alive -> should be max score
        assert h["score"] == 100, f"clean pool should score 100, got {h['score']}"
    finally:
        pool.shutdown()


def test_health_score_degrades_on_errors():
    """Forcing dry_run=False with no API key produces errors; score drops."""
    pool, m, s = _pool(size=2)
    try:
        # Seed some failures
        for i in range(3):
            pool.submit(m, {"input_data": f"r{i}"}, s, dry_run=False)
        h = pool.health()
        assert h["metrics"]["error_total"] >= 1, h["metrics"]
        assert h["score"] < 100, f"expected degraded score, got {h['score']}"
    finally:
        pool.shutdown()


# =========================================================================
# Percentiles
# =========================================================================

def test_percentiles_shape_and_monotonicity():
    """p50 <= p95 <= p99 after several submits."""
    pool, m, s = _pool(size=2)
    try:
        for i in range(20):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        p = pool.health()["percentiles"]
        for bucket in ("agent_ms", "infra_ms", "queue_wait_ms"):
            b = p[bucket]
            assert set(b.keys()) == {"p50", "p95", "p99", "count"}, b
            assert b["p50"] <= b["p95"] <= b["p99"], f"{bucket} not monotonic: {b}"
            assert b["count"] > 0
    finally:
        pool.shutdown()


# =========================================================================
# Per-agent attribution
# =========================================================================

def test_per_agent_stats():
    """per_agent dict keyed by manifest agent name with success_rate."""
    pool, m, s = _pool(size=2)
    try:
        for i in range(4):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        h = pool.health()
        name = m["agent"]["name"]
        assert name in h["per_agent"], h["per_agent"]
        entry = h["per_agent"][name]
        assert entry["requests"] == 4
        assert entry["completions"] == 4
        assert entry["success_rate"] == 1.0
        assert entry["avg_agent_ms"] >= 0
    finally:
        pool.shutdown()


# =========================================================================
# request_id correlation
# =========================================================================

def test_request_id_on_result():
    """Every submit() result carries a request_id."""
    pool, m, s = _pool(size=1)
    try:
        r = pool.submit(m, {"input_data": "x"}, s)
        assert "request_id" in r, r
        assert isinstance(r["request_id"], str) and len(r["request_id"]) >= 8
    finally:
        pool.shutdown()


def test_request_ids_unique():
    """Concurrent submits get distinct request_ids."""
    pool, m, s = _pool(size=2)
    try:
        ids = {pool.submit(m, {"input_data": f"r{i}"}, s)["request_id"]
               for i in range(8)}
        assert len(ids) == 8, f"duplicate request_ids: {ids}"
    finally:
        pool.shutdown()


def test_request_id_propagates_to_timeline():
    """The exec event in a worker's timeline carries the request_id."""
    pool, m, s = _pool(size=1)
    try:
        r = pool.submit(m, {"input_data": "x"}, s)
        rid = r["request_id"]
        h = pool.health()
        # With size=1, worker 0 handled it
        events = h["timelines"]["0"]
        assert any(e["kind"] == "exec" and e.get("request_id") == rid
                   for e in events), events
    finally:
        pool.shutdown()


# =========================================================================
# Timeline events
# =========================================================================

def test_timeline_records_exec():
    """Each submit records an exec event with status and rss_kb."""
    pool, m, s = _pool(size=1)
    try:
        pool.submit(m, {"input_data": "a"}, s)
        pool.submit(m, {"input_data": "b"}, s)
        events = pool.health()["timelines"]["0"]
        execs = [e for e in events if e["kind"] == "exec"]
        assert len(execs) == 2
        for e in execs:
            assert e["status"] == "complete"
            assert e["rss_kb"] > 0
            assert "ts" in e
    finally:
        pool.shutdown()


def test_timeline_capped():
    """Timeline respects timeline_cap and drops oldest."""
    pool, m, s = _pool(size=1, timeline_cap=3)
    try:
        for i in range(6):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        events = pool.health()["timelines"]["0"]
        assert len(events) <= 3, f"timeline not capped: {len(events)}"
    finally:
        pool.shutdown()


def test_timeline_captures_replace():
    """Recycling produces a 'replace' event on the old worker's timeline."""
    pool, m, s = _pool(size=1, max_requests=2)
    try:
        for i in range(4):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        # The replaced worker was id=0; it should have a replace event
        all_events = []
        for tl in pool.health()["timelines"].values():
            all_events.extend(tl)
        replaces = [e for e in all_events if e["kind"] == "replace"]
        assert replaces, "expected at least one replace event"
        assert any(e["reason"] == "max_requests" for e in replaces), replaces
    finally:
        pool.shutdown()


# =========================================================================
# Replacement reason tracing
# =========================================================================

def test_replacement_reason_includes_context():
    """Replace events record rss_kb, request_count, error_count, timeout_streak."""
    pool, m, s = _pool(size=1, max_requests=2)
    try:
        for i in range(3):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        all_events = []
        for tl in pool.health()["timelines"].values():
            all_events.extend(tl)
        replace = next(e for e in all_events if e["kind"] == "replace")
        for key in ("rss_kb", "request_count", "error_count", "timeout_streak"):
            assert key in replace, f"missing {key}: {replace}"
    finally:
        pool.shutdown()


def test_replacement_reasons_tallied():
    """health()['replacement_reasons'] counts reasons across replacements."""
    pool, m, s = _pool(size=1, max_requests=2)
    try:
        for i in range(4):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        reasons = pool.health()["replacement_reasons"]
        assert reasons.get("max_requests", 0) >= 1, reasons
    finally:
        pool.shutdown()


# =========================================================================
# Anomaly detection
# =========================================================================

def test_anomaly_fires_on_replacement_burst():
    """Tight anomaly window + low threshold -> anomaly counter increments."""
    pool, m, s = _pool(
        size=2,
        unhealthy_rss_mb=1,    # any run trips the early-warning threshold
        unhealthy_consecutive=2,
        anomaly_window_s=60.0,
        anomaly_threshold=2,   # 2 replacements in window -> anomaly
    )
    try:
        for i in range(6):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        h = pool.health()
        assert h["anomalies"] >= 1, f"expected anomaly, got {h['anomalies']}"
        assert h["metrics"]["unhealthy_replacements"] >= 2, h["metrics"]
    finally:
        pool.shutdown()


# =========================================================================
# top_causes / summary
# =========================================================================

def test_top_causes_after_replacements():
    """top_causes summary names the dominant replacement reason."""
    pool, m, s = _pool(size=1, max_requests=2)
    try:
        for i in range(4):
            pool.submit(m, {"input_data": f"r{i}"}, s)
        tc = pool.health()["top_causes"]
        assert tc["top_replacement_reason"] == "max_requests", tc
        assert "max_requests" in tc["summary"]
    finally:
        pool.shutdown()


def test_top_causes_empty_when_clean():
    """With no replacements, top_replacement_reason is None."""
    pool, m, s = _pool(size=2)
    try:
        pool.submit(m, {"input_data": "x"}, s)
        tc = pool.health()["top_causes"]
        assert tc["top_replacement_reason"] is None, tc
    finally:
        pool.shutdown()


# =========================================================================
# Runner
# =========================================================================

def _run_all():
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith("test_") and callable(obj)]
    tests.sort(key=lambda t: t[0])

    passed = failed = 0
    failures = []
    print(f"Running {len(tests)} observability tests...\n")

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
        print("\nFailures:")
        for name, err in failures:
            print(f"  {name}: {err}")
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    _run_all()
