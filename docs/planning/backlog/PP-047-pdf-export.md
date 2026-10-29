---
id: PP-047
title: "feat(reporting): export forensic reports as PDF"
milestone: M5
labels: [type:feature, priority:P2, area:core, effort:medium, status:blocked]
depends_on: [PP-046]
---
## Summary

Render the structured report to PDF with embedded fonts, a document hash and metadata,
without requiring a headless browser.

## Problem Statement

Reports are often shared and archived as PDF.

## Objective

`render_pdf(report) -> bytes`, deterministic apart from the creation date, which is
recorded, with the report's SHA-256 printed in the footer.

## Technical Background

Options: WeasyPrint (HTML/CSS → PDF; needs Pango/cairo system libraries, a cross-platform
burden) versus ReportLab or fpdf2 (pure Python; lay out from the model). Prefer a
pure-Python library to keep `main.py` installs wheel-only. Decide in the PR.

## Functional Requirements

- Same content as the HTML report (generated from the same model).
- Embedded fonts with an open licence. Page numbers. A footer with the run ID and the
  report hash.
- PDF metadata: title, application version. No author PII.
- Images: previews and heatmaps only.

## Technical Implementation

`core/reporting/pdf.py`. The dependency is added to the lockfiles. CLI `report RUN_ID
--format pdf`.

## Integration Requirements

PP-045 download. Stored in `ARE/reports/<run_id>/report.pdf`.

## Security and Edge Cases

Escape text (PDF libraries can still interpret markup in some APIs). Large reports
(many regions; paginate the tables). Unicode file names.

## Testing Requirements

The PDF opens and its text layer contains the key fields (extract with `pypdf` in
tests); page count bounds; images embedded.

## Acceptance Criteria

- [ ] PDF contains the same sections as the HTML report (text extraction test).
- [ ] Installation remains wheel-only on all CI platforms.
- [ ] The footer includes the run ID and the report hash.

## Expected Outcome

Shareable, archivable reports.

## Documentation Requirements

The report-format page.

## Dependencies

PP-046.

## Priority and Effort

P2, medium.
