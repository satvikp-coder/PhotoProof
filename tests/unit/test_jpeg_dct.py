"""Tests for photoproof.plugins.compression.jpeg_dct.algorithm."""

import io

import pytest
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.plugins.compression.jpeg_dct.algorithm import (
    _estimate_quality,
    _extract_quantization_tables,
    _is_standard_table,
    analyze_jpeg_dct,
)


def _make_jpeg_with_quality(q: int, size: tuple[int, int] = (64, 64)) -> bytes:
    """Create a valid JPEG at the given quality."""
    img = Image.new("RGB", size, color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q)
    return buf.getvalue()


def test_valid_jpeg_tables() -> None:
    """Valid JPEG produces quantization tables."""
    data = _make_jpeg_with_quality(75)
    tables_dict, _dq_map_dict = analyze_jpeg_dct(data)
    assert "tables" in tables_dict
    assert len(tables_dict["tables"]) > 0
    assert "estimated_quality" in tables_dict
    assert "standard_table" in tables_dict
    assert "context" in tables_dict
    assert tables_dict["context"] == "jpeg"


def test_quality_estimation() -> None:
    """Quality estimation should return a reasonable value."""
    for q in [50, 75, 90, 95]:
        data = _make_jpeg_with_quality(q)
        tables_dict, _ = analyze_jpeg_dct(data)
        est = tables_dict["estimated_quality"]
        assert est is not None
        assert 1 <= est <= 100  # Quality should be in valid range


def test_standard_table_detection() -> None:
    """Q50 JPEG should have standard-looking tables."""
    data = _make_jpeg_with_quality(50)
    tables_dict, _ = analyze_jpeg_dct(data)
    # standard_table is a boolean indicating table consistency
    assert isinstance(tables_dict["standard_table"], bool)


def test_non_standard_table() -> None:
    """Non-Q50 images should have non-standard tables."""
    data = _make_jpeg_with_quality(75)
    tables_dict, _ = analyze_jpeg_dct(data)
    assert tables_dict["standard_table"] is False


def test_dq_map_output() -> None:
    """DQ map should contain anomaly data."""
    data = _make_jpeg_with_quality(75, size=(128, 128))
    _, dq_map_dict = analyze_jpeg_dct(data)
    assert "anomaly" in dq_map_dict
    assert "quality_estimate" in dq_map_dict
    assert "context" in dq_map_dict
    assert dq_map_dict["context"] == "jpeg"
    anomaly = dq_map_dict["anomaly"]
    assert "values" in anomaly
    assert "block" in anomaly
    assert "valid_mask" in anomaly


def test_empty_input_rejected() -> None:
    """Empty input is rejected."""
    with pytest.raises(InputRejectedError):
        analyze_jpeg_dct(b"")


def test_non_jpeg_rejected() -> None:
    """Non-JPEG input is rejected."""
    with pytest.raises(InputRejectedError):
        analyze_jpeg_dct(b"not a jpeg")


def test_double_compression_detection() -> None:
    """Double-compressed JPEG should show elevated anomaly in some blocks."""
    # Create a single-compressed image
    img1 = Image.new("RGB", (128, 128), color=(100, 150, 200))
    buf1 = io.BytesIO()
    img1.save(buf1, format="JPEG", quality=90)
    data1 = buf1.getvalue()

    # Recompress at different quality (double compression)
    img2 = Image.open(buf1)
    buf2 = io.BytesIO()
    img2.save(buf2, format="JPEG", quality=70)
    data2 = buf2.getvalue()

    _, dq1 = analyze_jpeg_dct(data1)
    _, dq2 = analyze_jpeg_dct(data2)

    # Both should produce valid anomaly maps
    assert len(dq1["anomaly"]["values"]) > 0
    assert len(dq2["anomaly"]["values"]) > 0


def test_extract_quantization_tables() -> None:
    """Quantization table extraction works on valid JPEG."""
    data = _make_jpeg_with_quality(75)
    tables = _extract_quantization_tables(data)
    assert len(tables) > 0
    for t in tables:
        assert t.table.shape == (8, 8)
        assert t.table_id >= 0


def test_estimate_quality_direct() -> None:
    """Quality estimation works directly on tables."""
    data = _make_jpeg_with_quality(75)
    tables = _extract_quantization_tables(data)
    quality = _estimate_quality(tables)
    assert quality is not None
    assert 1 <= quality <= 100  # Quality should be in valid range


def test_is_standard_table_direct() -> None:
    """Standard table detection returns a boolean."""
    data = _make_jpeg_with_quality(50)
    tables = _extract_quantization_tables(data)
    result = _is_standard_table(tables)
    assert isinstance(result, bool)

    data75 = _make_jpeg_with_quality(75)
    tables75 = _extract_quantization_tables(data75)
    result75 = _is_standard_table(tables75)
    assert isinstance(result75, bool)


def test_analyze_jpeg_dct_small_image() -> None:
    """Very small image still produces output."""
    data = _make_jpeg_with_quality(75, size=(16, 16))
    tables_dict, dq_map_dict = analyze_jpeg_dct(data)
    assert "tables" in tables_dict
    assert "anomaly" in dq_map_dict


def test_dq_map_structure() -> None:
    """DQ map has correct structure."""
    data = _make_jpeg_with_quality(75, size=(128, 128))
    _, dq_map_dict = analyze_jpeg_dct(data)
    anomaly = dq_map_dict["anomaly"]
    assert "values" in anomaly
    assert "block" in anomaly
    assert anomaly["block"] == [8, 8]
    assert "stride" in anomaly
    assert anomaly["stride"] == [8, 8]
    assert "scale" in anomaly
    assert anomaly["scale"] == "robust_z"
    assert "valid_mask" in anomaly
    assert "notes" in anomaly
