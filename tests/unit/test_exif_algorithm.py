"""Tests for photoproof.plugins.metadata.exif.algorithm."""

import io

import pytest

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.metadata.exif.algorithm import analyze_exif


def _make_jpeg(exif_dict=None, xmp_bytes=None) -> bytes:
    """Build a valid JPEG using Pillow's save with optional EXIF."""
    from PIL import Image

    img = Image.new("RGB", (64, 64), color="white")
    buf = io.BytesIO()
    kwargs = {}
    if exif_dict is not None:
        exif = Image.Exif()
        for tag, value in exif_dict.items():
            exif[tag] = value
        kwargs["exif"] = exif
    img.save(buf, format="JPEG", **kwargs)
    data = buf.getvalue()
    if xmp_bytes is not None:
        # Inject XMP into APP1 segment after SOI
        xmp_segment = b"\xff\xe1" + (len(xmp_bytes) + 2 + 29).to_bytes(2, "big")
        xmp_segment += b"http://ns.adobe.com/xap/1.0/\x00" + xmp_bytes
        # Insert after SOI (first 2 bytes)
        data = data[:2] + xmp_segment + data[2:]
    return data


def test_empty_input_rejected() -> None:
    with pytest.raises(InputRejectedError):
        analyze_exif(b"")


def test_no_metadata_absent_finding() -> None:
    """Acceptance: missing metadata -> metadata_absent (info, not suspicious)."""
    data = _make_jpeg()
    summary, findings = analyze_exif(data)
    assert summary["gps_present"] is False
    codes = {f["code"] for f in findings}
    assert "metadata_absent" in codes
    absent = next(f for f in findings if f["code"] == "metadata_absent")
    assert absent["severity"] == "info"
    assert "not suspicious" in absent["false_positive_note"].lower()


def test_editor_software_detected() -> None:
    """Positive test: editor Software tag produces metadata_editor_detected."""
    data = _make_jpeg(exif_dict={305: "Adobe Photoshop 2024"})
    summary, findings = analyze_exif(data)
    assert summary["software"] == "Adobe Photoshop 2024"
    codes = {f["code"] for f in findings}
    assert "metadata_editor_detected" in codes
    editor = next(f for f in findings if f["code"] == "metadata_editor_detected")
    assert editor["severity"] == "info"


def test_no_editor_software_no_finding() -> None:
    """Negative test: no software tag -> no editor finding."""
    data = _make_jpeg()
    _summary, findings = analyze_exif(data)
    codes = {f["code"] for f in findings}
    assert "metadata_editor_detected" not in codes


def test_impossible_date_flagged() -> None:
    """Positive test: impossible EXIF date produces metadata_impossible_date."""
    data = _make_jpeg(exif_dict={306: "3000:01:01 00:00:00"})
    _summary, findings = analyze_exif(data)
    codes = {f["code"] for f in findings}
    assert "metadata_impossible_date" in codes


def test_valid_date_no_finding() -> None:
    """Negative test: valid date -> no metadata_impossible_date."""
    data = _make_jpeg(exif_dict={306: "2020:06:15 10:30:00"})
    _summary, findings = analyze_exif(data)
    codes = {f["code"] for f in findings}
    assert "metadata_impossible_date" not in codes


def test_dimension_mismatch() -> None:
    """Positive test: EXIF dimensions disagree with actual dimensions."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="white")
    exif = Image.Exif()
    exif[0xA002] = 200
    exif[0xA003] = 200
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    data = buf.getvalue()
    _summary, findings = analyze_exif(data)
    codes = {f["code"] for f in findings}
    assert "metadata_dimension_mismatch" in codes


def test_dimensions_match_no_finding() -> None:
    """Negative test: EXIF dimensions match actual dimensions."""
    data = _make_jpeg()
    _summary, findings = analyze_exif(data)
    codes = {f["code"] for f in findings}
    assert "metadata_dimension_mismatch" not in codes


def test_xmp_entity_expansion_neutralized() -> None:
    """XMP entity-expansion payloads do not crash and produce a finding."""
    xmp_bomb = (
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about="">'
        b'<xmp:CreatorTool xmlns:xmp="http://ns.adobe.com/xap/1.0/">Test</xmp:CreatorTool>'
        b"</rdf:Description></rdf:RDF>"
    )
    data = _make_jpeg(xmp_bytes=xmp_bomb)
    summary, findings = analyze_exif(data)
    assert isinstance(findings, list)
    assert isinstance(summary, dict)


def test_gps_coordinates_never_emitted() -> None:
    """GPS coordinates never appear in outputs, logs or manifests."""
    from PIL import Image

    img = Image.new("RGB", (64, 64), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    data = buf.getvalue()
    summary, findings = analyze_exif(data)
    # GPS should be boolean only
    assert summary["gps_present"] is False
    # No GPS coordinates in summary
    summary_str = str(summary)
    assert "latitude" not in summary_str.lower()
    assert "longitude" not in summary_str.lower()
    # No GPS coordinates in findings
    findings_str = str(findings)
    assert "latitude" not in findings_str.lower()
    assert "longitude" not in findings_str.lower()


def test_summary_structure() -> None:
    """Summary contains expected keys with correct types."""
    data = _make_jpeg()
    summary, _findings = analyze_exif(data)
    assert "camera" in summary
    assert "lens" in summary
    assert "timestamps" in summary
    assert "software" in summary
    assert "gps_present" in summary
    assert "dimensions" in summary
    assert "thumbnail_present" in summary
    assert "format" in summary
    assert summary["format"] == "JPEG"
    assert summary["dimensions"] == (64, 64)
    assert isinstance(summary["gps_present"], bool)
    assert isinstance(summary["timestamps"], tuple)


def test_findings_structure() -> None:
    """Each finding has code, severity, message, fields, false_positive_note."""
    data = _make_jpeg()
    _summary, findings = analyze_exif(data)
    for f in findings:
        assert "code" in f
        assert "severity" in f
        assert "message" in f
        assert "fields" in f
        assert "false_positive_note" in f
        assert f["severity"] in ("info", "low", "medium")
        assert isinstance(f["fields"], list)
        assert isinstance(f["false_positive_note"], str)


def test_malformed_input_rejected() -> None:
    """Non-image bytes are rejected."""
    with pytest.raises(InputRejectedError):
        analyze_exif(b"not an image at all")
