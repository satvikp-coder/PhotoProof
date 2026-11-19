"""Project measured score cells with explicit footprints and EXIF coordinates."""

import math
from dataclasses import dataclass
from typing import Self

import numpy as np

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.images import ImageGeometry


@dataclass(frozen=True)
class SamplingGrid:
    """Decoded-raster cells: origin/stride/block are [x,y] in pixel units.

    Origin is the index of the first pixel covered by cell zero. The final cell
    footprint is clipped to the source image, so partial border cells retain
    their actual centres instead of pretending to have a full padded extent.
    """

    image_size: tuple[int, int]
    origin_xy: tuple[float, float]
    stride_xy: tuple[float, float]
    block_xy: tuple[float, float]

    def to_record(self) -> dict[str, object]:
        """Return compact JSON metadata; array values belong in separate artifacts."""
        return {
            "image_size": list(self.image_size),
            "origin_xy": list(self.origin_xy),
            "stride_xy": list(self.stride_xy),
            "block_xy": list(self.block_xy),
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        """Read only the explicit sampling schema, without guessed dimensions."""
        if not isinstance(value, dict) or set(value) != {
            "image_size",
            "origin_xy",
            "stride_xy",
            "block_xy",
        }:
            raise InputRejectedError("sampling grid metadata is missing or has unknown fields")
        image_size = _positive_pair(value["image_size"], name="grid image_size")
        origin = _sampling_pair(value["origin_xy"], name="grid origin")
        stride = _sampling_pair(value["stride_xy"], name="grid stride")
        block = _sampling_pair(value["block_xy"], name="grid block")
        if min(origin) < 0 or min(stride) <= 0 or min(block) <= 0:
            raise InputRejectedError("sampling grid origin/stride/block bounds are invalid")
        return cls(image_size, origin, stride, block)


def _positive_pair(value: object, *, name: str) -> tuple[int, int]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or any(
            isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 2**31 - 1 for n in value
        )
    ):
        raise InputRejectedError(f"{name} must contain two positive 32-bit integers")
    return value[0], value[1]


def _sampling_pair(value: object, *, name: str) -> tuple[float, float]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or any(
            isinstance(n, bool)
            or not isinstance(n, (int, float))
            or not -(2**31 - 1) <= n <= 2**31 - 1
            or not math.isfinite(n)
            for n in value
        )
    ):
        raise InputRejectedError(f"{name} must contain two finite 32-bit-bounded numbers")
    return float(value[0]), float(value[1])


def _axis(
    *, size: int, count: int, origin: float, stride: float, block: float, positions: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    if (
        not all(math.isfinite(n) for n in (origin, stride, block))
        or origin < 0
        or stride <= 0
        or block <= 0
        or origin + (count - 1) * stride >= size
    ):
        raise InputRejectedError("score grid has invalid sampling or cells outside the image")
    starts = origin + np.arange(count, dtype=np.float64) * stride
    ends = np.minimum(starts + block, size)
    centres = (starts + ends - 1) / 2
    right = np.clip(np.searchsorted(centres, positions), 0, count - 1)
    left = np.maximum(right - 1, 0)
    indices = np.where(
        np.abs(positions - centres[left]) < np.abs(positions - centres[right]), left, right
    )
    supported = (positions >= starts[indices] - 0.5) & (positions < ends[indices] - 0.5)
    return indices, supported


def project_scores(
    scores: np.ndarray,
    valid_mask: np.ndarray,
    *,
    grid: SamplingGrid,
    geometry: ImageGeometry,
    output_shape: tuple[int, int],
    max_output_elements: int = 16_777_216,
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest measured cell at each output centre, with explicit support validity.

    A missing/invalid measurement remains invalid, never zero-score evidence.
    Zeros outside support are storage fill values and must travel with the mask.
    Output samples cover the working image uniformly; no interpolated suspicion
    values are invented. This is projection, not registration or calibration.
    """
    height, width = _positive_pair(output_shape, name="output_shape")
    if (
        isinstance(max_output_elements, bool)
        or not isinstance(max_output_elements, int)
        or max_output_elements < 1
        or height * width > max_output_elements
    ):
        raise InputRejectedError("score projection exceeds its output-element ceiling")
    grid = SamplingGrid.from_record(grid.to_record())
    source_width, source_height = grid.image_size
    if (
        not isinstance(scores, np.ndarray)
        or scores.ndim != 2
        or not scores.size
        or scores.dtype.kind not in "iuf"
        or not np.isfinite(scores).all()
        or np.any((scores < 0) | (scores > 1))
        or not isinstance(valid_mask, np.ndarray)
        or valid_mask.dtype != np.bool_
        or valid_mask.shape != scores.shape
    ):
        raise InputRejectedError(
            "score projection requires finite unit scores and matching bool mask"
        )
    origin, stride, block = grid.origin_xy, grid.stride_xy, grid.block_xy
    if any(
        origin[axis] + (count - 1) * stride[axis] >= grid.image_size[axis]
        for axis, count in enumerate((scores.shape[1], scores.shape[0]))
    ):
        raise InputRejectedError("score grid has cells outside its source image")
    if (
        not isinstance(geometry, dict)
        or geometry.get("schema_version") != 1
        or isinstance(geometry.get("schema_version"), bool)
        or not {"decoded_size", "working_size", "decoded_to_working", "coordinate_convention"}
        <= geometry.keys()
    ):
        raise InputRejectedError("score projection requires complete version-1 image geometry")
    if _positive_pair(geometry["decoded_size"], name="decoded_size") != (
        source_width,
        source_height,
    ):
        raise InputRejectedError("score grid source size disagrees with image geometry")
    working_width, working_height = _positive_pair(geometry["working_size"], name="working_size")
    try:
        matrix = np.asarray(geometry["decoded_to_working"])
    except (TypeError, ValueError, OverflowError) as error:
        raise InputRejectedError("score projection has an invalid pixel transform") from error
    if (
        geometry["coordinate_convention"] != "zero_based_pixel_centres_xy"
        or matrix.shape != (3, 3)
        or matrix.dtype.kind not in "iu"
        or np.any((matrix[:2, :2] < -1) | (matrix[:2, :2] > 1))
        or np.any((matrix[:2, 2] < 0) | (matrix[:2, 2] > max(source_width, source_height)))
        or not np.array_equal(matrix[2], [0, 0, 1])
        or not np.array_equal(matrix[:2, :2] @ matrix[:2, :2].T, np.eye(2, dtype=int))
    ):
        raise InputRejectedError("score projection requires an exact EXIF pixel transform")
    corners = (
        np.array(
            [
                [0, 0, 1],
                [source_width - 1, 0, 1],
                [0, source_height - 1, 1],
                [source_width - 1, source_height - 1, 1],
            ],
            dtype=np.int64,
        )
        @ matrix.T
    )
    if tuple(corners[:, :2].min(axis=0)) != (0, 0) or tuple(corners[:, :2].max(axis=0)) != (
        working_width - 1,
        working_height - 1,
    ):
        raise InputRejectedError("score projection transform disagrees with image bounds")
    # Signed permutation matrices are separable: avoid a full float coordinate mesh.
    axes = (
        (np.arange(width, dtype=np.float64) + 0.5) * working_width / width - 0.5,
        (np.arange(height, dtype=np.float64) + 0.5) * working_height / height - 0.5,
    )
    inverse = matrix[:2, :2].T
    positions = []
    for native_axis in range(2):
        working_axis = int(np.flatnonzero(inverse[native_axis])[0])
        positions.append(
            inverse[native_axis, working_axis] * (axes[working_axis] - matrix[working_axis, 2])
        )
    ix, valid_x = _axis(
        size=source_width,
        count=scores.shape[1],
        origin=origin[0],
        stride=stride[0],
        block=block[0],
        positions=positions[0],
    )
    iy, valid_y = _axis(
        size=source_height,
        count=scores.shape[0],
        origin=origin[1],
        stride=stride[1],
        block=block[1],
        positions=positions[1],
    )
    values = scores[np.ix_(iy, ix)]
    valid = valid_mask[np.ix_(iy, ix)] & valid_y[:, None] & valid_x[None, :]
    if inverse[0, 0] == 0:
        values, valid = values.T, valid.T
    return np.where(valid, values, 0).astype(np.float32), valid
