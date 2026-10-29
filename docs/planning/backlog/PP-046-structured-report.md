---
id: PP-046
title: "feat(reporting): generate structured forensic reports with evidence traceability"
milestone: M5
labels: [type:feature, priority:P1, area:core, effort:large, status:blocked]
depends_on: [PP-034]
---
## Summary

Implement `core/reporting/`: a versioned report data model built from a run manifest,
plus HTML rendering. It separates **suspected manipulation**, **confirmed differences**
and **unavailable evidence**, and every claim is traceable to manifest fields and
artifact hashes.

## Problem Statement

Manifests are complete but technical. Investigators need a readable, accurate document
that cannot overstate the evidence.

## Objective

`build_report(manifest) -> Report` (pydantic, `report_version: 1`) and
`render_html(report) -> str`, stored in `ARE/reports/<run_id>/`.

## Technical Background

Evidence traceability: each statement in the report holds references
(`manifest_path`, `artifact_sha256`) so a reviewer can verify it independently. The
report format is a public contract, so changes are versioned like the manifest format.

## Functional Requirements

- Sections: summary (assessment category and reasons), inputs (hashes, formats, sizes;
  no GPS), methodology (detectors run, versions, configuration), findings by category,
  unavailable evidence (failed, skipped, not applicable, with reasons), limitations,
  reproducibility (application version, pipeline and computational fingerprints, seed),
  and an appendix with the manifest summary.
- Fixed, reviewed wording templates (shared with PP-031 and PP-034).
- Generated deterministically from the manifest alone (re-rendering gives the same
  result).

## Technical Implementation

`src/photoproof/core/reporting/{model,build,render}.py`. Jinja2 templates with
autoescaping (new dependency, hash-pinned). A CLI command `report RUN_ID [--format
html|json]`.

## Integration Requirements

Inputs: the manifest and artifacts. Consumers: PP-045, PP-047, PP-031.

## Security and Edge Cases

Autoescape everything. Embed only previews and derived artifacts. No GPS coordinates.
Reports of BLOCKED and NO_DETECTORS runs are still valid and state this clearly.

## Testing Requirements

Snapshot tests for runs with each status and assessment category. A traceability test:
every finding has at least one reference. Forbidden-word checks.

## Acceptance Criteria

- [ ] Reports are generated deterministically from manifests for every run status.
- [ ] Every finding references manifest paths and/or artifact hashes (test).
- [ ] Suspected manipulation, confirmed differences and unavailable evidence are separate sections.
- [ ] The report format is versioned and documented.

## Expected Outcome

Accurate, auditable reports.

## Documentation Requirements

A report-format reference page, and the CLI docs.

## Dependencies

PP-034 (assessment categories).

## Priority and Effort

P1, large.
