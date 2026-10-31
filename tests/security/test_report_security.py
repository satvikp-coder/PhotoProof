"""No executable markup, unreviewed narrative, metadata leaks, or implied forensic certainty."""

import re
from html.parser import HTMLParser

from reporting_helpers import manifest

from photoproof.core.reporting import build_report, canonical_report_json, render_html


class Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend(attrs)


def test_offline_autoescaping_privacy_and_no_preview_embedding() -> None:
    source = manifest(assessment="inconclusive")
    payload = (
        '<script>alert("xss")</script>'
        '<img src="https://example.invalid/tracker" onerror="alert(1)">'
    )
    source["application"]["name"] = '<script>alert("xss")</script>'
    source["metadata"] = {"GPSLatitude": "PRIVATE_GPS_COORDINATE", "filename": payload}
    source["effective_settings"]["runtime"] = {"root": "C:/PRIVATE_EVIDENCE_PATH"}
    source["executions"]["demo.detector"]["message"] = payload + "PRIVATE_DIAGNOSTIC_PATH"
    source["outputs"]["fusion.assessment"]["reasons"] = [payload, "PRIVATE_GPS_COORDINATE"]
    source["outputs"]["preview"] = {"url": "https://example.invalid/tracker", "metadata": payload}
    html = render_html(build_report(source))
    assert "&lt;script&gt;" in html and "<script>" not in html
    for private in (
        "PRIVATE_GPS_COORDINATE",
        "PRIVATE_EVIDENCE_PATH",
        "PRIVATE_CONFIGURATION_VALUE",
        "PRIVATE_DIAGNOSTIC_PATH",
        "example.invalid",
        "onerror",
    ):
        assert private not in html and private.encode() not in canonical_report_json(
            build_report(source)
        )
    parsed = Elements()
    parsed.feed(html)
    assert not ({"script", "img", "iframe", "object", "embed", "form", "link"} & set(parsed.tags))
    assert not any(
        name.startswith("on") or name in {"src", "srcset"} for name, _ in parsed.attributes
    )
    assert {"main", "header", "footer", "table", "caption", "th"} <= set(parsed.tags)
    assert "Content-Security-Policy" in html


def test_fixed_language_has_no_unsupported_claims_or_probabilities() -> None:
    for status in ("COMPLETED", "INCOMPLETE", "ABORTED", "BLOCKED", "NO_DETECTORS"):
        html = render_html(build_report(manifest(status=status)))
        assert re.search(r"\b(authentic|genuine|real)\b", html, re.IGNORECASE) is None
        assert "proven manipulated" not in html.lower()
        assert "definitively unmodified" not in html.lower()
        narrative = re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)
        assert re.search(r"\d+(?:\.\d+)?%", narrative) is None
        assert "Suspected manipulation" in html and "Confirmed comparison differences" in html
        assert "Unavailable evidence" in html


def test_heuristic_scores_and_artifact_names_are_never_narrated() -> None:
    source = manifest()
    source["outputs"] = {
        "demo.score": {"score": 0.99999, "verdict": "authentic", "GPS": "PRIVATE_GPS"}
    }
    source["artifacts"] = [
        {
            "sha256": "e" * 64,
            "name": '<img onerror="alert(1)">',
            "media_type": "image/svg+xml",
            "size": 10,
            "producer": "demo.detector",
        }
    ]
    result = canonical_report_json(build_report(source))
    assert b"0.99999" not in result and b"authentic" not in result and b"onerror" not in result
    assert b"PRIVATE_GPS" not in result
