# Forensic report format 1

`photoproof.core.reporting.build_report(manifest) -> Report` validates a manifest and
creates a privacy-filtered derived document. `render_html(report) -> str` produces
offline HTML. `canonical_report_json(report) -> bytes` produces reproducible UTF-8 JSON.
`Report.model_json_schema()` is the machine-readable schema; the checked-in
[report-v1.schema.json](report-v1.schema.json) records this contract.

Reports do not change the manifest-v1 or plugin contracts. Unknown top-level manifest
extensions remain part of the source digest but are not exported. Invalid required
fields, roles, hashes, run IDs, execution references or artifact references are rejected
with `ReportError`. A report is not a forensic verdict or an independently signed record.

## Public fields

All report models forbid extra fields and are frozen. JSON arrays preserve the
documented builder order; object keys are sorted by canonical serialization. Null means
unavailable/not recorded, never success. Strings are UTF-8. Sizes and seeds are integers;
no heuristic numeric scores or probabilities are emitted.

| Field | Meaning |
|---|---|
| `report_version` | Integer constant `1`; incompatible schema/meaning changes require a new version |
| `report_id` | `report-v1-` plus the canonical source-manifest SHA-256; deterministic, not a random ID |
| `run_id` | Controller-generated 32-character lowercase hexadecimal ID |
| `application` | Recorded `name`, semantic `version`, `plugin_api_version`; application identity is not inferred from the host |
| `status` | Pipeline `RunStatus`, independently of assessment |
| `mode` | `single` or `comparison`; inputs must match this mode |
| `assessment` | Category, fixed reason codes, source/coverage/reason/limitation pointers; see below |
| `inputs` | Role-sorted identities: `role`, SHA-256, integer `size`, known `media_format` or null, and source `reference` |
| `methodology` | Plugin-ID-sorted execution details; see below |
| `suspected_manipulation` | Findings supported by a recorded manipulation assessment; heuristic indications, not proof |
| `confirmed_differences` | Observed differences attributed to a recorded comparison assessment; not a finding about either input's origin |
| `unavailable_evidence` | Missing assessment/execution, limited run, unverified byte identities, uninterpreted outputs |
| `limitations` | Fixed reviewed limitations, including uncertainty, privacy and integrity boundaries |
| `reproducibility` | Recorded `pipeline_fingerprint` (nullable), `computational_fingerprint`, `random_seed`, `cache_hit` |
| `artifacts` | Hash-sorted declared artifacts: `sha256`, `size`, `producer`, `reference`, `verification: declared_not_verified` |
| `evidence_references` | Deterministically sorted, deduplicated source references |
| `manifest_appendix` | `manifest_version: 1`, `canonical_manifest_sha256`, sorted `included_paths`, and `redactions` |

Each methodology item records `plugin_id`, `version`, `implementation_sha256`,
`required`, `deterministic`, nullable execution `state`, nullable `failure_category`,
`references`, and the fixed `configuration: omitted_for_privacy`. A null failure
category means none was recorded, not that execution succeeded. States come from
`ExecutionState`, including pending/ready/running and terminal states. Raw configuration,
exception messages and tracebacks are not reproduced. Fingerprints and the original
access-controlled manifest retain the route to reproducibility.

Known input formats are jpeg, png, webp, tiff, bmp, gif, heif and avif. Unrecognized
format strings become null rather than arbitrary metadata. Original filenames are
not part of the current `InputDescriptor` and are not invented or looked up.

## Assessment versus pipeline status

Pipeline explanations are fixed and snapshot-tested:

| Status | Interpretation |
|---|---|
| `COMPLETED` | Pipeline completed; this is not a forensic assessment |
| `INCOMPLETE` | Partial evidence; planned work did not all succeed |
| `ABORTED` | Stopped early; evidence may be incomplete |
| `BLOCKED` | Policy/dependencies prevented the selected pipeline from operating |
| `NO_DETECTORS` | No installed/enabled forensic detectors; no forensic conclusion available |

The assessment category is one of:

| Value | Fixed label |
|---|---|
| `unavailable` | Assessment unavailable |
| `inconclusive` | Inconclusive |
| `manipulation_indicated` | Suspected manipulation indicated |
| `differences_found` | Observed comparison differences |
| `no_indicators_found_with_sufficient_coverage` | No indicators reported with recorded sufficient coverage |

No assessment is fabricated when #34 has not run. Successful pipeline execution and
absence of findings never imply a favorable assessment. The provisional #34 adapter
accepts only the documented `outputs["fusion.assessment"]` shape:
`{category, reasons[], coverage{}, limitations[]}` with a recorded successful
`fusion.assessment` execution and identity. Unknown/malformed/failed assessments are
unavailable. Non-COMPLETED runs, any recorded plugin without a successful execution,
empty coverage, or comparison differences in single mode downgrade an otherwise
recognized assessment to inconclusive. The report does not implement #34's coverage
threshold or independently confirm its conclusion.

`assessment.reasons` contains fixed codes: `assessment_not_recorded`,
`assessment_not_usable`, or `recorded_assessment`. Raw reason/coverage/limitation prose
can contain private information, so it is withheld. `reason_references`,
`coverage_references` and `limitation_references` point to the original fields instead.
Other detector outputs have no reviewed reporting contract and are explicitly
uninterpreted. #31 may later supply reviewed narrative mappings through these existing
sections; #34 integration must be verified again when its implementation lands.

## Traceability and findings

A `Finding` has a fixed `code` and at least one `references` entry. Human narrative
comes from reviewed code-to-text mappings, never arbitrary detector prose. Codes are
`assessment_missing`, `assessment_unusable`, `assessment_manipulation`,
`assessment_differences`, `run_limited`, `detector_unavailable`, `artifact_unverified`,
`input_unverified`, and `outputs_uninterpreted`. Categories cannot be interchanged.

A `SourceReference` contains an exact RFC 6901 JSON Pointer `manifest_path` and optional
`input_sha256`, `artifact_sha256` or `plugin_id`. Input/artifact identities cannot both
be set on one reference. Pointers index the **original** arrays, even if the report
sorts its presentation. The builder resolves every pointer, checks supplied identities,
and rejects unrecorded plugin/artifact producers. Normalized artifact outputs must
match entries in `manifest.artifacts`; no missing descriptor is invented.

For example, `/inputs/0/sha256` references the actual input digest, and
`/executions/fusion.assessment/state` references recorded execution. A missing assessment
references the existing `/outputs` object, not a nonexistent assessment path.

Manifest-only building performs no filesystem I/O. Declared hashes are therefore
explicitly **not byte verification**. The stored-run service separately verifies the
database-recorded manifest hash and all declared input/artifact blobs, refusing missing
or corrupt evidence. It does not alter the portable report to claim a different
verification level; this keeps the report deterministic from its manifest alone.

## Determinism, privacy and integrity

Canonical JSON uses sorted keys, compact separators, UTF-8, no NaN/Infinity and one
trailing LF. Input/method/artifact/reference ordering is explicit. HTML uses one packaged
Jinja template with autoescaping and LF newlines. There are no current-time timestamps,
random IDs, locale-based numeric formats or host paths. Repeated generation is tested
with exact byte comparisons. The source is never modified.

The appendix is an index, not the full manifest. Its digest hashes canonical JSON;
it is intentionally different from a database hash of the original file's bytes when
whitespace/key ordering differs. A digest is not a signature, provenance certification,
or protection against an owner who can alter both database and evidence. Retain the
original manifest and evidence under their existing access controls. Reports can be
edited after export and must not be treated as independently authenticated documents.

Only allowlisted identity, hash, size, enum and reproducibility fields are exported.
GPS/EXIF, raw filenames, artifact names, runtime settings, software paths, diagnostic
prose and unknown outputs/metadata are omitted. Recorded application/plugin identities
are deliberately public report data; do not put secrets in those identifiers.
HTML has no script, remote assets, image embedding, links to local evidence or network
requests. Autoescaping prevents identity text from becoming markup. The CSP allows
only the fixed inline stylesheet; all scripts, images and other resources are denied.

No preview authorization contract exists in manifest v1, so **all media embedding is
disabled**, including SVG and derived images. #45 may consume report data, #31 may add
reviewed explanation adapters, and #47 owns PDF export; none is implemented here.
