"""Versioned deterministic reporting from validated manifests; no detector inference."""

from photoproof.core.reporting.build import ReportError, build_report, canonical_report_json
from photoproof.core.reporting.model import Report
from photoproof.core.reporting.render import render_html

__all__ = ["Report", "ReportError", "build_report", "canonical_report_json", "render_html"]
