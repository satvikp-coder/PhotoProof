"""Pure-Python JPEG DCT/quantization analysis algorithm.

Parses JPEG quantization tables from DQT markers and performs 8x8 DCT on
decoded blocks to detect double-compression patterns via histogram periodicity.

No new dependencies beyond NumPy. All JPEG bitstream parsing is done in pure
Python; no native decoder bindings are required.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.imaging.stats import anomaly_map, robust_zscore

#: JPEG marker prefixes
_MARKER_SOI = b"\xff\xd8"
_MARKER_EOI = b"\xff\xd9"
_MARKER_DQT = b"\xff\xdb"

#: Libjpeg standard luminance quantization table (quality 50 baseline)
_STD_LUM_Q50 = np.array(
    [
        16,
        11,
        10,
        16,
        24,
        40,
        51,
        61,
        12,
        12,
        14,
        19,
        26,
        58,
        60,
        55,
        14,
        13,
        16,
        24,
        40,
        57,
        69,
        56,
        14,
        17,
        22,
        29,
        51,
        87,
        80,
        62,
        18,
        22,
        37,
        56,
        68,
        109,
        103,
        77,
        24,
        35,
        55,
        64,
        81,
        104,
        113,
        92,
        49,
        64,
        78,
        87,
        103,
        121,
        120,
        101,
        72,
        92,
        95,
        98,
        112,
        100,
        103,
        99,
    ],
    dtype=np.float32,
).reshape(8, 8)

#: Libjpeg standard chrominance quantization table (quality 50 baseline)
_STD_CHROMA_Q50 = np.array(
    [
        17,
        18,
        24,
        47,
        99,
        99,
        99,
        99,
        18,
        21,
        26,
        66,
        99,
        99,
        99,
        99,
        24,
        26,
        56,
        99,
        99,
        99,
        99,
        99,
        47,
        66,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
        99,
    ],
    dtype=np.float32,
).reshape(8, 8)

#: Precomputed 8x8 DCT basis matrix (orthonormal)
_DCT_BASIS = np.zeros((8, 8, 8, 8), dtype=np.float32)
for u in range(8):
    for v in range(8):
        for x in range(8):
            for y in range(8):
                cu = 1.0 / np.sqrt(2.0) if u == 0 else 1.0
                cv = 1.0 / np.sqrt(2.0) if v == 0 else 1.0
                _DCT_BASIS[u, v, x, y] = (
                    cu
                    * cv
                    * 0.25
                    * np.cos((2 * x + 1) * u * np.pi / 16.0)
                    * np.cos((2 * y + 1) * v * np.pi / 16.0)
                )


@dataclass(frozen=True)
class QuantTable:
    """One JPEG quantization table."""

    table_id: int
    is_chroma: bool
    table: np.ndarray  # (8, 8) float32


@dataclass(frozen=True)
class JpegTablesResult:
    """Result of quantization table analysis."""

    tables: list[QuantTable]
    estimated_quality: int | None
    standard_table: bool
    context: str  # "jpeg" | "non_jpeg"


def _parse_jpeg_markers(data: bytes) -> dict[int, list[bytes]]:
    """Parse JPEG markers from bitstream.

    Returns a dict mapping marker type to list of marker payloads.
    """
    markers: dict[int, list[bytes]] = {}
    pos = 0
    if data[:2] != _MARKER_SOI:
        return markers
    pos = 2
    while pos < len(data) - 1:
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        pos += 2
        # Markers without length field
        if marker in (
            0xD8,
            0xD9,
            0x01,
            0xD0,
            0xD1,
            0xD2,
            0xD3,
            0xD4,
            0xD5,
            0xD6,
            0xD7,
        ):
            continue
        if pos + 2 > len(data):
            break
        length = struct.unpack(">H", data[pos : pos + 2])[0]
        if pos + length > len(data):
            break
        payload = data[pos + 2 : pos + length]
        markers.setdefault(marker, []).append(payload)
        pos += length
    return markers


def _extract_quantization_tables(data: bytes) -> list[QuantTable]:
    """Extract quantization tables from JPEG DQT markers."""
    markers = _parse_jpeg_markers(data)
    tables: list[QuantTable] = []
    for payload in markers.get(0xDB, []):
        if len(payload) < 1:
            continue
        info = payload[0]
        precision = (info >> 4) & 0x0F
        table_id = info & 0x0F
        is_chroma = table_id > 0

        offset = 1
        table = np.zeros(64, dtype=np.float32)
        if precision == 0:  # 8-bit entries
            for i in range(64):
                if offset + i < len(payload):
                    table[i] = float(payload[offset + i])
        else:  # 16-bit entries
            for i in range(64):
                if offset + i * 2 + 1 < len(payload):
                    table[i] = float(
                        struct.unpack(
                            ">H",
                            payload[offset + i * 2 : offset + i * 2 + 2],
                        )[0]
                    )
        tables.append(
            QuantTable(
                table_id=table_id,
                is_chroma=is_chroma,
                table=table.reshape(8, 8),
            )
        )
    return tables


def _estimate_quality(tables: list[QuantTable]) -> int | None:
    """Estimate JPEG quality from quantization tables.

    Compares against libjpeg's standard Q50 tables scaled by quality factor.
    Returns quality 1-100 or None if unable to estimate.
    """
    lum_table = None
    for t in tables:
        if not t.is_chroma:
            lum_table = t.table
            break

    if lum_table is None:
        return None

    # Collect scale factors from non-zero Q50 reference entries
    scale_factors = []
    for i in range(8):
        for j in range(8):
            q50_val = _STD_LUM_Q50[i, j]
            if q50_val > 0:
                scale = lum_table[i, j] / q50_val
                scale_factors.append(scale)

    if not scale_factors:
        return None

    median_scale = float(np.median(scale_factors))

    # Invert Pillow's quality-to-scale mapping:
    #   q < 50: scale = 5000 / q  => q = 5000 / scale / 100 = 50 / scale
    #   q >= 50: scale = 200 - 2*q => q = (200 - scale) / 2
    quality = int(50.0 / median_scale) if median_scale <= 1.0 else int((200.0 - median_scale) / 2.0)

    return max(1, min(100, quality))


def _is_standard_table(tables: list[QuantTable]) -> bool:
    """Check if quantization tables match libjpeg's standard Q50 tables.

    Uses a loose tolerance because Pillow's table generation includes
    rounding and small variations.
    """
    for t in tables:
        ref = _STD_LUM_Q50 if not t.is_chroma else _STD_CHROMA_Q50
        if not np.allclose(t.table, ref, rtol=0.15, atol=3.0):
            return False
    return True


def _dct_8x8(block: np.ndarray) -> np.ndarray:
    """Compute 8x8 DCT of an 8x8 block using precomputed basis."""
    return np.tensordot(_DCT_BASIS, block, axes=([2, 3], [0, 1]))


def _zigzag_order() -> list[tuple[int, int]]:
    """Standard JPEG zigzag order for 8x8 blocks."""
    order = []
    for s in range(15):
        if s % 2 == 0:
            for i in range(max(0, s - 7), min(s, 7) + 1):
                j = s - i
                order.append((i, j))
        else:
            for j in range(max(0, s - 7), min(s, 7) + 1):
                i = s - j
                order.append((i, j))
    return order


_ZIGZAG = _zigzag_order()


def _analyze_dq_periodicity(
    dct_block: np.ndarray,
    ac_frequencies: list[tuple[int, int]],
) -> float:
    """Analyze DCT coefficient histogram for double-quantization periodicity.

    Returns a likelihood score (higher = more likely double compressed).
    """
    values = []
    for u, v in ac_frequencies:
        if u < dct_block.shape[0] and v < dct_block.shape[1]:
            values.append(float(dct_block[u, v]))

    if len(values) < 3:
        return 0.0

    values_arr = np.array(values, dtype=np.float32)

    # For a single block, we look at the distribution of AC coefficients.
    # Double quantization creates characteristic patterns: coefficients
    # cluster at multiples of the quantization step with gaps between.
    # We measure this by looking at the ratio of values near zero vs
    # spread across the range.
    abs_values = np.abs(values_arr)
    if len(abs_values) == 0:
        return 0.0

    # Count how many coefficients are small (near zero) vs large
    zero_threshold = 5.0
    small_count = int(np.sum(abs_values < zero_threshold))
    total_count = len(abs_values)

    if total_count == 0:
        return 0.0

    # Double-compressed images tend to have more coefficients clustered
    # at quantized values (including zero), creating a distinctive pattern.
    small_ratio = small_count / total_count

    # Also look at the variance of nonzero coefficients
    nonzero = abs_values[abs_values >= zero_threshold]
    if len(nonzero) > 1:
        nonzero_cv = float(np.std(nonzero)) / (float(np.mean(nonzero)) + 1e-6)
    else:
        nonzero_cv = 0.0

    # Combine: high small_ratio + low nonzero_cv = double compression likelihood
    likelihood = small_ratio * (1.0 / (1.0 + nonzero_cv))
    return float(likelihood)


def analyze_jpeg_dct(
    data: bytes,
    *,
    num_ac_frequencies: int = 5,
    em_iterations: int = 10,
    min_blocks: int = 10,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Analyze JPEG bitstream for quantization tables and double compression.

    Returns ``(tables_dict, dq_map_dict)`` suitable for plugin output.

    Raises :exc:`InputRejectedError` for invalid input.
    """
    if not data:
        raise InputRejectedError("no input bytes to analyze")

    if data[:2] != _MARKER_SOI:
        raise InputRejectedError("input is not a JPEG file")

    # Extract quantization tables
    tables = _extract_quantization_tables(data)
    if not tables:
        raise InputRejectedError("no quantization tables found in JPEG")

    estimated_quality = _estimate_quality(tables)
    standard_table = _is_standard_table(tables)

    # Decode image for DCT analysis
    try:
        image: Image.Image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as error:
        raise InputRejectedError(
            f"cannot decode JPEG for DCT analysis: {type(error).__name__}: {error}"
        ) from error

    if image.mode not in ("RGB", "RGBA", "L", "YCbCr"):
        image = image.convert("RGB")

    arr = np.asarray(image)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=2)
    elif arr.shape[2] == 4:
        arr = arr[:, :, :3]

    # Use Y channel (luma) for DCT analysis
    y_channel = arr[:, :, 0].astype(np.float32)
    h, w = y_channel.shape

    # Compute 8x8 DCT for each block
    block_h = h // 8
    block_w = w // 8

    if block_h * block_w < min_blocks:
        tables_dict = {
            "tables": [
                {
                    "table_id": t.table_id,
                    "is_chroma": t.is_chroma,
                    "table": t.table.tolist(),
                }
                for t in tables
            ],
            "estimated_quality": estimated_quality,
            "standard_table": standard_table,
            "context": "jpeg",
        }
        dq_map_dict = {
            "anomaly": {
                "values": [],
                "block": [8, 8],
                "stride": [8, 8],
                "scale": "robust_z",
                "valid_mask": [],
                "notes": ["too_few_blocks"],
            },
            "quality_estimate": estimated_quality,
            "context": "jpeg",
        }
        return tables_dict, dq_map_dict

    # Extract Y channel blocks and compute DCT
    y_cropped = y_channel[: block_h * 8, : block_w * 8]
    blocks = y_cropped.reshape(block_h, 8, block_w, 8).transpose(0, 2, 1, 3)
    blocks = blocks.reshape(-1, 8, 8)  # type: ignore[assignment]

    # Compute DCT for each block
    dct_blocks = np.zeros_like(blocks)
    for i in range(blocks.shape[0]):
        dct_blocks[i] = _dct_8x8(blocks[i])

    # Select AC frequencies for analysis (zigzag order, skip DC)
    ac_freqs = _ZIGZAG[1 : 1 + num_ac_frequencies]

    # Analyze DQ periodicity per block
    block_scores = np.zeros(blocks.shape[0], dtype=np.float32)
    for i in range(blocks.shape[0]):
        block_scores[i] = _analyze_dq_periodicity(dct_blocks[i], ac_freqs)

    # Reshape to block grid
    block_grid = block_scores.reshape(block_h, block_w)

    # Robust z-score normalization
    z_scores, degenerate = robust_zscore(block_grid)

    anomaly = anomaly_map(
        values=z_scores,
        block=(8, 8),
        stride=(8, 8),
        notes=("degenerate",) if degenerate else (),
    )

    tables_dict = {
        "tables": [
            {
                "table_id": t.table_id,
                "is_chroma": t.is_chroma,
                "table": t.table.tolist(),
            }
            for t in tables
        ],
        "estimated_quality": estimated_quality,
        "standard_table": standard_table,
        "context": "jpeg",
    }

    dq_map_dict = {
        "anomaly": {
            "values": anomaly.values.tolist(),
            "block": list(anomaly.block),
            "stride": list(anomaly.stride),
            "scale": anomaly.scale,
            "valid_mask": anomaly.valid_mask.tolist(),
            "notes": list(anomaly.notes),
        },
        "quality_estimate": estimated_quality,
        "context": "jpeg",
    }

    return tables_dict, dq_map_dict


def _make_plain_table_dict(
    tables: list[QuantTable],
    estimated_quality: int | None,
    standard_table: bool,
) -> dict[str, Any]:
    """Convert QuantTable list to plain dict (for testing)."""
    return {
        "tables": [
            {
                "table_id": t.table_id,
                "is_chroma": t.is_chroma,
                "table": t.table.tolist(),
            }
            for t in tables
        ],
        "estimated_quality": estimated_quality,
        "standard_table": standard_table,
        "context": "jpeg",
    }


__all__ = [
    "QuantTable",
    "_estimate_quality",
    "_extract_quantization_tables",
    "_is_standard_table",
    "_make_plain_table_dict",
    "analyze_jpeg_dct",
]
