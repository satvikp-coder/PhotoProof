"""Tests for photoproof.core.reporting."""

from photoproof.core.reporting import build_report, render_html, render_json
from photoproof.core.reporting.model import Report


def _sample_manifest() -> dict:
    return {
        "run_id": "run-123",
        "status": "COMPLETED",
        "profile": "default",
        "created_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "application": {
            "name": "photoproof",
            "version": "0.8.0",
            "plugin_api_version": "1.0.0",
        },
        "pipeline_fingerprint": "abc123",
        "computational_fingerprint": "def456",
        "random_seed": 42,
        "effective_settings": {"foo": "bar"},
        "inputs": [
            {
                "path": "tests/fixtures/target.jpg",
                "sha256": "aaaa",
                "size_bytes": 1024,
                "mime_type": "image/jpeg",
            }
        ],
        "executions": {
            "copymove.sift_keypoints": {
                "state": "completed",
                "failure_category": None,
                "message": None,
                "config": {"max_keypoints": 20000},
                "summary": {"num_keypoints": 100},
            },
            "copymove.matching": {
                "state": "failed",
                "failure_category": "timeout",
                "message": "keypoint matching exceeded time limit",
                "config": {},
                "summary": {},
            },
        },
        "artifacts": [
            {
                "plugin_id": "copymove.matching",
                "sha256": "deadbeef",
                "anomaly_score": 0.8,
                "description": "Copy-move region detected",
            }
        ],
        "blocked_reasons": ["plugin copymove.matching timed out"],
    }


def test_build_report_from_manifest() -> None:
    """build_report produces a Report from a manifest."""
    manifest = _sample_manifest()
    report = build_report(manifest)
    assert isinstance(report, Report)
    assert report.report_version == 1
    assert report.run_id == "run-123"
    assert report.status == "COMPLETED"


def test_build_report_summary() -> None:
    """The summary reflects execution states."""
    manifest = _sample_manifest()
    report = build_report(manifest)
    assert report.summary.detectors_run == 2
    assert report.summary.detectors_completed == 1
    assert report.summary.detectors_failed == 1
    assert report.summary.findings_suspected >= 1
    assert report.summary.evidence_unavailable >= 1


def test_findings_have_traceability() -> None:
    """Every finding references manifest paths and/or artifact hashes."""
    manifest = _sample_manifest()
    report = build_report(manifest)
    for finding in report.findings:
        assert finding.manifest_path or finding.artifact_sha256, (
            f"finding {finding.title} has no traceability"
        )


def test_findings_by_category() -> None:
    """Findings are grouped into separate sections."""
    manifest = _sample_manifest()
    report = build_report(manifest)
    grouped = report.findings_by_category
    assert "suspected_manipulation" in grouped
    assert "confirmed_difference" in grouped
    assert "unavailable_evidence" in grouped


def test_render_html_deterministic() -> None:
    """Rendering the same report twice gives the same HTML."""
    manifest = _sample_manifest()
    report = build_report(manifest)
    html1 = render_html(report)
    html2 = render_html(report)
    assert html1 == html2
    assert "run-123" in html1
    assert "PhotoProof Forensic Report" in html1


def test_render_json_deterministic() -> None:
    """Rendering the same report twice gives the same JSON."""
    manifest = _sample_manifest()
    report = build_report(manifest)
    json1 = render_json(report)
    json2 = render_json(report)
    assert json1 == json2
    assert '"run_id": "run-123"' in json1


def test_report_no_detectors() -> None:
    """A NO_DETECTORS run produces a valid report stating no verdict."""
    manifest = _sample_manifest()
    manifest["status"] = "NO_DETECTORS"
    manifest["executions"] = {}
    manifest["artifacts"] = []
    manifest["blocked_reasons"] = []
    report = build_report(manifest)
    assert report.summary.assessment == "no_detectors"
    assert "no forensic detectors" in " ".join(report.summary.reasons).lower()


def test_report_blocked() -> None:
    """A BLOCKED run produces a valid blocked report."""
    manifest = _sample_manifest()
    manifest["status"] = "BLOCKED"
    manifest["executions"] = {}
    manifest["artifacts"] = []
    manifest["blocked_reasons"] = ["preflight failed: missing decoders"]
    report = build_report(manifest)
    assert report.summary.assessment == "blocked"
