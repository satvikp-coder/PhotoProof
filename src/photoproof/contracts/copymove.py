"""Source-bound version-2 copy-move interchange, with bounded numeric transport."""

import io
import json
import math
import re
import struct
import zipfile
from dataclasses import asdict, dataclass
from typing import Literal, Self

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from photoproof.contracts.arrays import decode_npz_numeric
from photoproof.contracts.exceptions import InputRejectedError

_MAX_BYTES = 64 << 20
_MAX_META = 1 << 20


def _number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise InputRejectedError(f"copy-move {label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise InputRejectedError(f"copy-move {label} exceeds its numeric range") from error
    if not math.isfinite(result):
        raise InputRejectedError(f"copy-move {label} must be finite")
    return result


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value < 2**31:
        raise InputRejectedError(f"copy-move {label} must be a bounded integer >= {minimum}")
    return value


def _box(value: object) -> None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise InputRejectedError("copy-move boxes need [y0,x0,y1,x1]")
    for coordinate in value:
        _integer(coordinate, label="box coordinate")
    if value[2] <= value[0] or value[3] <= value[1]:
        raise InputRejectedError("copy-move box upper bounds must be exclusive and increasing")


def _array(value: object, *, dtype: str, ndim: int) -> np.ndarray:
    if (
        not isinstance(value, np.ndarray)
        or value.ndim != ndim
        or value.size > 16_777_216
        or value.dtype.kind not in "iuf"
        or not np.isfinite(value).all()
    ):
        raise InputRejectedError(
            "copy-move arrays need bounded finite numeric values and valid rank"
        )
    target = np.dtype(dtype)
    if target.kind in "iu" and (
        value.dtype.kind not in "iu"
        or (value.size and (np.any(value < 0) or np.any(value > np.iinfo(target).max)))
    ):
        raise InputRejectedError(
            "copy-move integer values cannot be truncated, wrapped or negative"
        )
    with np.errstate(over="ignore"):
        result = np.asarray(value, dtype=target, order="C")
    if not np.isfinite(result).all():
        raise InputRejectedError("copy-move array exceeds its canonical numeric range")
    # Back with immutable bytes, not merely an owned array whose writeable flag
    # could be re-enabled after validation.
    return np.frombuffer(result.tobytes(order="C"), dtype=target).reshape(result.shape)


def _no_constant(value: str) -> None:
    raise InputRejectedError("copy-move JSON cannot contain NaN or infinity")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InputRejectedError("copy-move JSON contains duplicate fields")
        result[key] = value
    return result


def _json(text: str) -> dict[str, object]:
    if not isinstance(text, str) or len(text) > _MAX_META or len(text.encode("utf-8")) > _MAX_META:
        raise InputRejectedError("copy-move metadata exceeds its UTF-8 ceiling")
    try:
        value = json.loads(text, parse_constant=_no_constant, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise InputRejectedError("invalid copy-move JSON metadata") from error
    if not isinstance(value, dict) or value.get("contract_version") != "2.0.0":
        raise InputRejectedError("unsupported copy-move contract; preserve historical artifacts")
    return value


def _pack(meta: dict[str, object], **arrays: np.ndarray) -> bytes:
    text = json.dumps(meta, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(text) > _MAX_META or sum(a.nbytes for a in arrays.values()) + len(text) > _MAX_BYTES:
        raise InputRejectedError("copy-move archive exceeds its metadata or expanded-byte ceiling")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in {"metadata": np.frombuffer(text, dtype=np.uint8), **arrays}.items():
            part = io.BytesIO()
            np.save(part, array, allow_pickle=False)
            info = zipfile.ZipInfo(name + ".npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, part.getvalue())
    if stream.tell() > _MAX_BYTES:
        raise InputRejectedError("copy-move archive including headers exceeds its byte ceiling")
    return stream.getvalue()


def _unpack(data: bytes, names: frozenset[str]) -> tuple[dict[str, object], dict[str, np.ndarray]]:
    arrays = decode_npz_numeric(data, names=names | {"metadata"})
    metadata = arrays.pop("metadata")
    if metadata.dtype != np.uint8 or metadata.ndim != 1 or metadata.size > _MAX_META:
        raise InputRejectedError("copy-move metadata must be bounded UTF-8 bytes")
    try:
        return _json(metadata.tobytes().decode("utf-8")), arrays
    except UnicodeError as error:
        raise InputRejectedError("copy-move metadata is not valid UTF-8") from error


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    contract_version: Literal["2.0.0"] = "2.0.0"
    source_sha256: str
    image_size: tuple[int, int]  # working [width,height]
    coordinate_space: Literal["working_pixels"] = "working_pixels"
    calibration: Literal["uncalibrated"] = "uncalibrated"

    @field_validator("source_sha256")
    @classmethod
    def _source(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise InputRejectedError("copy-move source must be an explicit SHA-256")
        return value

    @field_validator("image_size", mode="before")
    @classmethod
    def _size(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise InputRejectedError("copy-move image_size needs [width,height]")
        for dimension in value:
            _integer(dimension, label="image dimension", minimum=1)
        return value

    def _meta(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "source_sha256": self.source_sha256,
            "image_size": list(self.image_size),
            "coordinate_space": self.coordinate_space,
            "calibration": self.calibration,
        }

    @classmethod
    def _read(cls, values: dict[str, object], *, source_sha256: str | None = None) -> Self:
        if source_sha256 is not None and values.get("source_sha256") != source_sha256:
            raise InputRejectedError("copy-move source differs from the expected image geometry")
        try:
            return cls.model_validate(values)
        except ValidationError as error:
            raise InputRejectedError("invalid or unsupported copy-move contract fields") from error


class Keypoints(_Contract):
    """Working [x,y] coordinates and aligned SIFT attributes, never box coordinates."""

    xy: np.ndarray
    scale: np.ndarray
    angle: np.ndarray
    response: np.ndarray
    desc: np.ndarray

    @field_validator("xy", "desc", mode="before")
    @classmethod
    def _matrix(cls, value: object) -> np.ndarray:
        return _array(value, dtype="float32", ndim=2)

    @field_validator("scale", "angle", "response", mode="before")
    @classmethod
    def _vector(cls, value: object) -> np.ndarray:
        return _array(value, dtype="float32", ndim=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        n = len(self.xy)
        if (
            self.xy.shape != (n, 2)
            or self.desc.shape != (n, 128)
            or any(a.shape != (n,) for a in (self.scale, self.angle, self.response))
        ):
            raise InputRejectedError("keypoint coordinates, descriptors and attributes disagree")
        if (
            np.any(self.xy < 0)
            or np.any(self.xy[:, 0] >= self.image_size[0])
            or np.any(self.xy[:, 1] >= self.image_size[1])
            or np.any(self.scale <= 0)
        ):
            raise InputRejectedError("keypoint coordinates/scale are outside their working image")
        return self

    def to_npz(self) -> bytes:
        return _pack(
            self._meta(),
            xy=self.xy,
            scale=self.scale,
            angle=self.angle,
            response=self.response,
            desc=self.desc,
        )

    @classmethod
    def from_npz(cls, data: bytes, *, source_sha256: str | None = None) -> Self:
        meta, arrays = _unpack(data, frozenset({"xy", "scale", "angle", "response", "desc"}))
        return cls._read({**meta, **arrays}, source_sha256=source_sha256)


class Matches(_Contract):
    pairs: np.ndarray
    distance: np.ndarray
    num_keypoints: int

    @field_validator("num_keypoints", mode="before")
    @classmethod
    def _count(cls, value: object) -> int:
        return _integer(value, label="keypoint count")

    @field_validator("pairs", mode="before")
    @classmethod
    def _pairs(cls, value: object) -> np.ndarray:
        return _array(value, dtype="int32", ndim=2)

    @field_validator("distance", mode="before")
    @classmethod
    def _distance(cls, value: object) -> np.ndarray:
        return _array(value, dtype="float32", ndim=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.pairs.shape != (len(self.distance), 2) or np.any(self.distance < 0):
            raise InputRejectedError(
                "match pairs/distances have invalid shapes or negative distances"
            )
        if np.any(self.pairs >= self.num_keypoints):
            raise InputRejectedError("match indices exceed their explicit source keypoint count")
        return self

    def to_npz(self) -> bytes:
        return _pack(
            {**self._meta(), "num_keypoints": self.num_keypoints},
            pairs=self.pairs,
            distance=self.distance,
        )

    @classmethod
    def from_npz(cls, data: bytes, *, source_sha256: str | None = None) -> Self:
        meta, arrays = _unpack(data, frozenset({"pairs", "distance"}))
        return cls._read({**meta, **arrays}, source_sha256=source_sha256)


@dataclass(frozen=True)
class Cluster:
    id: int
    pair_indices: tuple[int, ...]
    source_bbox: tuple[int, int, int, int]
    target_bbox: tuple[int, int, int, int]
    mean_displacement: tuple[float, float]  # [dy,dx]

    def __post_init__(self) -> None:
        _integer(self.id, label="cluster id")
        _box(self.source_bbox)
        _box(self.target_bbox)
        if len(self.pair_indices) > 1_000_000 or len(set(self.pair_indices)) != len(
            self.pair_indices
        ):
            raise InputRejectedError("cluster pair references must be bounded and unique")
        for index in self.pair_indices:
            _integer(index, label="cluster pair index")
        if len(self.mean_displacement) != 2:
            raise InputRejectedError("cluster displacement needs two finite values")
        displacement = tuple(_number(v, label="displacement") for v in self.mean_displacement)
        object.__setattr__(self, "pair_indices", tuple(self.pair_indices))
        object.__setattr__(self, "source_bbox", tuple(self.source_bbox))
        object.__setattr__(self, "target_bbox", tuple(self.target_bbox))
        object.__setattr__(self, "mean_displacement", displacement)


class Clusters(_Contract):
    clusters: tuple[Cluster, ...]
    num_matches: int

    @field_validator("num_matches", mode="before")
    @classmethod
    def _count(cls, value: object) -> int:
        return _integer(value, label="match count")

    @field_validator("clusters", mode="before")
    @classmethod
    def _clusters(cls, value: object) -> tuple[Cluster, ...]:
        if not isinstance(value, (list, tuple)) or len(value) > 1000:
            raise InputRejectedError("cluster collection must be bounded")
        try:
            return tuple(c if isinstance(c, Cluster) else Cluster(**c) for c in value)
        except (TypeError, ValueError) as error:
            raise InputRejectedError("invalid cluster fields") from error

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _integer(self.num_matches, label="match count")
        if len(self.clusters) > 1000 or len({c.id for c in self.clusters}) != len(self.clusters):
            raise InputRejectedError("cluster collection exceeds its bound or repeats ids")
        for cluster in self.clusters:
            if any(index >= self.num_matches for index in cluster.pair_indices):
                raise InputRejectedError("cluster references a nonexistent match")
            for box in (cluster.source_bbox, cluster.target_bbox):
                if box[2] > self.image_size[1] or box[3] > self.image_size[0]:
                    raise InputRejectedError("cluster box extends outside its working image")
            if (
                abs(cluster.mean_displacement[0]) >= self.image_size[1]
                or abs(cluster.mean_displacement[1]) >= self.image_size[0]
            ):
                raise InputRejectedError("cluster displacement exceeds the working image bounds")
        return self

    def to_json(self) -> str:
        text = json.dumps(
            {
                **self._meta(),
                "num_matches": self.num_matches,
                "clusters": [asdict(c) for c in self.clusters],
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(text.encode("utf-8")) > _MAX_META:
            raise InputRejectedError("cluster metadata exceeds its UTF-8 ceiling")
        return text

    @classmethod
    def from_json(cls, text: str, *, source_sha256: str | None = None) -> Self:
        return cls._read(_json(text), source_sha256=source_sha256)


@dataclass(frozen=True)
class VerifiedTransform:
    cluster_id: int
    matrix: np.ndarray
    inliers: int
    rmse: float
    scale: float
    rotation_deg: float
    score: float
    flags: tuple[str, ...]

    def __post_init__(self) -> None:
        _integer(self.cluster_id, label="verified cluster id")
        _integer(self.inliers, label="inlier count", minimum=1)
        matrix = _array(self.matrix, dtype="float64", ndim=2)
        for name in ("rmse", "scale", "rotation_deg", "score"):
            object.__setattr__(self, name, _number(getattr(self, name), label=name))
        if (
            matrix.shape != (2, 3)
            or not all(
                math.isfinite(v) for v in (self.rmse, self.scale, self.rotation_deg, self.score)
            )
            or self.rmse < 0
            or self.scale <= 0
            or not 0 <= self.score <= 1
        ):
            raise InputRejectedError("verified transform has invalid geometry or heuristic quality")
        with np.errstate(over="ignore", invalid="ignore"):
            determinant = float(np.linalg.det(matrix[:, :2]))
        if not math.isfinite(determinant) or abs(determinant) <= 1e-12:
            raise InputRejectedError("verified transform must have nonsingular finite geometry")
        if len(self.flags) > 16 or any(not isinstance(f, str) or len(f) > 128 for f in self.flags):
            raise InputRejectedError("verified transform flags exceed their metadata bound")
        object.__setattr__(self, "matrix", matrix)
        object.__setattr__(self, "flags", tuple(self.flags))


class Verified(_Contract):
    transforms: tuple[VerifiedTransform, ...]
    cluster_ids: tuple[int, ...]

    @field_validator("cluster_ids", mode="before")
    @classmethod
    def _ids(cls, value: object) -> tuple[int, ...]:
        if not isinstance(value, (list, tuple)) or len(value) > 1000:
            raise InputRejectedError("known cluster ids must be bounded")
        ids = tuple(_integer(v, label="known cluster id") for v in value)
        if len(set(ids)) != len(ids):
            raise InputRejectedError("known cluster ids must be unique")
        return ids

    @field_validator("transforms", mode="before")
    @classmethod
    def _transforms(cls, value: object) -> tuple[VerifiedTransform, ...]:
        if not isinstance(value, (list, tuple)) or len(value) > 1000:
            raise InputRejectedError("verified collection must be bounded")
        if any(not isinstance(t, VerifiedTransform) for t in value):
            raise InputRejectedError("verified matrices need explicit transform records")
        return tuple(value)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(self.transforms) > 1000 or len(self.cluster_ids) > 1000:
            raise InputRejectedError("verified collection exceeds its transform/cluster bound")
        for cluster_id in self.cluster_ids:
            _integer(cluster_id, label="known cluster id")
        if any(t.cluster_id not in self.cluster_ids for t in self.transforms):
            raise InputRejectedError("verified transform refers to a nonexistent cluster")
        return self

    def to_npz(self) -> bytes:
        matrices = np.array([t.matrix for t in self.transforms], dtype=np.float64).reshape(-1, 2, 3)
        records = [{k: v for k, v in asdict(t).items() if k != "matrix"} for t in self.transforms]
        return _pack(
            {**self._meta(), "cluster_ids": list(self.cluster_ids), "transforms": records},
            matrices=matrices,
        )

    @classmethod
    def from_npz(cls, data: bytes, *, source_sha256: str | None = None) -> Self:
        meta, arrays = _unpack(data, frozenset({"matrices"}))
        records = meta.get("transforms")
        matrices = arrays["matrices"]
        if (
            not isinstance(records, list)
            or len(records) > 1000
            or matrices.shape != (len(records), 2, 3)
        ):
            raise InputRejectedError("verified transform metadata/matrices disagree")
        try:
            meta["transforms"] = tuple(
                VerifiedTransform(matrix=matrices[i], **record) for i, record in enumerate(records)
            )
        except (TypeError, ValueError) as error:
            raise InputRejectedError("invalid verified transform fields") from error
        return cls._read(meta, source_sha256=source_sha256)


@dataclass(frozen=True)
class Region:
    kind: str
    bbox: tuple[int, int, int, int]
    area: int

    def __post_init__(self) -> None:
        if self.kind not in {"source", "target"}:
            raise InputRejectedError("copy-move region kind must be source or target")
        _box(self.bbox)
        _integer(self.area, label="region area", minimum=1)
        object.__setattr__(self, "bbox", tuple(self.bbox))


class Mask(_Contract):
    mask: np.ndarray
    regions: tuple[Region, ...]

    @field_validator("regions", mode="before")
    @classmethod
    def _regions(cls, value: object) -> tuple[Region, ...]:
        if not isinstance(value, (list, tuple)) or len(value) > 1000:
            raise InputRejectedError("copy-move regions must be bounded")
        try:
            return tuple(r if isinstance(r, Region) else Region(**r) for r in value)
        except (TypeError, ValueError) as error:
            raise InputRejectedError("invalid region fields") from error

    @field_validator("mask", mode="before")
    @classmethod
    def _mask(cls, value: object) -> np.ndarray:
        return _array(value, dtype="uint8", ndim=2)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.mask.shape != (self.image_size[1], self.image_size[0]) or np.any(self.mask > 2):
            raise InputRejectedError("copy-move mask needs image-sized uint8 labels 0,1,2")
        if len(self.regions) > 1000:
            raise InputRejectedError("copy-move mask exceeds its region metadata bound")
        covered = np.zeros(self.mask.shape, dtype=bool)
        for region in self.regions:
            y0, x0, y1, x1 = region.bbox
            if (
                y1 > self.image_size[1]
                or x1 > self.image_size[0]
                or region.area
                != int(
                    np.count_nonzero(
                        self.mask[y0:y1, x0:x1] == (1 if region.kind == "source" else 2)
                    )
                )
            ):
                raise InputRejectedError("copy-move region area/bounds disagree with mask pixels")
            pixels = self.mask[y0:y1, x0:x1] == (1 if region.kind == "source" else 2)
            if np.any(covered[y0:y1, x0:x1] & pixels):
                raise InputRejectedError("copy-move region metadata counts the same pixels twice")
            covered[y0:y1, x0:x1] |= pixels
        if np.any((self.mask != 0) & ~covered):
            raise InputRejectedError("copy-move mask pixels lack region metadata")
        return self

    def to_npz(self) -> bytes:
        return _pack({**self._meta(), "regions": [asdict(r) for r in self.regions]}, mask=self.mask)

    def to_json(self) -> str:
        text = json.dumps(
            {**self._meta(), "regions": [asdict(r) for r in self.regions]},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(text.encode("utf-8")) > _MAX_META:
            raise InputRejectedError("mask metadata exceeds its UTF-8 ceiling")
        return text

    def to_png(self) -> bytes:
        """Encode semantic 0/1/2 labels, with no original image metadata."""
        stream = io.BytesIO()
        Image.fromarray(self.mask).save(stream, format="PNG", optimize=False, compress_level=9)
        return stream.getvalue()

    @classmethod
    def from_png(cls, data: bytes, *, metadata: str, source_sha256: str | None = None) -> Self:
        """Read a bounded derived mask; callers obtain bytes through the artifact broker."""
        meta = _json(metadata)
        base_keys = {
            "contract_version",
            "source_sha256",
            "image_size",
            "coordinate_space",
            "calibration",
        }
        if set(meta) != base_keys | {"regions"}:
            raise InputRejectedError("mask metadata has missing or unknown fields")
        source = _Contract._read(
            {k: v for k, v in meta.items() if k in base_keys}, source_sha256=source_sha256
        )
        if (
            not isinstance(data, bytes)
            or not 33 <= len(data) <= _MAX_BYTES
            or data[:8] != b"\x89PNG\r\n\x1a\n"
            or data[8:16] != b"\x00\x00\x00\rIHDR"
            or data[24:29] != b"\x08\x00\x00\x00\x00"
        ):
            raise InputRejectedError("mask needs bounded noninterlaced 8-bit grayscale PNG bytes")
        width, height = struct.unpack(">II", data[16:24])
        if (width, height) != source.image_size or width * height > 16_777_216:
            raise InputRejectedError(
                "mask PNG dimensions disagree with geometry or exceed the ceiling"
            )
        try:
            with Image.open(io.BytesIO(data)) as image:
                if (
                    image.mode != "L"
                    or image.size != source.image_size
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise InputRejectedError(
                        "mask PNG mode/frames disagree with its declared format"
                    )
                image.load()
                mask = np.asarray(image)
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
            raise InputRejectedError("invalid derived mask PNG; preserve the artifact") from error
        return cls._read({**meta, "mask": mask}, source_sha256=source_sha256)

    @classmethod
    def from_npz(cls, data: bytes, *, source_sha256: str | None = None) -> Self:
        meta, arrays = _unpack(data, frozenset({"mask"}))
        return cls._read({**meta, **arrays}, source_sha256=source_sha256)
