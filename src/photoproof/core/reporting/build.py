"""Validate manifest-v1 structure and project only reviewed, privacy-safe report fields."""

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from photoproof.contracts.analysis import InputDescriptor
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.enums import AnalysisMode, ExecutionState, FailureCategory, RunStatus
from photoproof.contracts.exceptions import ReportError
from photoproof.core.reporting.model import (
    ApplicationIdentity,
    ArtifactEvidence,
    Assessment,
    AssessmentCategory,
    Digest,
    Finding,
    FindingCode,
    FrozenModel,
    InputEvidence,
    ManifestAppendix,
    Method,
    PluginID,
    Report,
    Reproducibility,
    RunID,
    SourceReference,
    Version,
)
from photoproof.core.storage.store import sha256_bytes


class _Execution(BaseModel):
    state: ExecutionState
    failure_category: FailureCategory | None = None


class _Plugin(BaseModel):
    version: Version
    implementation_sha256: Digest
    required: bool
    deterministic: bool


class _Cache(BaseModel):
    key: Digest | None
    hit: bool


class _Manifest(BaseModel):
    # Unknown fields remain in the original for traceability/hash binding, never exported.
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    manifest_version: Literal[1]
    run_id: RunID
    application: ApplicationIdentity
    status: RunStatus
    mode: AnalysisMode
    inputs: tuple[InputDescriptor, ...] = Field(min_length=1, max_length=2)
    plugins: dict[PluginID, _Plugin]
    executions: dict[PluginID, _Execution]
    artifacts: tuple[ArtifactRef, ...]
    outputs: dict[str, JsonValue]
    execution_order: tuple[PluginID, ...]
    blocked_reasons: tuple[str, ...]
    pipeline_fingerprint: Digest | None
    computational_fingerprint: Digest
    random_seed: int
    cache: _Cache

    @model_validator(mode="after")
    def consistent(self) -> "_Manifest":
        expected = {"target", "reference"} if self.mode is AnalysisMode.COMPARISON else {"target"}
        if {i.role for i in self.inputs} != expected or len(self.inputs) != len(expected):
            raise ValueError("input roles do not match analysis mode")
        if not set(self.executions).issubset(self.plugins) or not set(
            self.execution_order
        ).issubset(self.plugins):
            raise ValueError("execution references an unrecorded plugin")
        for artifact in self.artifacts:
            execution = self.executions.get(artifact.producer)
            if execution is None or execution.state is not ExecutionState.SUCCESS:
                raise ValueError("artifact has no successful producer")
        return self


class _RecordedAssessment(FrozenModel):
    category: Literal[
        "manipulation_indicated",
        "differences_found",
        "no_indicators_found_with_sufficient_coverage",
        "inconclusive",
    ]
    reasons: tuple[str, ...] = Field(min_length=1)
    coverage: dict[str, JsonValue]
    limitations: tuple[str, ...]


def canonical_report_json(report: Report) -> bytes:
    """Canonical UTF-8 JSON: sorted keys, compact separators, no NaN, one final newline."""
    validated = Report.model_validate_json(report.model_dump_json())
    return _canonical(validated.model_dump(mode="json")) + b"\n"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def resolve_reference(manifest: Mapping[str, object], reference: SourceReference) -> object:
    """Resolve an RFC 6901 pointer and verify optional recorded identities; never guess."""
    current: object = manifest
    try:
        for part in reference.manifest_path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                current = current[key]
            elif isinstance(current, list) and key.isdigit() and str(int(key)) == key:
                current = current[int(key)]
            else:
                raise ValueError("unresolvable pointer")
        digest = reference.input_sha256 or reference.artifact_sha256
        if digest is not None and current != digest:
            raise ValueError("hash reference mismatch")
        if reference.input_sha256 is not None and not reference.manifest_path.startswith(
            "/inputs/"
        ):
            raise ValueError("input pointer mismatch")
        if reference.artifact_sha256 is not None and not reference.manifest_path.startswith(
            "/artifacts/"
        ):
            raise ValueError("artifact pointer mismatch")
        if reference.plugin_id is not None:
            plugins = manifest.get("plugins")
            if not isinstance(plugins, dict) or reference.plugin_id not in plugins:
                raise ValueError("plugin reference mismatch")
            pid = reference.plugin_id
            allowed = (f"/plugins/{pid}", f"/executions/{pid}", f"/outputs/{pid}")
            if reference.manifest_path.startswith("/artifacts/"):
                index = int(reference.manifest_path.split("/")[2])
                artifacts = manifest.get("artifacts")
                item = artifacts[index] if isinstance(artifacts, list) else None
                if not isinstance(item, dict) or item.get("producer") != pid:
                    raise ValueError("artifact producer mismatch")
            elif not any(
                reference.manifest_path == prefix
                or reference.manifest_path.startswith(prefix + "/")
                for prefix in allowed
            ):
                raise ValueError("plugin pointer mismatch")
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ReportError(
            "report source reference is inconsistent; inspect the source manifest"
        ) from error
    return current


def _check_output_artifacts(value: JsonValue, declared: tuple[ArtifactRef, ...]) -> None:
    if isinstance(value, dict):
        if "artifact" in value:
            artifact = ArtifactRef.model_validate(value["artifact"])
            if artifact not in declared:
                raise ValueError("output references an undeclared artifact")
        for child in value.values():
            _check_output_artifacts(child, declared)
    elif isinstance(value, list):
        for child in value:
            _check_output_artifacts(child, declared)


def build_report(manifest: Mapping[str, object]) -> Report:
    """Build a deterministic report without I/O or source mutation; raise ReportError if invalid.

    Artifact/input bytes are explicitly unverified here. Stored-run publication separately
    verifies the manifest's recorded hash and every referenced input/artifact blob.
    """
    try:
        raw = _canonical(dict(manifest))
        original: dict[str, object] = json.loads(raw)
        source = _Manifest.model_validate_json(raw, strict=True)
        for output in source.outputs.values():
            _check_output_artifacts(output, source.artifacts)
    except (ValidationError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ReportError(
            "invalid manifest structure or artifact references; report not generated"
        ) from error

    unavailable: list[Finding] = []
    suspected: list[Finding] = []
    differences: list[Finding] = []
    references: list[SourceReference] = []

    def ref(
        path: str,
        *,
        input_hash: str | None = None,
        artifact_hash: str | None = None,
        plugin_id: str | None = None,
    ) -> SourceReference:
        result = SourceReference(
            manifest_path=path,
            input_sha256=input_hash,
            artifact_sha256=artifact_hash,
            plugin_id=plugin_id,
        )
        resolve_reference(original, result)
        if result not in references:
            references.append(result)
        return result

    def missing(code: FindingCode, *refs: SourceReference) -> None:
        unavailable.append(Finding(code=code, references=refs))

    status_ref = ref("/status")
    for field in (
        "run_id",
        "application",
        "mode",
        "pipeline_fingerprint",
        "computational_fingerprint",
        "random_seed",
        "cache",
    ):
        ref(f"/{field}")
    if source.status is not RunStatus.COMPLETED:
        missing(FindingCode.RUN_LIMITED, status_ref)
    inputs = []
    known_formats = {"jpeg", "png", "webp", "tiff", "bmp", "gif", "heif", "avif"}
    for index, item in sorted(enumerate(source.inputs), key=lambda pair: pair[1].role):
        pointer = ref(f"/inputs/{index}/sha256", input_hash=item.sha256)
        ref(f"/inputs/{index}")
        # Unknown format labels never become arbitrary exported metadata.
        media_format = item.media_format if item.media_format in known_formats else None
        inputs.append(
            InputEvidence.model_validate(
                {
                    "role": item.role,
                    "sha256": item.sha256,
                    "size": item.size,
                    "media_format": media_format,
                    "reference": pointer,
                }
            )
        )
        missing(FindingCode.INPUT_UNVERIFIED, pointer)
    methods = []
    for pid, plugin in sorted(source.plugins.items()):
        execution = source.executions.get(pid)
        plugin_refs = [ref(f"/plugins/{pid}", plugin_id=pid)]
        if execution is not None:
            plugin_refs.append(ref(f"/executions/{pid}/state", plugin_id=pid))
        if execution is None or execution.state is not ExecutionState.SUCCESS:
            missing(FindingCode.DETECTOR_UNAVAILABLE, *plugin_refs)
        methods.append(
            Method(
                plugin_id=pid,
                version=plugin.version,
                implementation_sha256=plugin.implementation_sha256,
                required=plugin.required,
                deterministic=plugin.deterministic,
                state=execution.state if execution else None,
                failure_category=execution.failure_category if execution else None,
                references=tuple(plugin_refs),
            )
        )
    artifacts = []
    for index, artifact in sorted(
        enumerate(source.artifacts), key=lambda pair: (pair[1].sha256, pair[1].producer, pair[0])
    ):
        pointer = ref(
            f"/artifacts/{index}/sha256", artifact_hash=artifact.sha256, plugin_id=artifact.producer
        )
        artifacts.append(
            ArtifactEvidence(
                sha256=artifact.sha256,
                size=artifact.size,
                producer=artifact.producer,
                reference=pointer,
            )
        )
        missing(FindingCode.ARTIFACT_UNVERIFIED, pointer)

    assessment_ref = ref("/outputs")
    execution_ref: SourceReference | None = None
    category = AssessmentCategory.UNAVAILABLE
    reason = "assessment_not_recorded"
    reason_refs: tuple[SourceReference, ...] = ()
    coverage_refs: tuple[SourceReference, ...] = ()
    limitation_refs: tuple[SourceReference, ...] = ()
    if "fusion.assessment" in source.outputs:
        assessment_ref = ref("/outputs/fusion.assessment")
        try:
            recorded = _RecordedAssessment.model_validate(source.outputs["fusion.assessment"])
        except ValidationError:
            recorded = None
        execution = source.executions.get("fusion.assessment")
        healthy = source.status is RunStatus.COMPLETED and all(
            pid in source.executions and source.executions[pid].state is ExecutionState.SUCCESS
            for pid in source.plugins
        )
        if (
            recorded is not None
            and execution is not None
            and execution.state is ExecutionState.SUCCESS
        ):
            category = AssessmentCategory(recorded.category)
            if (
                not healthy
                or not recorded.coverage
                or (
                    category is AssessmentCategory.DIFFERENCES_FOUND
                    and source.mode is not AnalysisMode.COMPARISON
                )
            ):
                category = AssessmentCategory.INCONCLUSIVE
            assessment_ref = ref(
                "/outputs/fusion.assessment/category", plugin_id="fusion.assessment"
            )
            execution_ref = ref(
                "/executions/fusion.assessment/state", plugin_id="fusion.assessment"
            )
            reason_refs = (
                ref("/outputs/fusion.assessment/reasons", plugin_id="fusion.assessment"),
            )
            coverage_refs = (
                ref("/outputs/fusion.assessment/coverage", plugin_id="fusion.assessment"),
            )
            limitation_refs = (
                ref("/outputs/fusion.assessment/limitations", plugin_id="fusion.assessment"),
            )
            reason = (
                "recorded_assessment"
                if category.value == recorded.category
                else "assessment_not_usable"
            )
        else:
            reason = "assessment_not_usable"
    if category is AssessmentCategory.UNAVAILABLE:
        missing(
            FindingCode.ASSESSMENT_MISSING
            if reason == "assessment_not_recorded"
            else FindingCode.ASSESSMENT_UNUSABLE,
            assessment_ref,
        )
    if reason == "assessment_not_usable" and category is not AssessmentCategory.UNAVAILABLE:
        missing(FindingCode.ASSESSMENT_UNUSABLE, assessment_ref, status_ref)
    if category is AssessmentCategory.MANIPULATION_INDICATED:
        suspected.append(
            Finding(
                code=FindingCode.SUSPECTED,
                references=(
                    assessment_ref,
                    *([execution_ref] if execution_ref else []),
                    *[i.reference for i in inputs],
                ),
            )
        )
    if category is AssessmentCategory.DIFFERENCES_FOUND:
        differences.append(
            Finding(
                code=FindingCode.DIFFERENCES,
                references=(
                    assessment_ref,
                    *([execution_ref] if execution_ref else []),
                    *[i.reference for i in inputs],
                ),
            )
        )
    if set(source.outputs) - {"fusion.assessment"}:
        missing(FindingCode.OUTPUTS_UNINTERPRETED, ref("/outputs"))
    # A stable sort never depends on dict insertion order, locale, current time or runtime paths.
    references.sort(
        key=lambda r: (
            r.manifest_path,
            r.input_sha256 or "",
            r.artifact_sha256 or "",
            r.plugin_id or "",
        )
    )
    digest = sha256_bytes(raw)
    return Report(
        report_id=f"report-v1-{digest}",
        run_id=source.run_id,
        application=source.application,
        status=source.status,
        mode=source.mode,
        assessment=Assessment.model_validate(
            {
                "category": category,
                "reasons": (reason,),
                "references": (assessment_ref,),
                "reason_references": reason_refs,
                "coverage_references": coverage_refs,
                "limitation_references": limitation_refs,
            }
        ),
        inputs=tuple(inputs),
        methodology=tuple(methods),
        suspected_manipulation=tuple(suspected),
        confirmed_differences=tuple(differences),
        unavailable_evidence=tuple(unavailable),
        reproducibility=Reproducibility(
            pipeline_fingerprint=source.pipeline_fingerprint,
            computational_fingerprint=source.computational_fingerprint,
            random_seed=source.random_seed,
            cache_hit=source.cache.hit,
        ),
        artifacts=tuple(artifacts),
        evidence_references=tuple(references),
        manifest_appendix=ManifestAppendix(
            canonical_manifest_sha256=digest,
            included_paths=tuple(sorted({r.manifest_path for r in references})),
        ),
    )
