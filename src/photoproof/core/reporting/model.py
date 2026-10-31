"""Public report-v1 contract. Narrative is selected from fixed codes, never plugin prose."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from photoproof.contracts.enums import AnalysisMode, ExecutionState, FailureCategory, RunStatus

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RunID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
PluginID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", max_length=200)]
Version = Annotated[
    str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.+-]+)?$", max_length=100)
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class AssessmentCategory(StrEnum):
    UNAVAILABLE = "unavailable"
    INCONCLUSIVE = "inconclusive"
    MANIPULATION_INDICATED = "manipulation_indicated"
    DIFFERENCES_FOUND = "differences_found"
    NO_INDICATORS = "no_indicators_found_with_sufficient_coverage"


class SourceReference(FrozenModel):
    """RFC 6901 JSON Pointer into the original manifest, with optional recorded identities."""

    manifest_path: str = Field(pattern=r"^/(?:[^~]|~[01])*$", max_length=500)
    input_sha256: Digest | None = None
    artifact_sha256: Digest | None = None
    plugin_id: PluginID | None = None

    @model_validator(mode="after")
    def one_kind(self) -> "SourceReference":
        if self.input_sha256 is not None and self.artifact_sha256 is not None:
            raise ValueError("a source reference cannot identify both input and artifact")
        return self


class FindingCode(StrEnum):
    ASSESSMENT_MISSING = "assessment_missing"
    ASSESSMENT_UNUSABLE = "assessment_unusable"
    SUSPECTED = "assessment_manipulation"
    DIFFERENCES = "assessment_differences"
    RUN_LIMITED = "run_limited"
    DETECTOR_UNAVAILABLE = "detector_unavailable"
    ARTIFACT_UNVERIFIED = "artifact_unverified"
    INPUT_UNVERIFIED = "input_unverified"
    OUTPUTS_UNINTERPRETED = "outputs_uninterpreted"


FINDING_TEXT = {
    FindingCode.ASSESSMENT_MISSING: (
        "No assessment was recorded. Evidence sufficiency is unavailable."
    ),
    FindingCode.ASSESSMENT_UNUSABLE: (
        "The recorded assessment cannot support a conclusion for this run."
    ),
    FindingCode.SUSPECTED: (
        "The recorded assessment indicates suspected manipulation. Heuristic "
        "indicators are not proof."
    ),
    FindingCode.DIFFERENCES: (
        "The recorded comparison assessment reports observed differences "
        "between the supplied inputs. This does not establish which input was "
        "changed."
    ),
    FindingCode.RUN_LIMITED: (
        "This pipeline outcome limits the available evidence; no favorable conclusion follows."
    ),
    FindingCode.DETECTOR_UNAVAILABLE: (
        "A recorded detector did not succeed or has no recorded execution. Its "
        "evidence is unavailable."
    ),
    FindingCode.ARTIFACT_UNVERIFIED: (
        "This artifact hash is declared in the manifest. The manifest alone "
        "cannot verify the stored bytes."
    ),
    FindingCode.INPUT_UNVERIFIED: (
        "This input hash identifies submitted evidence. The manifest alone "
        "cannot verify the stored bytes."
    ),
    FindingCode.OUTPUTS_UNINTERPRETED: (
        "Additional plugin outputs are recorded but have no reviewed reporting "
        "adapter and are not interpreted here."
    ),
}

STATUS_TEXT = {
    RunStatus.COMPLETED: (
        "The pipeline completed. Successful execution is not a forensic assessment."
    ),
    RunStatus.INCOMPLETE: (
        "The pipeline finished with partial evidence; some planned work did not succeed."
    ),
    RunStatus.ABORTED: "The pipeline stopped early. Evidence may be incomplete.",
    RunStatus.BLOCKED: "Policy or dependencies prevented the selected pipeline from operating.",
    RunStatus.NO_DETECTORS: (
        "No forensic detectors were installed and enabled for this run. No "
        "forensic conclusion is available."
    ),
}
ASSESSMENT_TEXT = {
    AssessmentCategory.UNAVAILABLE: "Assessment unavailable",
    AssessmentCategory.INCONCLUSIVE: "Inconclusive",
    AssessmentCategory.MANIPULATION_INDICATED: "Suspected manipulation indicated",
    AssessmentCategory.DIFFERENCES_FOUND: "Observed comparison differences",
    AssessmentCategory.NO_INDICATORS: "No indicators reported with recorded sufficient coverage",
}
LIMITATIONS = (
    "This report summarizes recorded evidence; it does not establish an image's origin or history.",
    "Missing, failed, skipped or inapplicable detectors provide no favorable evidence.",
    "Heuristic scores are not calibrated probabilities and are not reproduced here.",
    "Raw metadata, filenames, configuration values and diagnostic prose are omitted for privacy.",
    (
        "No previews are embedded: the current manifest has no reviewed preview "
        "authorization contract."
    ),
    (
        "Hashes support verification against retained sources; this report has "
        "no digital signature or independent chain of custody."
    ),
)


class Finding(FrozenModel):
    code: FindingCode
    references: tuple[SourceReference, ...] = Field(min_length=1)


class ApplicationIdentity(FrozenModel):
    name: str = Field(min_length=1, max_length=100)
    version: Version
    plugin_api_version: Version


class Assessment(FrozenModel):
    category: AssessmentCategory
    reasons: tuple[
        Literal["assessment_not_recorded", "assessment_not_usable", "recorded_assessment"], ...
    ]
    references: tuple[SourceReference, ...] = Field(min_length=1)
    reason_references: tuple[SourceReference, ...] = ()
    coverage_references: tuple[SourceReference, ...] = ()
    limitation_references: tuple[SourceReference, ...] = ()


class InputEvidence(FrozenModel):
    role: Literal["target", "reference"]
    sha256: Digest
    size: int = Field(ge=0)
    media_format: Literal["jpeg", "png", "webp", "tiff", "bmp", "gif", "heif", "avif"] | None
    reference: SourceReference


class Method(FrozenModel):
    plugin_id: PluginID
    version: Version
    implementation_sha256: Digest
    required: bool
    deterministic: bool
    state: ExecutionState | None
    failure_category: FailureCategory | None
    references: tuple[SourceReference, ...] = Field(min_length=1)
    configuration: Literal["omitted_for_privacy"] = "omitted_for_privacy"


class ArtifactEvidence(FrozenModel):
    sha256: Digest
    size: int = Field(ge=0)
    producer: PluginID
    reference: SourceReference
    verification: Literal["declared_not_verified"] = "declared_not_verified"


class Reproducibility(FrozenModel):
    pipeline_fingerprint: Digest | None
    computational_fingerprint: Digest
    random_seed: int
    cache_hit: bool


class ManifestAppendix(FrozenModel):
    manifest_version: Literal[1] = 1
    canonical_manifest_sha256: Digest
    included_paths: tuple[str, ...]
    redactions: tuple[str, ...] = (
        "effective_settings",
        "outputs_except_assessment_category",
        "free_text",
        "software_paths",
        "unknown_metadata",
    )


class Report(FrozenModel):
    """Stable derived report, distinct from the source manifest and original evidence."""

    report_version: Literal[1] = 1
    report_id: str = Field(pattern=r"^report-v1-[0-9a-f]{64}$")
    run_id: RunID
    application: ApplicationIdentity
    status: RunStatus
    mode: AnalysisMode
    assessment: Assessment
    inputs: tuple[InputEvidence, ...]
    methodology: tuple[Method, ...]
    suspected_manipulation: tuple[Finding, ...]
    confirmed_differences: tuple[Finding, ...]
    unavailable_evidence: tuple[Finding, ...]
    limitations: tuple[str, ...] = LIMITATIONS
    reproducibility: Reproducibility
    artifacts: tuple[ArtifactEvidence, ...]
    evidence_references: tuple[SourceReference, ...]
    manifest_appendix: ManifestAppendix

    @model_validator(mode="after")
    def validate_references(self) -> "Report":
        for findings, allowed in (
            (self.suspected_manipulation, {FindingCode.SUSPECTED}),
            (self.confirmed_differences, {FindingCode.DIFFERENCES}),
            (
                self.unavailable_evidence,
                set(FindingCode) - {FindingCode.SUSPECTED, FindingCode.DIFFERENCES},
            ),
        ):
            for finding in findings:
                if finding.code not in allowed or any(
                    ref not in self.evidence_references for ref in finding.references
                ):
                    raise ValueError("finding category or reference is inconsistent")
        if self.confirmed_differences and self.mode is not AnalysisMode.COMPARISON:
            raise ValueError("comparison differences require comparison inputs")
        if self.limitations != LIMITATIONS:
            raise ValueError("report-v1 limitations use fixed reviewed wording")
        if self.report_id != f"report-v1-{self.manifest_appendix.canonical_manifest_sha256}":
            raise ValueError("report identity does not match its manifest digest")
        if self.status is not RunStatus.COMPLETED and self.assessment.category not in {
            AssessmentCategory.UNAVAILABLE,
            AssessmentCategory.INCONCLUSIVE,
        }:
            raise ValueError("limited pipeline status cannot support a positive assessment")
        if bool(self.suspected_manipulation) != (
            self.assessment.category is AssessmentCategory.MANIPULATION_INDICATED
        ):
            raise ValueError("suspected findings do not match the recorded assessment")
        if bool(self.confirmed_differences) != (
            self.assessment.category is AssessmentCategory.DIFFERENCES_FOUND
        ):
            raise ValueError("difference findings do not match the recorded assessment")
        return self
