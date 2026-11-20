"""Bounded accepted-rule contributions for source-bound segmentation and coverage."""

import re
from dataclasses import dataclass

import numpy as np

from photoproof.contracts.arrays import decode_npy, encode_npy
from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.fused_maps import MAX_FUSED_CELLS, FusedScores, validate_fused_scores
from photoproof.contracts.images import ImageGeometry
from photoproof.contracts.plugins import PluginContext

_BASE = {
    "format_version",
    "source_sha256",
    "working_size",
    "grid_shape",
    "coordinate_space",
    "calibration",
    "rule_version",
    "contributions",
    "unavailable",
}
_ENTRY = {
    "detector_id",
    "family",
    "strength_class",
    "effective_weight",
    "scope",
    "notes",
    "scores",
    "measured",
}


@dataclass(frozen=True)
class Contribution:
    detector_id: str
    family: str
    strength_class: str
    effective_weight: float
    scope: str
    notes: tuple[str, ...]
    scores: np.ndarray
    measured: np.ndarray


def _names(names: object) -> tuple[str, ...]:
    if (
        not isinstance(names, (list, tuple))
        or len(names) > 256
        or any(
            not isinstance(n, str)
            or len(n) > 100
            or not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", n)
            for n in names
        )
        or len(set(names)) != len(names)
    ):
        raise InputRejectedError(
            "contribution unavailable entries must be bounded unique contract names"
        )
    return tuple(names)


def _check(entry: Contribution, fused: FusedScores) -> None:
    _names([entry.detector_id])
    if (
        not isinstance(entry.family, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", entry.family)
        or not isinstance(entry.strength_class, str)
        or entry.strength_class not in {"weak", "medium", "strong"}
        or isinstance(entry.effective_weight, bool)
        or not isinstance(entry.effective_weight, (int, float))
        or not np.isfinite(entry.effective_weight)
        or not 0 <= entry.effective_weight <= 1
        or not isinstance(entry.scope, str)
        or entry.scope not in {"spatial", "global_context"}
        or not isinstance(entry.notes, (list, tuple))
        or len(entry.notes) > 16
        or any(not isinstance(note, str) or len(note) > 4096 for note in entry.notes)
        or sum(len(note.encode("utf-8")) for note in entry.notes) > 4096
        or not isinstance(entry.scores, np.ndarray)
        or entry.scores.dtype != np.float32
        or entry.scores.shape != fused.scores.shape
        or not np.isfinite(entry.scores).all()
        or np.any((entry.scores < 0) | (entry.scores > 1))
        or not isinstance(entry.measured, np.ndarray)
        or entry.measured.dtype != np.bool_
        or entry.measured.shape != entry.scores.shape
    ):
        raise InputRejectedError("contribution data, scope, metadata or numerical shape is invalid")
    if np.any(entry.scores > entry.effective_weight + 1e-6) or np.any(
        entry.scores > fused.scores + 1e-6
    ):
        raise InputRejectedError("contribution exceeds its effective weight or actual fused score")
    if np.any(entry.scores[~fused.valid_mask] != 0):
        raise InputRejectedError("contribution cannot claim pixels outside measured fused coverage")
    if entry.effective_weight == 0 and np.any(entry.scores):
        raise InputRejectedError("a zero-weight detector cannot contribute")
    if entry.scope == "global_context":
        if entry.strength_class != "weak" or entry.measured.any():
            raise InputRejectedError("global context cannot supply non-weak measured coverage")
    elif np.any(entry.scores[~entry.measured] != 0):
        raise InputRejectedError("spatial contribution cannot claim its unmeasured pixels")


def _collection(entries: tuple[Contribution, ...], fused: FusedScores) -> None:
    validate_fused_scores(fused)
    if (
        not 0 < len(entries) <= 32
        or fused.scores.ndim != 2
        or fused.scores.size > MAX_FUSED_CELLS
        or fused.scores.size * len(entries) > 8 * MAX_FUSED_CELLS
    ):
        raise InputRejectedError("contributions exceed their numerical budget or repeat a detector")
    for entry in entries:
        _check(entry, fused)
    if len({entry.detector_id for entry in entries}) != len(entries):
        raise InputRejectedError("contributions repeat a detector")


def write_contributions(
    ctx: PluginContext,
    entries: tuple[Contribution, ...],
    *,
    fused: FusedScores,
    unavailable: tuple[str, ...] = (),
) -> dict[str, object]:
    """Write accepted scores and measured validity without unbounded pixel JSON lists."""
    _collection(entries, fused)
    absent = _names(unavailable)
    if set(absent) & {entry.detector_id for entry in entries}:
        raise InputRejectedError("a contribution cannot also be listed unavailable")
    records = []
    for ordinal, entry in enumerate(entries):
        values, measured = encode_npy(entry.scores), encode_npy(entry.measured)
        records.append(
            {
                "detector_id": entry.detector_id,
                "family": entry.family,
                "strength_class": entry.strength_class,
                "effective_weight": entry.effective_weight,
                "scope": entry.scope,
                "notes": list(entry.notes),
                "scores": ctx.write_artifact(
                    f"contribution-{ordinal:03d}-scores.npy", values, "application/x-npy"
                ),
                "measured": ctx.write_artifact(
                    f"contribution-{ordinal:03d}-measured.npy", measured, "application/x-npy"
                ),
            }
        )
    return {
        "format_version": 1,
        "source_sha256": fused.source_sha256,
        "working_size": list(fused.working_size),
        "grid_shape": list(fused.scores.shape),
        "coordinate_space": "uniform_working_image_cells",
        "calibration": "uncalibrated",
        "rule_version": "3.0.0",
        "contributions": records,
        "unavailable": list(absent),
    }


def read_contributions(
    ctx: PluginContext,
    *,
    geometry: ImageGeometry,
    fused: FusedScores,
    name: str = "fusion.evidence",
) -> tuple[Contribution, ...]:
    """Verify source/frame/grid metadata before reading only declared exact nested selectors."""
    record = ctx.inputs.get(name)
    if (
        not isinstance(record, dict)
        or set(record) != _BASE
        or type(record["format_version"]) is not int
        or record["format_version"] != 1
        or record["source_sha256"] != geometry["source_sha256"]
        or record["source_sha256"] != fused.source_sha256
        or not isinstance(record["working_size"], list)
        or not isinstance(record["grid_shape"], list)
        or any(type(n) is not int for n in record["working_size"] + record["grid_shape"])
        or record["working_size"] != list(fused.working_size)
        or record["working_size"] != geometry["working_size"]
        or record["grid_shape"] != list(fused.scores.shape)
        or record["coordinate_space"] != "uniform_working_image_cells"
        or record["calibration"] != "uncalibrated"
        or record["rule_version"] != "3.0.0"
        or not isinstance(record["contributions"], list)
        or not 0 < len(record["contributions"]) <= 32
        or len(record["contributions"]) * fused.scores.size > 8 * MAX_FUSED_CELLS
    ):
        raise InputRejectedError(
            "unsupported contribution source, coordinates, version or grid budget"
        )
    absent = _names(record["unavailable"])
    entries = []
    for ordinal, entry in enumerate(record["contributions"]):
        if not isinstance(entry, dict) or set(entry) != _ENTRY:
            raise InputRejectedError("contribution entry has missing or unknown metadata")
        scores = decode_npy(
            ctx.artifact(name, selector=("contributions", ordinal, "scores")),
            max_elements=MAX_FUSED_CELLS,
            expected_ndim=2,
        )
        measured = decode_npy(
            ctx.artifact(name, selector=("contributions", ordinal, "measured")),
            max_elements=MAX_FUSED_CELLS,
            expected_ndim=2,
        )
        if not isinstance(entry["notes"], list):
            raise InputRejectedError("contribution notes must be bounded metadata")
        entries.append(
            Contribution(
                entry["detector_id"],
                entry["family"],
                entry["strength_class"],
                entry["effective_weight"],
                entry["scope"],
                tuple(entry["notes"]),
                scores,
                measured,
            )
        )
    result = tuple(entries)
    _collection(result, fused)
    if set(absent) & {entry.detector_id for entry in result}:
        raise InputRejectedError("a contribution cannot also be listed unavailable")
    return result
