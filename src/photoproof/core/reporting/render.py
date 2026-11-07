"""Render a :class:`~photoproof.core.reporting.model.Report` to HTML.

Uses Jinja2 templates with autoescaping enabled.  Rendering is deterministic:
the same report always produces the same HTML.
"""

from __future__ import annotations

from jinja2 import BaseLoader, Environment

from photoproof.core.reporting.model import Report

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PhotoProof Forensic Report — {{ report.run_id }}</title>
<style>
  body {
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    margin: 2rem;
    color: #1a1a1a;
  }
  h1 {
    font-size: 1.5rem;
    border-bottom: 2px solid #e5e7eb;
    padding-bottom: 0.5rem;
  }
  h2 {
    font-size: 1.25rem;
    margin-top: 1.5rem;
    color: #374151;
  }
  .meta {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.5rem 1rem;
    font-size: 0.9rem;
  }
  .badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 0.25rem;
    font-size: 0.8rem;
    font-weight: 600;
  }
  .badge-authentic {
    background: #dcfce7;
    color: #166534;
  }
  .badge-suspected {
    background: #fef3c7;
    color: #92400e;
  }
  .badge-confirmed {
    background: #fee2e2;
    color: #991b1b;
  }
  .badge-blocked {
    background: #e5e7eb;
    color: #374151;
  }
  .badge-no-detectors {
    background: #f3f4f6;
    color: #4b5563;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.75rem 0;
    font-size: 0.9rem;
  }
  th, td {
    border: 1px solid #d1d5db;
    padding: 0.5rem;
    text-align: left;
  }
  th {
    background: #f9fafb;
    font-weight: 600;
  }
  .section {
    margin-bottom: 2rem;
  }
  .finding {
    border: 1px solid #e5e7eb;
    border-radius: 0.5rem;
    padding: 0.75rem;
    margin: 0.5rem 0;
    background: #ffffff;
  }
  .finding h3 {
    margin: 0 0 0.25rem 0;
    font-size: 1rem;
  }
  .finding .meta {
    font-size: 0.8rem;
    color: #6b7280;
    margin-bottom: 0.5rem;
  }
  .severity-low {
    color: #6b7280;
  }
  .severity-medium {
    color: #92400e;
  }
  .severity-high {
    color: #991b1b;
  }
  .severity-critical {
    color: #7f1d1d;
    font-weight: 600;
  }
  .traceability {
    font-size: 0.8rem;
    color: #4b5563;
    font-family: ui-monospace, SFMono-Regular, monospace;
  }
  .limitations {
    background: #f3f4f6;
    border-radius: 0.5rem;
    padding: 1rem;
    margin-top: 1rem;
  }
  .limitations h3 {
    margin-top: 0;
  }
  .appendix {
    font-size: 0.85rem;
    color: #6b7280;
  }
  footer {
    margin-top: 3rem;
    border-top: 1px solid #e5e7eb;
    padding-top: 1rem;
    font-size: 0.8rem;
    color: #6b7280;
  }
</style>
</head>
<body>
<h1>PhotoProof Forensic Report</h1>
<div class="meta">
  <div><strong>Run ID:</strong> {{ report.run_id }}</div>
  <div><strong>Status:</strong>
    <span class="badge badge-{{ report.summary.assessment }}">
      {{ report.summary.assessment }}
    </span>
  </div>
  <div><strong>Generated:</strong> {{ report.generated_at }}</div>
  <div><strong>Report version:</strong> {{ report.report_version }}</div>
</div>

<div class="section">
  <h2>Summary</h2>
  <p>{{ report.summary.assessment | replace("_", " ") | title }}</p>
  <ul>
    {% for reason in report.summary.reasons %}
    <li>{{ reason }}</li>
    {% endfor %}
  </ul>
  <table>
    <tr><th>Detectors run</th><td>{{ report.summary.detectors_run }}</td></tr>
    <tr><th>Completed</th><td>{{ report.summary.detectors_completed }}</td></tr>
    <tr><th>Failed</th><td>{{ report.summary.detectors_failed }}</td></tr>
    <tr><th>Skipped</th><td>{{ report.summary.detectors_skipped }}</td></tr>
    <tr><th>Suspected manipulations</th><td>{{ report.summary.findings_suspected }}</td></tr>
    <tr><th>Confirmed differences</th><td>{{ report.summary.findings_confirmed }}</td></tr>
    <tr><th>Evidence unavailable</th><td>{{ report.summary.evidence_unavailable }}</td></tr>
  </table>
</div>

<div class="section">
  <h2>Inputs</h2>
  {% if report.inputs %}
  <table>
    <tr><th>Path</th><th>SHA-256</th><th>Size</th><th>MIME</th></tr>
    {% for inp in report.inputs %}
    <tr>
      <td>{{ inp.path }}</td>
      <td>{{ inp.sha256 }}</td>
      <td>{{ inp.size_bytes }} bytes</td>
      <td>{{ inp.mime_type or "n/a" }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p>No inputs recorded.</p>
  {% endif %}
</div>

<div class="section">
  <h2>Methodology</h2>
  <p><strong>Pipeline fingerprint:</strong> {{ report.methodology.pipeline_fingerprint }}</p>
  {% if report.methodology.detectors %}
  <table>
    <tr><th>Detector</th><th>State</th><th>Category</th><th>Message</th></tr>
    {% for det in report.methodology.detectors %}
    <tr>
      <td>{{ det.plugin_id }}</td>
      <td>{{ det.state }}</td>
      <td>{{ det.failure_category or "n/a" }}</td>
      <td>{{ det.message or "n/a" }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p>No detectors executed.</p>
  {% endif %}
</div>

<div class="section">
  <h2>Findings — Suspected Manipulation</h2>
  {% set suspected = report.findings_by_category.get("suspected_manipulation", []) %}
  {% if suspected %}
  {% for f in suspected %}
  <div class="finding">
    <h3>{{ f.title }}</h3>
    <div class="meta">
      <span class="severity-{{ f.severity }}">severity: {{ f.severity }}</span>
      {% if f.detector %} | detector: {{ f.detector }}{% endif %}
    </div>
    <p>{{ f.description }}</p>
    {% if f.manifest_path or f.artifact_sha256 %}
    <div class="traceability">
      Traceability:
      {% for p in f.manifest_path %} manifest.{{ p }}{% endfor %}
      {% for s in f.artifact_sha256 %} artifact.{{ s }}{% endfor %}
    </div>
    {% endif %}
  </div>
  {% endfor %}
  {% else %}
  <p>No suspected manipulation findings.</p>
  {% endif %}
</div>

<div class="section">
  <h2>Findings — Confirmed Differences</h2>
  {% set confirmed = report.findings_by_category.get("confirmed_difference", []) %}
  {% if confirmed %}
  {% for f in confirmed %}
  <div class="finding">
    <h3>{{ f.title }}</h3>
    <div class="meta">
      <span class="severity-{{ f.severity }}">severity: {{ f.severity }}</span>
      {% if f.detector %} | detector: {{ f.detector }}{% endif %}
    </div>
    <p>{{ f.description }}</p>
    {% if f.manifest_path or f.artifact_sha256 %}
    <div class="traceability">
      Traceability:
      {% for p in f.manifest_path %} manifest.{{ p }}{% endfor %}
      {% for s in f.artifact_sha256 %} artifact.{{ s }}{% endfor %}
    </div>
    {% endif %}
  </div>
  {% endfor %}
  {% else %}
  <p>No confirmed differences.</p>
  {% endif %}
</div>

<div class="section">
  <h2>Unavailable Evidence</h2>
  {% set unavailable = report.findings_by_category.get("unavailable_evidence", []) %}
  {% if unavailable %}
  {% for f in unavailable %}
  <div class="finding">
    <h3>{{ f.title }}</h3>
    <p>{{ f.description }}</p>
    {% if f.manifest_path or f.artifact_sha256 %}
    <div class="traceability">
      Traceability:
      {% for p in f.manifest_path %} manifest.{{ p }}{% endfor %}
      {% for s in f.artifact_sha256 %} artifact.{{ s }}{% endfor %}
    </div>
    {% endif %}
  </div>
  {% endfor %}
  {% else %}
  <p>All evidence is available.</p>
  {% endif %}
</div>

<div class="section">
  <h2>Limitations</h2>
  <div class="limitations">
  {% for limitation in report.limitations %}
  <p>{{ limitation }}</p>
  {% endfor %}
  </div>
</div>

<div class="section appendix">
  <h2>Appendix — Manifest Summary</h2>
  <table>
    <tr><th>Field</th><th>Value</th></tr>
    {% for key, value in report.appendix.manifest_summary.items() %}
    <tr><td>{{ key }}</td><td>{{ value }}</td></tr>
    {% endfor %}
  </table>
</div>

<footer>
  PhotoProof {{ report.methodology.configuration.get("application", {}).get("version", "0.8.0") }}
  &mdash; report generated from manifest only; no authenticity verdict is implied.
</footer>
</body>
</html>
"""


def render_html(report: Report) -> str:
    """Render a :class:`Report` to an HTML string.

    Autoescaping is enabled so all user-controlled text is escaped.
    Rendering is deterministic for a given report.
    """
    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(_HTML_TEMPLATE)
    return template.render(report=report)


def render_json(report: Report) -> str:
    """Render a :class:`Report` to a JSON string."""
    return str(report.model_dump_json(indent=2))
