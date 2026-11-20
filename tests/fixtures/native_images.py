"""Reproducible actual pixels for controller acceptance; no external images or scores."""

from pathlib import Path

import numpy as np
from PIL import Image


def write_copy_image(path: Path) -> Path:
    pixels = np.full((384, 512, 3), 120, np.uint8)
    patch = np.random.default_rng(22).integers(0, 256, (96, 96, 3), np.uint8)
    pixels[32:128, 32:128] = patch
    pixels[240:336, 240:336] = patch
    Image.fromarray(pixels).save(path)
    return path
