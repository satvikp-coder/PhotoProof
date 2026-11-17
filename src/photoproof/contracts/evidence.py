"""Evidence-layer contract: a normalized, comparable representation of one detector's
output.

Every detector's native output (ELA error level, noise robust-z score, DQ probability,
copy-move correlation, etc.) lives on an incomparable scale. :class:`EvidenceLayer` is
the common representation produced by :mod:`fusion.normalize` (#32) so that
:mod:`fusion.rules` (#33) can combine heterogeneous detectors transparently.

Scores remain **uncalibrated heuristics** until #37 provides calibration data. The
``calibration`` field says so explicitly: ``"uncalibrated"`` or ``"calibrated:<dataset>"``.
Never represent an uncalibrated score as a calibrated probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class EvidenceLayer:
    """Normalized evidence from one detector.

    ``map`` is a float32 array in ``[0, 1]`` (or ``None`` when the detector could not
    produce a map). ``regions`` lists suspicious regions in detector-native coordinates.
    ``strength_class`` is one of ``"weak"``, ``"medium"``, ``"strong"``, fixed per
    detector in configuration with documented rationale. ``valid_mask`` marks blocks
    that contain valid data. ``calibration`` documents the score's provenance.
    """

    detector_id: str
    map: np.ndarray | None  # (H_b, W_b) float32 in [0, 1], or None
    regions: tuple[tuple[int, int, int, int], ...]  # (y0, x0, y1, x1) per region
    strength_class: str  # "weak" | "medium" | "strong"
    valid_mask: np.ndarray  # (H_b, W_b) bool
    calibration: str  # "uncalibrated" | "calibrated:<dataset>"
    notes: tuple[str, ...] = ()


class UnavailableEvidence(Protocol):
    """Marker protocol for a detector that could not produce evidence.

    A detector that failed, was not applicable, or is not installed yields an
    ``unavailable`` entry rather than a zero-score layer. Zero scores would be
    indistinguishable from genuinely clean evidence and are forbidden.
    """

    detector_id: str
    reason: str
