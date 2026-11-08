"""Tests for photoproof.core.pipeline.manifest_tools (#48)."""

from photoproof.core.pipeline.manifest_tools import (
    diff_manifests,
    fingerprint_matches,
    manifest_graph,
    manifest_timeline,
)


def _manifest(
    run_id: str,
    status: str = "COMPLETED",
    executions: dict | None = None,
    settings: dict | None = None,
    dependency_graph: dict | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "status": status,
        "mode": "single",
        "profile": "default",
        "computational_fingerprint": f"fp-{run_id}",
        "executions": executions or {},
        "effective_settings": settings or {},
        "dependency_graph": dependency_graph or {},
        "execution_order": [],
        "outputs": {},
    }


def test_fingerprint_matches_identical() -> None:
    a = _manifest("run-a")
    b = _manifest("run-a")
    b["computational_fingerprint"] = a["computational_fingerprint"]
    assert fingerprint_matches(a, b)


def test_fingerprint_matches_different() -> None:
    a = _manifest("run-a")
    b = _manifest("run-b")
    assert not fingerprint_matches(a, b)


def test_diff_identical_manifests_empty() -> None:
    m = _manifest("run-x")
    m2 = dict(m)
    assert diff_manifests(m, m2) == {}


def test_diff_identity_change() -> None:
    a = _manifest("run-a", status="COMPLETED")
    b = _manifest("run-b", status="BLOCKED")
    diffs = diff_manifests(a, b)
    assert "identity" in diffs
    assert "status" in diffs["identity"]


def test_diff_settings_change() -> None:
    a = _manifest("run-a", settings={"foo": 1})
    b = _manifest("run-b", settings={"foo": 2})
    diffs = diff_manifests(a, b)
    assert "configuration" in diffs


def test_diff_state_change() -> None:
    a = _manifest(
        "run-a",
        executions={"p1": {"state": "completed"}},
    )
    b = _manifest(
        "run-b",
        executions={"p1": {"state": "failed"}},
    )
    diffs = diff_manifests(a, b)
    assert "states" in diffs
    assert "executions" in diffs["states"]
    assert diffs["states"]["executions"]["p1"] == ("completed", "failed")


def test_graph_mermaid_output() -> None:
    m = _manifest(
        "run-g",
        executions={
            "p1": {"state": "completed"},
            "p2": {"state": "failed"},
        },
        dependency_graph={"p2": {"p1"}},
    )
    graph = manifest_graph(m, format="mermaid")
    assert "flowchart LR" in graph
    assert "p1" in graph
    assert "p2" in graph
    assert "completed" in graph
    assert "failed" in graph


def test_graph_dot_output() -> None:
    m = _manifest(
        "run-g",
        executions={"p1": {"state": "completed"}},
    )
    graph = manifest_graph(m, format="dot")
    assert "digraph G" in graph
    assert "p1" in graph


def test_timeline_sorted() -> None:
    started_p1 = "2026-01-01T00:00:00Z"
    finished_p1 = "2026-01-01T00:00:03Z"
    started_p2 = "2026-01-02T00:00:00Z"
    finished_p2 = "2026-01-02T00:00:05Z"
    m = _manifest(
        "run-t",
        executions={
            "p2": {
                "state": "completed",
                "started_at": started_p2,
                "finished_at": finished_p2,
            },
            "p1": {
                "state": "completed",
                "started_at": started_p1,
                "finished_at": finished_p1,
            },
        },
    )
    rows = manifest_timeline(m)
    assert len(rows) == 2
    assert rows[0]["plugin_id"] == "p1"
    assert rows[1]["plugin_id"] == "p2"
    assert rows[0]["duration_seconds"] == 3.0
    assert rows[1]["duration_seconds"] == 5.0


def test_timeline_untimed_last() -> None:
    m = _manifest(
        "run-t",
        executions={
            "p1": {"state": "completed"},
            "p2": {
                "state": "completed",
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:00:03Z",
            },
        },
    )
    rows = manifest_timeline(m)
    assert rows[0]["plugin_id"] == "p2"
    assert rows[1]["plugin_id"] == "p1"
