"""Report-v1 semantics, determinism, exact source references and conservative assessment."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from reporting_helpers import manifest, normalized_html

from photoproof.contracts.enums import RunStatus
from photoproof.core.reporting import (
    Report,
    ReportError,
    build_report,
    canonical_report_json,
    render_html,
)
from photoproof.core.reporting.build import resolve_reference
from photoproof.core.reporting.model import (
    ASSESSMENT_TEXT,
    FINDING_TEXT,
    STATUS_TEXT,
    AssessmentCategory,
    Finding,
    FindingCode,
    SourceReference,
)


@pytest.mark.parametrize("status", list(RunStatus))
def test_all_statuses_deterministic_and_do_not_mutate_source(status: RunStatus) -> None:
    source = manifest(status=status.value)
    before = copy.deepcopy(source)
    first, second = build_report(source), build_report(dict(reversed(list(source.items()))))
    assert canonical_report_json(first) == canonical_report_json(second)
    assert render_html(first).encode() == render_html(second).encode()
    assert render_html(first) == render_html(first)
    assert source == before and first.report_version == 1
    assert first.assessment.category is AssessmentCategory.UNAVAILABLE
    assert STATUS_TEXT[status] in render_html(first)
    assert b"PRIVATE_CONFIGURATION_VALUE" not in canonical_report_json(first)
    assert Report.model_validate_json(canonical_report_json(first)) == first


@pytest.mark.parametrize(
    "category",
    [
        "manipulation_indicated",
        "differences_found",
        "no_indicators_found_with_sufficient_coverage",
        "inconclusive",
    ],
)
def test_recorded_assessments_are_attributed_and_separated(category: str) -> None:
    source = manifest(assessment=category, comparison=category == "differences_found")
    report = build_report(source)
    assert report.assessment.category.value == category
    assert bool(report.suspected_manipulation) == (category == "manipulation_indicated")
    assert bool(report.confirmed_differences) == (category == "differences_found")
    assert report.unavailable_evidence  # byte availability cannot be inferred from a manifest
    assert {r.manifest_path for r in report.assessment.reason_references} == {
        "/outputs/fusion.assessment/reasons"
    }
    assert "synthetic reason" not in render_html(report)  # raw prose can contain private data


def test_failed_skipped_and_inapplicable_evidence_is_not_a_positive_assessment() -> None:
    for state in (
        "FAILED",
        "SKIPPED",
        "NOT_APPLICABLE",
        "TIMEOUT",
        "ABORTED",
        "RUNNING",
        "PENDING",
        "READY",
    ):
        source = manifest(assessment="no_indicators_found_with_sufficient_coverage")
        source["executions"]["demo.detector"]["state"] = state
        report = build_report(source)
        assert report.assessment.category is AssessmentCategory.INCONCLUSIVE
        assert FindingCode.DETECTOR_UNAVAILABLE in {f.code for f in report.unavailable_evidence}


def test_missing_or_failed_assessment_never_becomes_clean() -> None:
    for value in (None, {"category": "authentic"}, {"category": "inconclusive"}):
        source = manifest()
        source["outputs"]["fusion.assessment"] = value
        assert build_report(source).assessment.category is AssessmentCategory.UNAVAILABLE
    source = manifest(assessment="manipulation_indicated")
    source["executions"]["fusion.assessment"]["state"] = "FAILED"
    assert build_report(source).assessment.category is AssessmentCategory.UNAVAILABLE
    source = manifest(assessment="differences_found")
    assert build_report(source).assessment.category is AssessmentCategory.INCONCLUSIVE


def test_every_finding_has_resolvable_recorded_sources() -> None:
    source = manifest(assessment="manipulation_indicated")
    report = build_report(source)
    for finding in (
        *report.suspected_manipulation,
        *report.confirmed_differences,
        *report.unavailable_evidence,
    ):
        assert finding.references
        for ref in finding.references:
            assert ref in report.evidence_references
            resolve_reference(source, ref)
    with pytest.raises(ReportError):
        resolve_reference(source, SourceReference(manifest_path="/does/not/exist"))
    with pytest.raises(ReportError):
        resolve_reference(
            source, SourceReference(manifest_path="/inputs/0/sha256", input_sha256="f" * 64)
        )
    with pytest.raises(ValidationError):
        Finding(code=FindingCode.SUSPECTED, references=())


@pytest.mark.parametrize(
    "key,value",
    [
        ("manifest_version", 2),
        ("run_id", "../escape"),
        ("status", "AUTHENTIC"),
        ("random_seed", "42"),
        ("computational_fingerprint", "bad"),
        ("inputs", []),
        ("executions", {"not.valid": {"state": "SUCCESS"}}),
    ],
)
def test_invalid_manifest_is_rejected_without_echoing_values(key: str, value: object) -> None:
    source = manifest()
    source[key] = value
    with pytest.raises(ReportError, match="invalid manifest structure"):
        build_report(source)


def test_undeclared_artifact_and_unsuccessful_producer_are_rejected() -> None:
    source = manifest()
    artifact = {
        "sha256": "e" * 64,
        "name": "synthetic.bin",
        "media_type": "application/octet-stream",
        "size": 3,
        "producer": "demo.detector",
    }
    source["outputs"]["demo.output"] = {"artifact": artifact}
    with pytest.raises(ReportError):
        build_report(source)
    source["artifacts"] = [artifact]
    report = build_report(source)
    assert report.artifacts[0].verification == "declared_not_verified"
    assert any(ref.artifact_sha256 == artifact["sha256"] for ref in report.evidence_references)
    source["executions"]["demo.detector"]["state"] = "FAILED"
    with pytest.raises(ReportError):
        build_report(source)


def test_schema_and_reviewed_language_snapshot() -> None:
    expected = json.loads(
        (Path(__file__).parents[1] / "fixtures/reporting/language.json").read_text()
    )
    actual = {
        "status": {k.value: v for k, v in STATUS_TEXT.items()},
        "assessment": {k.value: v for k, v in ASSESSMENT_TEXT.items()},
        "finding": {k.value: v for k, v in FINDING_TEXT.items()},
    }
    assert actual == expected
    schema = Report.model_json_schema()
    assert schema == json.loads(
        (Path(__file__).parents[2] / "docs/reporting/report-v1.schema.json").read_text()
    )
    assert schema["properties"]["report_version"]["const"] == 1
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "report_version",
        "report_id",
        "run_id",
        "application",
        "status",
        "mode",
        "assessment",
        "inputs",
        "methodology",
        "suspected_manipulation",
        "confirmed_differences",
        "unavailable_evidence",
        "limitations",
        "reproducibility",
        "artifacts",
        "evidence_references",
        "manifest_appendix",
    }


def test_report_cannot_be_relabelled_or_reclassified() -> None:
    report = build_report(manifest())
    with pytest.raises(ValidationError):
        render_html(report.model_copy(update={"report_version": 2}))
    with pytest.raises(ValidationError):
        Report.model_validate({**report.model_dump(), "limitations": ("proven manipulated",)})
    with pytest.raises(ValidationError):
        Report.model_validate(
            {
                **report.model_dump(),
                "suspected_manipulation": (
                    Finding(code=FindingCode.SUSPECTED, references=report.assessment.references),
                ),
            }
        )


def test_synthetic_offline_html_golden() -> None:
    expected = (Path(__file__).parents[1] / "fixtures/reporting/synthetic.html").read_text(
        encoding="utf-8"
    )
    assert normalized_html(build_report(manifest())) == expected
