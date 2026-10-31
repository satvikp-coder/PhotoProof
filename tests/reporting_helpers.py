"""Synthetic manifest-v1 fixtures constructed through the production manifest builder."""

from helpers import REPO

from photoproof.core.config.loader import load_identity
from photoproof.core.pipeline.manifest import build_manifest
from photoproof.core.reporting import Report, render_html


def manifest(
    *, status: str = "COMPLETED", assessment: str | None = None, comparison: bool = False
) -> dict[str, object]:
    identity = load_identity(REPO).application
    plugins = {
        "demo.detector": {
            "version": identity.version,
            "implementation_sha256": "b" * 64,
            "priority": 10,
            "required": True,
            "deterministic": True,
            "python_dependencies": {},
        }
    }
    executions = {"demo.detector": {"state": "SUCCESS", "failure_category": None, "message": None}}
    outputs = {}
    if assessment is not None:
        plugins["fusion.assessment"] = {**plugins["demo.detector"], "priority": 95}
        executions["fusion.assessment"] = {
            "state": "SUCCESS",
            "failure_category": None,
            "message": None,
        }
        outputs["fusion.assessment"] = {
            "category": assessment,
            "reasons": ["synthetic reason"],
            "coverage": {"synthetic": "available"},
            "limitations": [],
        }
    if status in {"NO_DETECTORS", "BLOCKED"}:
        plugins, executions, outputs = {}, {}, {}
    elif status != "COMPLETED":
        executions["demo.detector"]["state"] = "FAILED"
    inputs = [{"role": "target", "sha256": "a" * 64, "size": 42, "media_format": "jpeg"}]
    if comparison:
        inputs.append({"role": "reference", "sha256": "c" * 64, "size": 84, "media_format": "png"})
    return build_manifest(
        run_id="1" * 32,
        created_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        application={
            "name": identity.name,
            "version": identity.version,
            "plugin_api_version": identity.plugin_api_version,
        },
        status=status,
        mode="comparison" if comparison else "single",
        profile="standard",
        inputs=inputs,
        pipeline_fingerprint="d" * 64,
        cache={"key": None, "hit": False},
        effective_settings={"security": {"secret": "PRIVATE_CONFIGURATION_VALUE"}},
        feature_flags={},
        policy={},
        blacklist_override=False,
        plugins=plugins,
        dependency_graph={},
        execution_order=list(plugins),
        executions=executions,
        blocked_reasons=["synthetic policy block"] if status == "BLOCKED" else [],
        random_seed=42,
        artifacts=[],
        outputs=outputs,
        software={"git_commit": "synthetic"},
    )


def normalized_html(report: Report) -> str:
    """Golden presentation without duplicating application versions outside app.toml."""
    result = render_html(report)
    for value, replacement in (
        (report.report_id, "REPORT_ID"),
        (report.manifest_appendix.canonical_manifest_sha256, "MANIFEST_DIGEST"),
        (report.reproducibility.computational_fingerprint, "COMPUTATIONAL_FINGERPRINT"),
        (report.application.version, "APPLICATION_VERSION"),
        (report.application.name, "APPLICATION_NAME"),
    ):
        result = result.replace(value, replacement)
    return result
