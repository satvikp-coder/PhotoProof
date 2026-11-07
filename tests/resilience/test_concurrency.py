"""Concurrency tests: verify multiple processes can safely share the runtime.

Runs real subprocesses performing concurrent analyses and repair operations
against the same ARE/ directory. Verifies no corruption, no failed writes,
and no live run marked aborted.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from photoproof.core.database.store import Database

SHA256_PLACEHOLDER = "a" * 64

ANALYSIS_SCRIPT = r"""
import sys
sys.path.insert(0, %r)
sys.path.insert(0, %r)
from pathlib import Path
from photoproof.core.bootstrap import bootstrap
from photoproof.core.controller import AnalysisController
from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
import json

app_root = Path(%r)
run_id = %r

app = bootstrap(app_root, plugin_package="fixtures.mockplugins.demo", environ={})
try:
    app.storage.ensure_layout()
    app.start()
    target = InputDescriptor(role="target", sha256=%s, size=1)
    result = AnalysisController(app).analyze(
        AnalysisRequest(target=target, use_cache=False)
    )
    print(json.dumps({"run_id": run_id, "status": result.status.value}))
except Exception as e:
    print(json.dumps({"run_id": run_id, "error": repr(e)}))
finally:
    app.stop()
"""

REPAIR_SCRIPT = r"""
import sys
sys.path.insert(0, %r)
sys.path.insert(0, %r)
from pathlib import Path
from photoproof.core.bootstrap import bootstrap
from photoproof.core.health.repair import run_repairs
from photoproof.core.health.checks import default_registry
import json

app_root = Path(%r)

app = bootstrap(app_root, plugin_package="fixtures.mockplugins.demo", environ={})
try:
    registry = default_registry()
    records = run_repairs(app, registry, allow_controlled=True, dry_run=False)
    print(json.dumps([r.to_dict() for r in records]))
except Exception as e:
    print(json.dumps({"error": repr(e)}))
finally:
    app.stop()
"""


def _run_analysis_subprocess(app_root: Path, run_id: str) -> dict:
    """Run an analysis in a subprocess and return the parsed output."""
    script = ANALYSIS_SCRIPT % (
        str(Path(__file__).parent.parent),
        str(Path(__file__).parent.parent / "tests"),
        str(app_root),
        repr(run_id),
        repr(SHA256_PLACEHOLDER),
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        return {"run_id": run_id, "error": f"exit {result.returncode}: {result.stderr}"}
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"run_id": run_id, "error": f"bad output: {result.stdout}"}


def _init_app_root(app_root: Path) -> None:
    """Initialize the database at the latest version."""
    from photoproof.core.bootstrap import bootstrap

    app = bootstrap(app_root, plugin_package="fixtures.mockplugins.demo", environ={})
    app.storage.ensure_layout()
    app.start()
    app.stop()


def test_two_concurrent_analyses_complete(app_root: Path) -> None:
    """Two concurrent analyses of the same input both complete without corruption."""
    _init_app_root(app_root)

    p1 = subprocess.Popen(
        [
            sys.executable,
            "-c",
            ANALYSIS_SCRIPT
            % (
                str(Path(__file__).parent.parent),
                str(Path(__file__).parent.parent / "tests"),
                str(app_root),
                repr("run-1"),
                repr(SHA256_PLACEHOLDER),
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    p2 = subprocess.Popen(
        [
            sys.executable,
            "-c",
            ANALYSIS_SCRIPT
            % (
                str(Path(__file__).parent.parent),
                str(Path(__file__).parent.parent / "tests"),
                str(app_root),
                repr("run-2"),
                repr(SHA256_PLACEHOLDER),
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    p1_stdout, p1_stderr = p1.communicate(timeout=120)
    p2_stdout, p2_stderr = p2.communicate(timeout=120)

    assert p1.returncode == 0, f"Process 1 failed: {p1_stderr}"
    assert p2.returncode == 0, f"Process 2 failed: {p2_stderr}"

    r1 = json.loads(p1_stdout.strip())
    r2 = json.loads(p2_stdout.strip())

    assert "error" not in r1, f"Analysis error: {r1}"
    assert "error" not in r2, f"Analysis error: {r2}"
    assert r1["status"] == "COMPLETED", f"Unexpected status: {r1}"
    assert r2["status"] == "COMPLETED", f"Unexpected status: {r2}"


def test_repair_does_not_mark_live_run_aborted(app_root: Path) -> None:
    """A repair running concurrently with a live analysis never marks it aborted."""
    _init_app_root(app_root)

    # Create a run directly in the database with a live PID
    db_path = app_root / "ARE" / "database" / "photoproof.db"
    db = Database.open(db_path, allow_migration=True)
    try:
        db.create_run(
            "live-run-1",
            mode="single",
            profile="default",
            cache_key=None,
            pid=os.getpid(),  # current process is alive
            hostname="test-host",
        )
    finally:
        db.close()

    # Run repair in a subprocess
    script = REPAIR_SCRIPT % (
        str(Path(__file__).parent.parent),
        str(Path(__file__).parent.parent / "tests"),
        str(app_root),
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"Repair failed: {proc.stderr}"

    # Verify the live run was NOT marked aborted
    db = Database.open(db_path)
    try:
        run = db.get_run("live-run-1")
        assert run is not None, "Live run should exist"
        assert run["status"] == "CREATED", f"Live run should not be aborted: {run}"
    finally:
        db.close()


def test_concurrent_analyses_no_corrupted_blobs(app_root: Path) -> None:
    """Concurrent analyses don't produce corrupted content-addressed blobs."""
    _init_app_root(app_root)

    procs = []
    for i in range(3):
        p = subprocess.Popen(
            [
                sys.executable,
                "-c",
                ANALYSIS_SCRIPT
                % (
                    str(Path(__file__).parent.parent),
                    str(Path(__file__).parent.parent / "tests"),
                    str(app_root),
                    repr(f"run-{i}"),
                    repr(SHA256_PLACEHOLDER),
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        procs.append(p)

    for p in procs:
        _p_stdout, p_stderr = p.communicate(timeout=120)
        assert p.returncode == 0, f"Process failed: {p_stderr}"

    # Verify all blobs are intact
    db_path = app_root / "ARE" / "database" / "photoproof.db"
    db = Database.open(db_path)
    try:
        for sha256 in db.evidence_hashes():
            for kind in ("objects", "artifacts"):
                blob_path = app_root / "ARE" / kind / sha256[:2] / sha256
                if blob_path.exists():
                    actual = hashlib.sha256(blob_path.read_bytes()).hexdigest()
                    assert actual == sha256, f"Blob {sha256} is corrupted"
    finally:
        db.close()
