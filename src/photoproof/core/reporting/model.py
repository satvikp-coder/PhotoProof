"""Versioned forensic report data model.

A :class:`Report` is built deterministically from an execution manifest and
separates findings into **suspected manipulation**, **confirmed differences** and
**unavailable evidence**, with every claim traceable to manifest fields and
artifact hashes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

REPORT_VERSION = 1


class ReportInput(BaseModel):
    """Describes one analysis input (target or reference image)."""

    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str
    size_bytes: int
    mime_type: str | None = None


class ReportPluginExecution(BaseModel):
    """One plugin execution within a run."""

    model_config = ConfigDict(frozen=True)

    plugin_id: str
    state: str  # "completed" | "failed" | "skipped" | "dependency_unavailable"
    failure_category: str | None = None
    message: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


class ReportFinding(BaseModel):
    """A single forensic finding, traceable to the manifest.

    Every finding must reference at least one manifest path or artifact hash so
    a reviewer can verify it independently.
    """

    model_config = ConfigDict(frozen=True)

    category: str  # "suspected_manipulation" | "confirmed_difference" | "unavailable_evidence"
    title: str
    description: str
    severity: str  # "low" | "medium" | "high" | "critical"
    manifest_path: list[str] = Field(default_factory=list)
    artifact_sha256: list[str] = Field(default_factory=list)
    detector: str | None = None


class ReportSummary(BaseModel):
    """Top-line assessment of the run."""

    model_config = ConfigDict(frozen=True)

    assessment: str
    # "authentic" | "suspected_manipulation" | "confirmed_manipulation" |
    # "inconclusive" | "no_detectors" | "blocked"
    reasons: list[str] = Field(default_factory=list)
    detectors_run: int = 0
    detectors_completed: int = 0
    detectors_failed: int = 0
    detectors_skipped: int = 0
    findings_suspected: int = 0
    findings_confirmed: int = 0
    evidence_unavailable: int = 0


class ReportMethodology(BaseModel):
    """Detectors, versions, and configuration used in the run."""

    model_config = ConfigDict(frozen=True)

    pipeline_fingerprint: str = ""
    detectors: list[ReportPluginExecution] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)


class ReportUnavailableEvidence(BaseModel):
    """Evidence that could not be used, with reasons."""

    model_config = ConfigDict(frozen=True)

    reason: str
    affected: list[str] = Field(default_factory=list)


class ReportReproducibility(BaseModel):
    """Information needed to reproduce the run."""

    model_config = ConfigDict(frozen=True)

    application_version: str = ""
    plugin_api_version: str = ""
    pipeline_fingerprint: str = ""
    random_seed: int | None = None
    computational_fingerprint: str | None = None


class Report(BaseModel):
    """A versioned forensic report built from an execution manifest."""

    model_config = ConfigDict(frozen=True)

    report_version: int = Field(default=REPORT_VERSION)
    report_id: str = Field(default_factory=lambda: str(uuid4()))
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_id: str
    status: str
    summary: ReportSummary
    inputs: list[ReportInput] = Field(default_factory=list)
    methodology: ReportMethodology = Field(default_factory=ReportMethodology)
    findings: list[ReportFinding] = Field(default_factory=list)
    unavailable_evidence: list[ReportUnavailableEvidence] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reproducibility: ReportReproducibility = Field(default_factory=ReportReproducibility)
    appendix: dict[str, Any] = Field(default_factory=dict)

    @property
    def findings_by_category(self) -> dict[str, list[ReportFinding]]:
        """Group findings by their category."""
        grouped: dict[str, list[ReportFinding]] = {
            "suspected_manipulation": [],
            "confirmed_difference": [],
            "unavailable_evidence": [],
        }
        for finding in self.findings:
            grouped.setdefault(finding.category, []).append(finding)
        return grouped
