"""Typed data contracts for the five-stage copy-move detection chain.

The copy-move chain is strictly sequential at runtime:

    keypoints -> matches -> clusters -> verified transforms -> masks

but the stages do not have to be developed sequentially. This module
freezes the interchange formats so that #20-#24 can be built in
parallel, each tested against realistic upstream data from the golden
example chain.

Each model carries ``contract_version`` so that changes to a contract
are a documented plugin-version decision (see the copy-move chain docs
page).

All arrays are NumPy ndarrays. Serialization uses NPZ for arrays and
JSON for scalar metadata; both round-trip exactly.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from photoproof.contracts.exceptions import InputRejectedError

_CONTRACT_VERSION = "1.0.0"


# ------------------------------------------------------------------ keypoints


class Keypoints(BaseModel):
    """SIFT-style keypoints extracted from an image.

    ``xy`` is ``(N, 2)`` float32 (row, col) coordinates. ``desc`` is
    ``(N, 128)`` float32 SIFT descriptors. All per-keypoint attributes
    have length ``N``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    contract_version: str = Field(default=_CONTRACT_VERSION)
    xy: np.ndarray = Field(...)  # (N, 2) float32
    scale: np.ndarray = Field(...)  # (N,) float32
    angle: np.ndarray = Field(...)  # (N,) float32
    response: np.ndarray = Field(...)  # (N,) float32
    desc: np.ndarray = Field(...)  # (N, 128) float32

    @field_validator("xy", "scale", "angle", "response", "desc")
    @classmethod
    def _float32(cls, value: np.ndarray) -> np.ndarray:
        return np.ascontiguousarray(value, dtype=np.float32)

    @model_validator(mode="after")
    def _consistent_lengths(self) -> Keypoints:
        n = self.xy.shape[0]
        for name in ("scale", "angle", "response"):
            attr = getattr(self, name)
            if attr.shape[0] != n:
                raise InputRejectedError(
                    f"keypoints: {name} length {attr.shape[0]} != xy length {n}"
                )
        if self.desc.shape[0] != n or self.desc.shape[1] != 128:
            raise InputRejectedError(f"keypoints: desc shape {self.desc.shape} != ({n}, 128)")
        if self.xy.shape[1] != 2:
            raise InputRejectedError(f"keypoints: xy shape {self.xy.shape} != ({n}, 2)")
        return self

    def to_npz(self) -> bytes:
        buffer = io.BytesIO()
        np.savez(
            buffer,
            contract_version=np.array(self.contract_version),
            xy=self.xy,
            scale=self.scale,
            angle=self.angle,
            response=self.response,
            desc=self.desc,
        )
        return buffer.getvalue()

    @classmethod
    def from_npz(cls, data: bytes) -> Keypoints:
        buffer = io.BytesIO(data)
        with np.load(buffer) as z:
            return cls(
                contract_version=str(z["contract_version"]),
                xy=z["xy"],
                scale=z["scale"],
                angle=z["angle"],
                response=z["response"],
                desc=z["desc"],
            )


# ------------------------------------------------------------------ matches


class Matches(BaseModel):
    """Descriptor matches between two keypoint sets.

    ``pairs`` is ``(M, 2)`` int32 indices into the source and target
    keypoint arrays. ``distance`` is ``(M,)`` float32 match distances.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    contract_version: str = Field(default=_CONTRACT_VERSION)
    pairs: np.ndarray = Field(...)  # (M, 2) int32
    distance: np.ndarray = Field(...)  # (M,) float32

    @field_validator("pairs")
    @classmethod
    def _int32(cls, value: np.ndarray) -> np.ndarray:
        return np.ascontiguousarray(value, dtype=np.int32)

    @field_validator("distance")
    @classmethod
    def _float32(cls, value: np.ndarray) -> np.ndarray:
        return np.ascontiguousarray(value, dtype=np.float32)

    @model_validator(mode="after")
    def _consistent_lengths(self) -> Matches:
        m = self.pairs.shape[0]
        if self.pairs.shape[1] != 2:
            raise InputRejectedError(f"matches: pairs shape {self.pairs.shape} != ({m}, 2)")
        if self.distance.shape[0] != m:
            raise InputRejectedError(
                f"matches: distance length {self.distance.shape[0]} != pairs length {m}"
            )
        return self

    def to_npz(self) -> bytes:
        buffer = io.BytesIO()
        np.savez(
            buffer,
            contract_version=np.array(self.contract_version),
            pairs=self.pairs,
            distance=self.distance,
        )
        return buffer.getvalue()

    @classmethod
    def from_npz(cls, data: bytes) -> Matches:
        buffer = io.BytesIO(data)
        with np.load(buffer) as z:
            return cls(
                contract_version=str(z["contract_version"]),
                pairs=z["pairs"],
                distance=z["distance"],
            )


# ------------------------------------------------------------------ clusters


@dataclass(frozen=True)
class Cluster:
    """One displacement cluster of matched keypoints."""

    id: int
    pair_indices: tuple[int, ...]  # indices into the Matches.pairs array
    source_bbox: tuple[int, int, int, int]  # (y0, x0, y1, x1)
    target_bbox: tuple[int, int, int, int]  # (y0, x0, y1, x1)
    mean_displacement: tuple[float, float]  # (dy, dx)


class Clusters(BaseModel):
    """List of displacement clusters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = Field(default=_CONTRACT_VERSION)
    clusters: tuple[Cluster, ...]

    def to_json(self) -> str:
        payload = {
            "contract_version": self.contract_version,
            "clusters": [
                {
                    "id": c.id,
                    "pair_indices": list(c.pair_indices),
                    "source_bbox": list(c.source_bbox),
                    "target_bbox": list(c.target_bbox),
                    "mean_displacement": list(c.mean_displacement),
                }
                for c in self.clusters
            ],
        }
        return json.dumps(payload, indent=2)

    @classmethod
    def from_json(cls, text: str) -> Clusters:
        payload = json.loads(text)
        return cls(
            contract_version=str(payload["contract_version"]),
            clusters=tuple(
                Cluster(
                    id=int(c["id"]),
                    pair_indices=tuple(int(i) for i in c["pair_indices"]),
                    source_bbox=tuple(int(v) for v in c["source_bbox"]),  # type: ignore[arg-type]
                    target_bbox=tuple(int(v) for v in c["target_bbox"]),  # type: ignore[arg-type]
                    mean_displacement=tuple(float(v) for v in c["mean_displacement"]),  # type: ignore[arg-type]
                )
                for c in payload["clusters"]
            ),
        )


# ------------------------------------------------------------------ verified transforms


@dataclass(frozen=True)
class VerifiedTransform:
    """One RANSAC-verified geometric transform for a cluster."""

    cluster_id: int
    matrix: np.ndarray  # (2, 3) float64 affine matrix
    inliers: int
    rmse: float
    scale: float
    rotation_deg: float
    score: float
    flags: tuple[str, ...]


class Verified(BaseModel):
    """List of RANSAC-verified transforms."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    contract_version: str = Field(default=_CONTRACT_VERSION)
    transforms: tuple[VerifiedTransform, ...]

    def to_npz(self) -> bytes:
        buffer = io.BytesIO()
        arrays: dict[str, np.ndarray] = {"contract_version": np.array(self.contract_version)}
        for i, t in enumerate(self.transforms):
            prefix = f"t{i}_"
            arrays[prefix + "matrix"] = np.asarray(t.matrix, dtype=np.float64)
            arrays[prefix + "inliers"] = np.array([t.inliers], dtype=np.int64)
            arrays[prefix + "rmse"] = np.array([t.rmse], dtype=np.float64)
            arrays[prefix + "scale"] = np.array([t.scale], dtype=np.float64)
            arrays[prefix + "rotation_deg"] = np.array([t.rotation_deg], dtype=np.float64)
            arrays[prefix + "score"] = np.array([t.score], dtype=np.float64)
            arrays[prefix + "cluster_id"] = np.array([t.cluster_id], dtype=np.int64)
            # Flags are stored as a comma-joined unicode string array.
            flags_str = ",".join(t.flags)
            arrays[prefix + "flags"] = np.array([flags_str], dtype="<U256")
        np.savez(buffer, **arrays)  # type: ignore[arg-type]
        return buffer.getvalue()

    @classmethod
    def from_npz(cls, data: bytes) -> Verified:
        buffer = io.BytesIO(data)
        with np.load(buffer) as z:
            contract_version = str(z["contract_version"])
            transforms: list[VerifiedTransform] = []
            i = 0
            while f"t{i}_matrix" in z:
                prefix = f"t{i}_"
                flags_str = str(z[prefix + "flags"][0])
                flags = tuple(flags_str.split(",")) if flags_str else ()
                transforms.append(
                    VerifiedTransform(
                        cluster_id=int(z[prefix + "cluster_id"][0]),
                        matrix=z[prefix + "matrix"],
                        inliers=int(z[prefix + "inliers"][0]),
                        rmse=float(z[prefix + "rmse"][0]),
                        scale=float(z[prefix + "scale"][0]),
                        rotation_deg=float(z[prefix + "rotation_deg"][0]),
                        score=float(z[prefix + "score"][0]),
                        flags=flags,
                    )
                )
                i += 1
            return cls(contract_version=contract_version, transforms=tuple(transforms))


# ------------------------------------------------------------------ masks


@dataclass(frozen=True)
class Region:
    """One localized region in a copy-move mask."""

    kind: str  # "source" or "target"
    bbox: tuple[int, int, int, int]  # (y0, x0, y1, x1)
    area: int  # pixel count


class Mask(BaseModel):
    """Copy-move localization mask with region list.

    The mask array is ``(H, W)`` uint8 with values:
        0 = background
        1 = source region
        2 = target region
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    contract_version: str = Field(default=_CONTRACT_VERSION)
    mask: np.ndarray  # (H, W) uint8
    regions: tuple[Region, ...]

    @field_validator("mask")
    @classmethod
    def _uint8(cls, value: np.ndarray) -> np.ndarray:
        return np.ascontiguousarray(value, dtype=np.uint8)

    @model_validator(mode="after")
    def _valid_values(self) -> Mask:
        unique = np.unique(self.mask)
        for v in unique:
            if int(v) not in (0, 1, 2):
                raise InputRejectedError(f"mask: invalid value {int(v)}; only 0, 1, 2 are allowed")
        return self

    def to_npz(self) -> bytes:
        buffer = io.BytesIO()
        np.savez(
            buffer,
            contract_version=np.array(self.contract_version),
            mask=self.mask,
        )
        regions_json = json.dumps(
            [
                {
                    "kind": r.kind,
                    "bbox": list(r.bbox),
                    "area": r.area,
                }
                for r in self.regions
            ]
        ).encode("utf-8")
        np.savez(
            buffer,
            contract_version=np.array(self.contract_version),
            mask=self.mask,
            regions=np.frombuffer(regions_json, dtype=np.uint8),
        )
        return buffer.getvalue()

    @classmethod
    def from_npz(cls, data: bytes) -> Mask:
        buffer = io.BytesIO(data)
        with np.load(buffer) as z:
            contract_version = str(z["contract_version"])
            mask = z["mask"]
            regions_bytes = z["regions"].tobytes()
            regions_payload = json.loads(regions_bytes.decode("utf-8"))
            return cls(
                contract_version=contract_version,
                mask=mask,
                regions=tuple(
                    Region(
                        kind=str(r["kind"]),
                        bbox=tuple(int(v) for v in r["bbox"]),  # type: ignore[arg-type]
                        area=int(r["area"]),
                    )
                    for r in regions_payload
                ),
            )


__all__ = [
    "Cluster",
    "Clusters",
    "Keypoints",
    "Mask",
    "Matches",
    "Region",
    "Verified",
    "VerifiedTransform",
]
