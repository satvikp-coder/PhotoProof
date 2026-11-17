"""Pure EXIF metadata analysis algorithm.

All functions here are deterministic, side-effect free, and operate only on
in-memory bytes. They are unit-testable without the pipeline or decoder chain.
"""

from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass
from typing import Any

from PIL import Image

from photoproof.contracts.exceptions import InputRejectedError

#: Maximum length for string values stored in findings/summary.
_MAX_STRING_LEN = 200

#: Tags considered "editor" indicators when present in Software/ProcessingSoftware
#: or XMP CreatorTool.
_EDITOR_KEYWORDS = (
    "photoshop",
    "gimp",
    "paint",
    "lightroom",
    "affinity",
    "pixelmator",
    "acdsee",
    "corel",
    "aftershot",
    "darktable",
    "rawtherapee",
    "snapseed",
    "instagram",
    "facebook",
    "twitter",
    "whatsapp",
    "telegram",
    "viber",
    "wechat",
    "line",
    "kik",
    "snapchat",
    "tiktok",
    "vsc",
    "facetune",
    "airbrush",
    "meitu",
    "b612",
    "cymera",
    "retouch",
    "editor",
    "editorx",
    "online editor",
    "image editor",
    "photo editor",
    "picture editor",
)

#: Impossible dates (before photography or in the far future).
_MIN_YEAR = 1900
_MAX_YEAR = 2099


@dataclass(frozen=True)
class Finding:
    """One typed metadata finding."""

    code: str
    severity: str  # "info" | "low" | "medium"
    message: str
    fields: tuple[str, ...]
    false_positive_note: str


@dataclass(frozen=True)
class Summary:
    """Normalized metadata summary."""

    camera: str | None
    lens: str | None
    timestamps: tuple[str, ...]  # sanitized, no GPS
    software: str | None
    gps_present: bool
    dimensions: tuple[int, int] | None
    thumbnail_present: bool


def _truncate(value: str, max_len: int = _MAX_STRING_LEN) -> str:
    """Truncate a string to a safe length."""
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _safe_str(value: object) -> str:
    """Convert any value to a safe string representation."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, ValueError):
            return "<binary>"
    return str(value)[:_MAX_STRING_LEN]


def _looks_like_editor(value: str | None) -> bool:
    """Return True if the value contains an editor keyword."""
    if not value:
        return False
    lower = value.lower()
    return any(kw in lower for kw in _EDITOR_KEYWORDS)


def _is_impossible_date(date_str: str | None) -> bool:
    """Return True if a date string is outside the plausible range."""
    if not date_str:
        return False
    # EXIF dates look like "YYYY:MM:DD HH:MM:SS"
    match = re.match(r"^(\d{4}):\d{2}:\d{2}", date_str)
    if not match:
        return True  # unparseable date
    year = int(match.group(1))
    return year < _MIN_YEAR or year > _MAX_YEAR


def _parse_exif(
    data: bytes, max_ifd_depth: int, max_exif_bytes: int
) -> tuple[dict[str, Any], list[Finding]]:
    """Parse EXIF metadata from JPEG APP1 bytes.

    Returns ``(tags, findings)`` where ``tags`` maps EXIF tag IDs to values and
    ``findings`` are metadata-level findings.
    """
    findings: list[Finding] = []
    tags: dict[str, Any] = {}

    if len(data) > max_exif_bytes:
        findings.append(
            Finding(
                code="metadata_oversized_exif",
                severity="low",
                message=(
                    f"EXIF block is {len(data)} bytes, over the "
                    f"{max_exif_bytes}-byte limit; truncated"
                ),
                fields=("exif_size",),
                false_positive_note=(
                    "Large EXIF blocks are common with embedded thumbnails or "
                    "maker notes; size alone is not suspicious."
                ),
            )
        )
        data = data[:max_exif_bytes]

    if len(data) < 6 or not data.startswith(b"Exif\x00\x00"):
        return tags, findings

    # TIFF header after "Exif\x00\x00"
    tiff = data[6:]
    if len(tiff) < 8:
        return tags, findings

    byte_order = tiff[:2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        return tags, findings

    magic = struct.unpack(endian + "H", tiff[2:4])[0]
    if magic != 42:
        return tags, findings

    ifd_offset = struct.unpack(endian + "I", tiff[4:8])[0]
    _parse_ifd(tiff, ifd_offset, endian, 0, max_ifd_depth, tags, findings)

    return tags, findings


def _parse_ifd(
    data: bytes,
    offset: int,
    endian: str,
    depth: int,
    max_depth: int,
    tags: dict[str, Any],
    findings: list[Finding],
) -> None:
    """Recursively parse one IFD, guarding against loops and excessive depth."""
    if depth > max_depth:
        findings.append(
            Finding(
                code="metadata_ifd_loop",
                severity="low",
                message=(f"EXIF IFD nesting exceeds {max_depth} levels; possible loop"),
                fields=("ifd_depth",),
                false_positive_note=(
                    "Deeply nested IFDs can occur with complex maker notes; "
                    "depth alone is not suspicious."
                ),
            )
        )
        return
    if offset + 2 > len(data):
        return
    entry_count = struct.unpack(endian + "H", data[offset : offset + 2])[0]
    pos = offset + 2
    for _ in range(entry_count):
        if pos + 12 > len(data):
            break
        tag_id = struct.unpack(endian + "H", data[pos : pos + 2])[0]
        field_type = struct.unpack(endian + "H", data[pos + 2 : pos + 4])[0]
        count = struct.unpack(endian + "I", data[pos + 4 : pos + 8])[0]
        value_offset = struct.unpack(endian + "I", data[pos + 8 : pos + 12])[0]
        pos += 12

        # Resolve the value (inline or offset)
        value: Any
        if field_type in (1, 2, 6, 7):  # BYTE, ASCII, UNDEFINED
            size = count if field_type != 2 else count + 1
            if size <= 4:
                value_bytes = data[pos - 4 : pos - 4 + size]
            else:
                if value_offset + size > len(data):
                    continue
                value_bytes = data[value_offset : value_offset + size]
            if field_type == 2:  # ASCII
                value = value_bytes.split(b"\x00")[0].decode("utf-8", errors="replace")
            else:
                value = value_bytes.hex()
        elif field_type in (3, 4, 8, 9, 10, 11, 12):  # SHORT, LONG, etc.
            size_map = {3: 2, 4: 4, 8: 1, 9: 4, 10: 8, 11: 8, 12: 12}
            elem_size = size_map.get(field_type, 1)
            total_size = elem_size * count
            if total_size <= 4:
                value_bytes = data[pos - 4 : pos - 4 + total_size]
            else:
                if value_offset + total_size > len(data):
                    continue
                value_bytes = data[value_offset : value_offset + total_size]
            if field_type == 3:
                value = list(struct.unpack(endian + "H" * count, value_bytes[: count * 2]))
            elif field_type == 4:
                value = list(struct.unpack(endian + "I" * count, value_bytes[: count * 4]))
            else:
                value = value_bytes.hex()
        else:
            continue

        tags[str(tag_id)] = value

    # Follow next IFD pointer
    ifd_end = offset + 2 + entry_count * 12 + 4
    if ifd_end <= len(data):
        next_ifd = struct.unpack(endian + "I", data[offset + 2 + entry_count * 12 : ifd_end - 4])[0]
        if next_ifd != 0 and next_ifd < len(data):
            _parse_ifd(data, next_ifd, endian, depth + 1, max_depth, tags, findings)


def _extract_xmp(data: bytes) -> tuple[dict[str, str], list[Finding]]:
    """Extract XMP metadata using string scanning (no XML parser, no XXE risk).

    Returns ``(tags, findings)``.
    """
    tags: dict[str, str] = {}
    findings: list[Finding] = []

    # Look for XMP packet markers
    start = data.find(b"<x:xmpmeta")
    if start < 0:
        # Also try rdf:RDF without xmpmeta wrapper
        start = data.find(b"<rdf:RDF")
    if start < 0:
        return tags, findings

    end = data.find(b"</x:xmpmeta>", start)
    if end < 0:
        end = data.find(b"</rdf:RDF>", start)
    if end < 0:
        end = start + 4096  # bounded read

    packet = data[start:end]
    # Simple tag extraction: <ns:tag>value</ns:tag>
    for match in re.finditer(rb"<[^:]+:([^>\s]+)[^>]*>([^<]*)</[^:]+:\1>", packet):
        tag_name = match.group(1).decode("utf-8", errors="replace")
        tag_value = match.group(2).decode("utf-8", errors="replace")
        tags[tag_name] = _truncate(tag_value)

    # Check for entity-expansion indicators
    if b"&entity;" in packet or b"<!ENTITY" in packet:
        findings.append(
            Finding(
                code="metadata_xmp_entities",
                severity="low",
                message="XMP packet contains entity declarations; parsing limited.",
                fields=("xmp",),
                false_positive_note=(
                    "Entity declarations in XMP are uncommon but not "
                    "inherently malicious; the parser ignores them."
                ),
            )
        )

    return tags, findings


def _compare_thumbnail(
    image: Image.Image, exif: dict[int, Any], hamming_threshold: int
) -> Finding | None:
    """Compare embedded JPEG thumbnail with downscaled main image using dHash.

    Returns a finding if the thumbnail is inconsistent, or None.
    """
    try:
        thumb = Image.open(io.BytesIO(exif[0x501B])) if 0x501B in exif else None
    except (OSError, ValueError, struct.error):
        return None
    if thumb is None:
        return None

    try:
        thumb = thumb.convert("L").resize((9, 8), Image.Resampling.LANCZOS)  # type: ignore[assignment]
        main_thumb = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None

    # dHash: compare adjacent pixels horizontally
    def dhash(img: Image.Image) -> int:
        pixels = list(img.getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                left = pixels[row * 9 + col]
                right = pixels[row * 9 + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return bits

    try:
        h1 = dhash(thumb)
        h2 = dhash(main_thumb)
        distance = bin(h1 ^ h2).count("1")
    except (OSError, ValueError):
        return None

    if distance > hamming_threshold:
        return Finding(
            code="metadata_thumbnail_mismatch",
            severity="medium",
            message=(
                f"Embedded JPEG thumbnail differs from downscaled main image "
                f"(dHash distance {distance}, threshold {hamming_threshold}); "
                f"the thumbnail may not have been regenerated after editing."
            ),
            fields=("thumbnail",),
            false_positive_note=(
                "Some cameras store thumbnails generated at capture time that "
                "naturally differ from a fresh downscale of the main image."
            ),
        )
    return None


def analyze_exif(
    data: bytes,
    *,
    config: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Analyze EXIF/XMP metadata from raw image bytes.

    Returns ``(summary_dict, findings_list)``. GPS coordinates are never
    included in the output.

    Raises :exc:`InputRejectedError` for empty or unreadable input.
    """
    if not data:
        raise InputRejectedError("no input bytes to analyze")

    max_ifd_depth = 10
    max_exif_bytes = 1 << 20
    hamming_threshold = 8
    include_software = True
    if config is not None:
        max_ifd_depth = getattr(config, "max_ifd_depth", max_ifd_depth)
        max_exif_bytes = getattr(config, "max_exif_bytes", max_exif_bytes)
        hamming_threshold = getattr(config, "thumbnail_hamming_threshold", hamming_threshold)
        include_software = getattr(config, "include_software", include_software)

    findings: list[Finding] = []

    # Decode image for dimensions and thumbnail comparison
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as error:
        raise InputRejectedError(
            f"cannot decode image for metadata analysis: {type(error).__name__}: {error}"
        ) from error

    width, height = image.size
    fmt = image.format or "unknown"

    # Extract EXIF
    exif = image.getexif()
    exif_tags: dict[int, Any] = dict(exif)
    _, raw_findings = _parse_exif(data, max_ifd_depth, max_exif_bytes)
    findings.extend(raw_findings)

    # Extract XMP
    xmp_tags, xmp_findings = _extract_xmp(data)
    findings.extend(xmp_findings)

    # Build summary
    camera = None
    lens = None
    software = None
    timestamps: list[str] = []
    gps_present = False

    # EXIF tag lookups (common tag IDs)
    make: Any = exif_tags.get(271)  # Make
    model: Any = exif_tags.get(272)  # Model
    if make or model:
        camera = " ".join(_safe_str(v) for v in (make, model) if v).strip() or None

    lens_val = exif_tags.get(42036)  # LensMake
    lens_model = exif_tags.get(42037)  # LensModel
    if lens_val or lens_model:
        lens = " ".join(_safe_str(v) for v in (lens_val, lens_model) if v).strip() or None

    if include_software:
        sw = exif_tags.get(305)  # Software
        xmp_tool = xmp_tags.get("CreatorTool")
        if sw:
            software = _safe_str(sw)
        elif xmp_tool:
            software = xmp_tool

    # Timestamps (sanitized)
    for tag_id in (306, 36867, 36868):  # DateTime, DateTimeOriginal, DateTimeDigitized
        val = exif_tags.get(tag_id)
        if val:
            s = _safe_str(val)
            if _is_impossible_date(s):
                findings.append(
                    Finding(
                        code="metadata_impossible_date",
                        severity="low",
                        message=f"EXIF date tag {tag_id} has an implausible value: {s}",
                        fields=(f"exif_{tag_id}",),
                        false_positive_note=(
                            "Incorrect camera clock settings are common and "
                            "not evidence of manipulation."
                        ),
                    )
                )
            timestamps.append(s)

    # GPS presence (boolean only, no coordinates)
    if any(k in exif_tags for k in (1, 2, 3, 4)):  # GPSLatitude, etc. (simplified)
        gps_present = True
    # Also check GPS IFD (tag 34853)
    if 34853 in exif_tags:
        gps_present = True

    thumbnail_present = 0x501B in exif_tags or 0x501C in exif_tags

    summary = Summary(
        camera=camera,
        lens=lens,
        timestamps=tuple(timestamps),
        software=software,
        gps_present=gps_present,
        dimensions=(width, height),
        thumbnail_present=thumbnail_present,
    )

    # Dimension check
    x_dim = exif_tags.get(0xA002)  # PixelXDimension
    y_dim = exif_tags.get(0xA003)  # PixelYDimension
    if x_dim is not None or y_dim is not None:
        x_val = int(x_dim) if x_dim is not None else -1
        y_val = int(y_dim) if y_dim is not None else -1
        if x_val != width or y_val != height:
            findings.append(
                Finding(
                    code="metadata_dimension_mismatch",
                    severity="medium",
                    message=(
                        f"EXIF PixelXDimension/PixelYDimension "
                        f"({x_val}, {y_val}) disagree with actual dimensions "
                        f"({width}, {height})."
                    ),
                    fields=("exif_dimensions", "actual_dimensions"),
                    false_positive_note=(
                        "Dimension fields can be stale after lossless cropping "
                        "or rotation; this is a weak signal."
                    ),
                )
            )

    # Software editor check
    if software and _looks_like_editor(software):
        findings.append(
            Finding(
                code="metadata_editor_detected",
                severity="info",
                message=(
                    f"Editing software detected: {software}; metadata may have "
                    f"been altered after capture."
                ),
                fields=("software",),
                false_positive_note=(
                    "Many legitimate images are processed with editors for "
                    "normal workflows (cropping, color correction). Editor "
                    "tags alone are not evidence of manipulation."
                ),
            )
        )

    # Missing metadata
    if not exif_tags and not xmp_tags:
        findings.append(
            Finding(
                code="metadata_absent",
                severity="info",
                message=(
                    "No EXIF or XMP metadata container found; provenance "
                    "cannot be assessed from metadata."
                ),
                fields=(),
                false_positive_note=(
                    "Many online services strip metadata by default; absence is not suspicious."
                ),
            )
        )

    # Thumbnail mismatch
    if thumbnail_present:
        thumb_finding = _compare_thumbnail(image, exif_tags, hamming_threshold)
        if thumb_finding:
            findings.append(thumb_finding)

    # Convert to plain dicts
    summary_dict: dict[str, Any] = {
        "camera": summary.camera,
        "lens": summary.lens,
        "timestamps": summary.timestamps,
        "software": summary.software,
        "gps_present": summary.gps_present,
        "dimensions": summary.dimensions,
        "thumbnail_present": summary.thumbnail_present,
        "format": fmt,
    }
    findings_list: list[dict[str, Any]] = [
        {
            "code": f.code,
            "severity": f.severity,
            "message": f.message,
            "fields": list(f.fields),
            "false_positive_note": f.false_positive_note,
        }
        for f in findings
    ]

    return summary_dict, findings_list
