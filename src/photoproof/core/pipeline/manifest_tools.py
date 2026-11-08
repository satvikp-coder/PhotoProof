"""Manifest analysis tools: diff, dependency graph, and timeline.

Pure functions for inspecting execution manifests. Used by the `manifest` CLI
command and (later) WebKit pages.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Categories that the diff groups changes into.
_DIFF_CATEGORIES = (
    "identity",
    "inputs",
    "configuration",
    "plugins",
    "graph",
    "states",
    "outputs",
)


def _section(manifest: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Extract a nested section from a manifest by dotted keys."""
    result: dict[str, Any] = {}
    for key in keys:
        parts = key.split(".")
        value: Any = manifest
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        result[key] = value
    return result


def diff_manifests(a: dict[str, Any], b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the differences between two manifests, grouped by category.

    Each category maps field names to ``(value_in_a, value_in_b)`` tuples.
    If the computational fingerprints match, the result is empty (the
    computations are identical).
    """
    fp_a = a.get("computational_fingerprint")
    fp_b = b.get("computational_fingerprint")
    if fp_a == fp_b:
        return {}

    diffs: dict[str, dict[str, Any]] = {cat: {} for cat in _DIFF_CATEGORIES}

    # Identity: run_id, status, mode, profile
    for key in ("run_id", "status", "mode", "profile"):
        va, vb = a.get(key), b.get(key)
        if va != vb:
            diffs["identity"][key] = (va, vb)

    # Inputs
    ia, ib = a.get("inputs", []), b.get("inputs", [])
    if ia != ib:
        diffs["inputs"]["inputs"] = (ia, ib)

    # Configuration: effective_settings, feature_flags, policy, blacklist_override
    for key in ("effective_settings", "feature_flags", "policy", "blacklist_override"):
        va, vb = a.get(key), b.get(key)
        if va != vb:
            diffs["configuration"][key] = (va, vb)

    # Plugins: registered plugin set and metadata
    pa, pb = a.get("plugins", {}), b.get("plugins", {})
    if pa != pb:
        diffs["plugins"]["plugins"] = (pa, pb)

    # Graph: dependency_graph and execution_order
    ga, gb = a.get("dependency_graph", {}), b.get("dependency_graph", {})
    oa, ob = a.get("execution_order", []), b.get("execution_order", [])
    if ga != gb:
        diffs["graph"]["dependency_graph"] = (ga, gb)
    if oa != ob:
        diffs["graph"]["execution_order"] = (oa, ob)

    # States: per-plugin execution states
    ea, eb = a.get("executions", {}), b.get("executions", {})
    state_diff: dict[str, tuple[str | None, str | None]] = {}
    all_pids = set(ea) | set(eb)
    for pid in sorted(all_pids):
        sa = ea.get(pid, {}).get("state") if pid in ea else None
        sb = eb.get(pid, {}).get("state") if pid in eb else None
        if sa != sb:
            state_diff[pid] = (sa, sb)
    if state_diff:
        diffs["states"]["executions"] = state_diff

    # Outputs
    out_a, out_b = a.get("outputs", {}), b.get("outputs", {})
    if out_a != out_b:
        diffs["outputs"]["outputs"] = (out_a, out_b)

    # Remove empty categories
    return {k: v for k, v in diffs.items() if v}


def fingerprint_matches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return True if the two manifests are computationally identical."""
    return a.get("computational_fingerprint") == b.get("computational_fingerprint")


def manifest_graph(manifest: dict[str, Any], *, format: str = "mermaid") -> str:
    """Render the dependency graph as Mermaid or DOT.

    Nodes are plugins; edges follow ``requires`` / ``after`` declarations.
    Node labels include the execution state.
    """
    executions = manifest.get("executions", {}) or {}
    graph = manifest.get("dependency_graph", {}) or {}

    if format == "dot":
        lines = ["digraph G {", "  rankdir=LR;", "  node [shape=box];"]
        for pid, ex in executions.items():
            state = ex.get("state", "unknown")
            label = f"{pid}\\n{state}"
            lines.append(f'  "{pid}" [label="{label}"];')
        # Edges from dependency graph
        for pid, deps in graph.items():
            if isinstance(deps, dict):
                for dep in deps:
                    lines.append(f'  "{dep}" -> "{pid}";')
        lines.append("}")
        return "\n".join(lines)

    # Mermaid (default)
    lines = [
        "flowchart LR",
        "  classDef completed fill:#dcfce7,stroke:#166534;",
        "  classDef failed fill:#fee2e2,stroke:#991b1b;",
        "  classDef skipped fill:#fef3c7,stroke:#92400e;",
    ]
    for pid, ex in executions.items():
        state = ex.get("state", "unknown")
        cls = f"class {pid} {state};"
        lines.append(f'    {pid}["{pid}\\n{state}"]')
        lines.append(cls)
    for pid, deps in graph.items():
        if isinstance(deps, dict):
            for dep in deps:
                lines.append(f"    {dep} --> {pid}")
    return "\n".join(lines)


def manifest_timeline(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a per-plugin timeline: start, duration, state.

    Sorted by started_at. Plugins without timing info are listed last.
    """
    executions = manifest.get("executions", {}) or {}
    rows: list[dict[str, Any]] = []
    for pid, ex in executions.items():
        started = ex.get("started_at")
        finished = ex.get("finished_at")
        duration = None
        if started and finished:
            try:
                t0 = datetime.fromisoformat(started)
                t1 = datetime.fromisoformat(finished)
                duration = (t1 - t0).total_seconds()
            except ValueError:
                duration = None
        rows.append(
            {
                "plugin_id": pid,
                "state": ex.get("state", "unknown"),
                "started_at": started,
                "finished_at": finished,
                "duration_seconds": duration,
            }
        )
    # Sort: timed rows first by start time, then untimed rows
    timed = [r for r in rows if r["started_at"]]
    untimed = [r for r in rows if not r["started_at"]]
    timed.sort(key=lambda r: r["started_at"] or "")
    return timed + untimed
