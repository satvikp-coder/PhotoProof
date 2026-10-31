"""Offline, autoescaped, deterministic HTML from the public report contract."""

from jinja2 import Environment, PackageLoader, StrictUndefined

from photoproof.core.reporting.model import (
    ASSESSMENT_TEXT,
    FINDING_TEXT,
    STATUS_TEXT,
    Report,
)


def render_html(report: Report) -> str:
    """Render packaged templates only. No untrusted template strings, media or network I/O."""
    # Revalidate even model_copy/model_construct callers; neither API runs validators.
    validated = Report.model_validate_json(report.model_dump_json())
    environment = Environment(
        loader=PackageLoader("photoproof.core.reporting", "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return environment.get_template("report.html").render(
        report=validated,
        status_text=STATUS_TEXT[validated.status],
        assessment_text=ASSESSMENT_TEXT[validated.assessment.category],
        finding_text=FINDING_TEXT,
    )
