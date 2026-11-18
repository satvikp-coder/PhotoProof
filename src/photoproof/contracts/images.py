"""JSON geometry accompanying host-provided decoded and working pixels."""

from typing import Literal, TypedDict


class ImageGeometry(TypedDict):
    """Sizes are [width,height]; the matrix maps integer-centred [x,y,1]."""

    schema_version: Literal[1]
    source_sha256: str
    coordinate_convention: Literal["zero_based_pixel_centres_xy"]
    decoded_size: list[int]
    working_size: list[int]
    source_orientation: int
    applied_orientation: int
    decoded_to_working: list[list[int]]
    normalization_transforms: list[str]
