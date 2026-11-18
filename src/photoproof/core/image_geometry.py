"""Describe exact decoded-pixel to normalized-working-pixel coordinates."""

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.images import ImageGeometry
from photoproof.imaging.decoder import DecodedImage
from photoproof.imaging.normalization import WorkingImage


def describe_geometry(decoded: DecodedImage, working: WorkingImage) -> ImageGeometry:
    """Return JSON geometry for zero-based pixel centres, with no guessed rotation."""
    width, height = decoded.width, decoded.height
    orientation = decoded.orientation if working.orientation_applied else 1
    matrices = {
        1: ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        2: ((-1, 0, width - 1), (0, 1, 0), (0, 0, 1)),
        3: ((-1, 0, width - 1), (0, -1, height - 1), (0, 0, 1)),
        4: ((1, 0, 0), (0, -1, height - 1), (0, 0, 1)),
        5: ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
        6: ((0, -1, height - 1), (1, 0, 0), (0, 0, 1)),
        7: ((0, -1, height - 1), (-1, 0, width - 1), (0, 0, 1)),
        8: ((0, 1, 0), (-1, 0, width - 1), (0, 0, 1)),
    }
    expected = (height, width) if orientation in (5, 6, 7, 8) else (width, height)
    if orientation not in matrices or (working.width, working.height) != expected:
        raise InputRejectedError("image geometry disagrees with normalization; inspect transforms")
    return {
        "schema_version": 1,
        "source_sha256": decoded.source_sha256,
        "coordinate_convention": "zero_based_pixel_centres_xy",
        "decoded_size": [width, height],
        "working_size": [working.width, working.height],
        "source_orientation": decoded.orientation,
        "applied_orientation": orientation,
        "decoded_to_working": [list(row) for row in matrices[orientation]],
        "normalization_transforms": list(working.transforms),
    }
