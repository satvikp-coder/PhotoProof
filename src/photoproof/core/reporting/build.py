"""Build a :class:`~photoproof.core.reporting.model.Report` from an execution manifest.

The build is deterministic: the same manifest always produces the same report.
"""

from __future__ import annotations

from typing import Any

from photoproof.core.reporting.model import (
    Report,
    ReportFinding,
    ReportInput,
    ReportMethodology,
    ReportPluginExecution,
    ReportReproducibility,
    ReportSummary,
    ReportUnavailableEvidence,
)


def _status_to_assessment(status: str, executions: dict[str, dict[str, Any]]) -> str:
    """Map a run status to an assessment category."""
    if status == "BLOCKED":
        return "blocked"
    if status == "NO_DETECTORS":
        return "no_detectors"
    if status == "ABORTED":
        return "inconclusive"
    if status == "INCOMPLETE":
        return "inconclusive"
    if status == "COMPLETED":
        # Inspect execution states to refine the assessment
        states = {ex.get("state") for ex in executions.values()}
        if "failed" in states:
            return "inconclusive"
        if "skipped" in states:
            return "suspected_manipulation"
        return "authentic"
    return "inconclusive"


def _build_summary(
    status: str,
    executions: dict[str, dict[str, Any]],
    findings: list[ReportFinding],
    unavailable: list[ReportUnavailableEvidence],
) -> ReportSummary:
    """Build the top-line assessment summary."""
    states = [ex.get("state") for ex in executions.values()]
    assessment = _status_to_assessment(status, executions)
    return ReportSummary(
        assessment=assessment,
        reasons=_reasons_for(status, assessment, executions, findings, unavailable),
        detectors_run=len(executions),
        detectors_completed=states.count("completed"),
        detectors_failed=states.count("failed"),
        detectors_skipped=states.count("skipped"),
        findings_suspected=sum(1 for f in findings if f.category == "suspected_manipulation"),
        findings_confirmed=sum(1 for f in findings if f.category == "confirmed_difference"),
        evidence_unavailable=len(unavailable),
    )


def _reasons_for(
    status: str,
    assessment: str,
    executions: dict[str, dict[str, Any]],
    findings: list[ReportFinding],
    unavailable: list[ReportUnavailableEvidence],
) -> list[str]:
    """Human-readable reasons for the assessment."""
    reasons: list[str] = []
    if status == "NO_DETECTORS":
        reasons.append("No forensic detectors are installed and enabled.")
        reasons.append("This is not evidence of authenticity.")
    elif status == "BLOCKED":
        reasons.append("The run was blocked before analysis could complete.")
    elif status == "ABORTED":
        reasons.append("The run was aborted due to an unrecoverable error.")
    elif status == "INCOMPLETE":
        reasons.append("The run did not complete; some plugins were skipped or failed.")
    elif status == "COMPLETED":
        failed = [pid for pid, ex in executions.items() if ex.get("state") == "failed"]
        skipped = [pid for pid, ex in executions.items() if ex.get("state") == "skipped"]
        if failed:
            reasons.append(f"{len(failed)} detector(s) failed: {', '.join(failed)}")
        if skipped:
            reasons.append(f"{len(skipped)} detector(s) skipped: {', '.join(skipped)}")
        suspected = sum(1 for f in findings if f.category == "suspected_manipulation")
        confirmed = sum(1 for f in findings if f.category == "confirmed_difference")
        if confirmed:
            reasons.append(f"{confirmed} confirmed difference(s) detected.")
        elif suspected:
            reasons.append(f"{suspected} suspected manipulation(s) detected.")
        else:
            reasons.append("No manipulation signals detected.")
    return reasons


def _build_inputs(
    manifest_inputs: list[dict[str, Any]],
) -> list[ReportInput]:
    """Build input descriptions from manifest input records."""
    inputs: list[ReportInput] = []
    for item in manifest_inputs:
        inputs.append(
            ReportInput(
                path=item.get("path", ""),
                sha256=item.get("sha256", ""),
                size_bytes=item.get("size_bytes", 0),
                mime_type=item.get("mime_type"),
            )
        )
    return inputs


def _build_methodology(
    executions: dict[str, dict[str, Any]],
    config: dict[str, Any],
    pipeline_fingerprint: str,
) -> ReportMethodology:
    """Build the methodology section from plugin executions."""
    plugins: list[ReportPluginExecution] = []
    for pid, ex in executions.items():
        plugins.append(
            ReportPluginExecution(
                plugin_id=pid,
                state=ex.get("state", "unknown"),
                failure_category=ex.get("failure_category"),
                message=ex.get("message"),
                config=ex.get("config", {}) or {},
                summary=ex.get("summary", {}) or {},
            )
        )
    return ReportMethodology(
        pipeline_fingerprint=pipeline_fingerprint,
        detectors=plugins,
        configuration=config,
    )


def _build_findings(
    executions: dict[str, dict[str, Any]],
    artifacts: list[dict[str, Any]],
    blocked_reasons: list[str],
    status: str,
) -> list[ReportFinding]:
    """Build findings from executions, artifacts, and blocked reasons."""
    findings: list[ReportFinding] = []

    # Suspected manipulation from failed plugins
    for pid, ex in executions.items():
        state = ex.get("state", "unknown")
        if state == "failed":
            findings.append(
                ReportFinding(
                    category="suspected_manipulation",
                    title=f"Detector {pid} failed",
                    description=ex.get("message") or "Detector raised an error during execution.",
                    severity="medium",
                    manifest_path=["executions", pid],
                    artifact_sha256=[],
                    detector=pid,
                )
            )
        elif state == "skipped":
            findings.append(
                ReportFinding(
                    category="suspected_manipulation",
                    title=f"Detector {pid} skipped",
                    description=(
                        f"Detector {pid} was skipped: "
                        f"{ex.get('message') or 'dependency unavailable'}"
                    ),
                    severity="low",
                    manifest_path=["executions", pid],
                    artifact_sha256=[],
                    detector=pid,
                )
            )

    # Confirmed differences from artifacts with anomaly scores
    for artifact in artifacts:
        if artifact.get("anomaly_score", 0) > 0.5:
            findings.append(
                ReportFinding(
                    category="confirmed_difference",
                    title=f"Anomaly detected in {artifact.get('plugin_id', 'unknown')}",
                    description=artifact.get("description", "Anomalous region detected."),
                    severity="high",
                    manifest_path=["artifacts"],
                    artifact_sha256=[artifact.get("sha256", "")],
                    detector=artifact.get("plugin_id"),
                )
            )

    # Unavailable evidence from blocked reasons
    for _i, reason in enumerate(blocked_reasons):
        findings.append(
            ReportFinding(
                category="unavailable_evidence",
                title=f"Blocked: {reason[:60]}",
                description=reason,
                severity="medium",
                manifest_path=["blocked_reasons"],
                artifact_sha256=[],
                detector=None,
            )
        )

    return findings


def _build_unavailable_evidence(
    executions: dict[str, dict[str, Any]],
    blocked_reasons: list[str],
) -> list[ReportUnavailableEvidence]:
    """Build the unavailable evidence list."""
    unavailable: list[ReportUnavailableEvidence] = []

    for pid, ex in executions.items():
        state = ex.get("state", "unknown")
        if state in {"skipped", "dependency_unavailable"}:
            unavailable.append(
                ReportUnavailableEvidence(
                    reason=f"Detector {pid}: {ex.get('message') or 'dependency unavailable'}",
                    affected=[pid],
                )
            )

    for reason in blocked_reasons:
        unavailable.append(ReportUnavailableEvidence(reason=reason, affected=[]))

    return unavailable


def build_report(manifest: dict[str, Any]) -> Report:
    """Build a :class:`Report` from an execution manifest.

    The build is deterministic: the same manifest always produces the same
    report.  Every finding references manifest paths and/or artifact hashes so
    a reviewer can verify it independently.

    Parameters
    ----------
    manifest:
        The execution manifest dict as written by :class:`AnalysisController`.

    Returns
    -------
    Report
        The structured forensic report.

    Raises
    ------
    ValueError
        If the manifest is missing required fields.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a dict")
    if "run_id" not in manifest:
        raise ValueError("manifest missing required field: run_id")

    run_id = manifest["run_id"]
    status = manifest.get("status", "UNKNOWN")
    executions = manifest.get("executions", {}) or {}
    artifacts = manifest.get("artifacts", []) or []
    blocked_reasons = manifest.get("blocked_reasons", []) or []
    inputs = manifest.get("inputs", []) or []

    findings = _build_findings(executions, artifacts, blocked_reasons, status)
    unavailable = _build_unavailable_evidence(executions, blocked_reasons)
    summary = _build_summary(status, executions, findings, unavailable)

    return Report(
        run_id=run_id,
        status=status,
        summary=summary,
        inputs=_build_inputs(inputs),
        methodology=_build_methodology(
            executions,
            manifest.get("effective_settings", {}) or {},
            manifest.get("pipeline_fingerprint", ""),
        ),
        findings=findings,
        unavailable_evidence=unavailable,
        limitations=_limitations_for(status, executions),
        reproducibility=ReportReproducibility(
            application_version=manifest.get("application", {}).get("version", ""),
            plugin_api_version=manifest.get("application", {}).get("plugin_api_version", ""),
            pipeline_fingerprint=manifest.get("pipeline_fingerprint", ""),
            random_seed=manifest.get("random_seed"),
            computational_fingerprint=manifest.get("computational_fingerprint"),
        ),
        appendix={
            "manifest_summary": {
                "run_id": run_id,
                "status": status,
                "profile": manifest.get("profile"),
                "created_at": manifest.get("created_at"),
                "finished_at": manifest.get("finished_at"),
                "num_executions": len(executions),
                "num_artifacts": len(artifacts),
                "num_blocked_reasons": len(blocked_reasons),
            }
        },
    )


def _limitations_for(status: str, executions: dict[str, Any]) -> list[str]:
    """List limitations of the analysis."""
    limitations: list[str] = []
    if status == "NO_DETECTORS":
        limitations.append(
            "No forensic detectors are installed; no manipulation signals can be "
            "evaluated. This is not evidence of authenticity."
        )
    failed = [pid for pid, ex in executions.items() if ex.get("state") == "failed"]
    if failed:
        limitations.append(
            f"{len(failed)} detector(s) failed; their results are unavailable: " + ", ".join(failed)
        )
    skipped = [pid for pid, ex in executions.items() if ex.get("state") == "skipped"]
    if skipped:
        limitations.append(
            f"{len(skipped)} detector(s) skipped due to missing dependencies: " + ", ".join(skipped)
        )
    limitations.append(
        "A heuristic score is not a calibrated probability; results are indicative, not conclusive."
    )
    return limitations
