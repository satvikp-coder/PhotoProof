# Generate an evidence report

Generate a report from a completed stored run (any recorded terminal pipeline status):

```bash
python main.py report RUN_ID --format html
python main.py report RUN_ID --format json
```

Use the 32-character lowercase run ID printed by `analyze`. The CLI prints the output
location. Files are `ARE/reports/<run_id>/report.html` and `report.json`; they are
derived documents, never original evidence. JSON is canonical and HTML works offline
and when printed. No original images or previews are embedded.

The command reuses configured application services but skips automatic preflight
repairs. It will not initialize a missing database, alter manifests, repair evidence,
or downgrade an integrity failure to a warning. Exit 1 indicates missing/invalid
sources, mismatched hashes or storage failure; invalid format arguments use exit 2.
Existing configuration errors retain the CLI's established exit behavior.

Before publication, the exact manifest byte snapshot is checked against the hash in
the database and then parsed. Missing hashes, duplicate JSON keys, wrong run identity,
unfinished/inconsistent database lifecycle, invalid structure, and missing/corrupt
input or artifact blobs cause refusal. Preserve the source and use `doctor` to
investigate; reporting never automatically repairs evidence.

Each requested format is published independently through the existing atomic-write
helper: readers see the old complete file or the new complete file. Interrupted
`.part` files are not finished reports. Failure before rename preserves any previous
report. This is not a two-file transaction or a guarantee against faulty hardware.
Compare `report_id` if exporting formats at different times. New report directories
and files use private POSIX modes and existing runtime inheritance rules on Windows.
Link/reparse/hardlink destinations are refused; do not redirect report directories
into evidence, manifests or unrelated trees. Trusted runtime parents are required.

The HTML's fixed headings distinguish suspected manipulation, observed comparison
differences and unavailable evidence. A completed pipeline is not an assessment.
Without #34, assessment is explicitly unavailable. No successful or empty result is
presented as proof of an input's history. See the [format reference](../reporting/format.md)
for exact fields, fixed category wording, traceability and privacy limits.

Jinja2 (BSD-3-Clause) supplies autoescaped templates; MarkupSafe (BSD-3-Clause) is its
escaping dependency. Both are hash-pinned, installed wheel-only, and receive Pallets
maintenance/security releases. Templates are packaged with PhotoProof; user data
never selects template source or performs a network lookup.

## Embedding the service

```python
from photoproof.core.reporting import build_report, canonical_report_json, render_html

report = build_report(manifest)  # validated data already loaded by your application
json_bytes = canonical_report_json(report)
html = render_html(report)
```

This pure builder does not verify filesystem bytes. For retained runs use
`Application.report(run_id, format="html")`, which performs stored integrity checks.
There is no dependency on the unmerged WebKit, Windows ACL or concurrency branches.
Future integrations should retain their stronger runtime protections when merged.

## Accessibility and print checklist

The automated smoke tests inspect semantic landmarks, table captions/headers, absence
of executable or remote elements and fixed findings sections. Browser/assistive
technology checks remain manual: open the synthetic HTML offline; tab to table regions;
check visible focus; zoom to 200%; use a narrow viewport; inspect heading order with a
screen reader; and print to confirm table wrapping/page breaks. No browser/axe run is
claimed by these tests.
