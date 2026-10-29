"""Unit tests for PhotoProof's initial classical visualizations."""

import numpy as np
import pytest
from PIL import Image

from photoproof.forensics import error_level_analysis, noise_residual


@pytest.fixture
def sample_image() -> Image.Image:
    pixels = np.zeros((64, 64, 3), dtype=np.uint8)
    pixels[:32, :32] = (220, 50, 40)
    pixels[32:, 32:] = (25, 180, 90)
    pixels[16:48, 16:48] = (80, 90, 210)
    return Image.fromarray(pixels, mode="RGB")


def test_ela_returns_rgb_image_with_same_dimensions(sample_image: Image.Image) -> None:
    result = error_level_analysis(sample_image)
    assert result.mode == "RGB"
    assert result.size == sample_image.size


def test_ela_accepts_grayscale_input() -> None:
    grayscale = Image.fromarray(np.arange(64 * 64, dtype=np.uint8).reshape(64, 64))
    result = error_level_analysis(grayscale, quality=85)
    assert result.mode == "RGB"
    assert result.size == grayscale.size


def test_ela_rejects_invalid_quality(sample_image: Image.Image) -> None:
    with pytest.raises(ValueError, match="quality"):
        error_level_analysis(sample_image, quality=0)


def test_ela_rejects_nonpositive_scale(sample_image: Image.Image) -> None:
    with pytest.raises(ValueError, match="scale"):
        error_level_analysis(sample_image, scale=0)


def test_noise_residual_returns_rgb_image_with_same_dimensions(
    sample_image: Image.Image,
) -> None:
    result = noise_residual(sample_image)
    assert result.mode == "RGB"
    assert result.size == sample_image.size


def test_noise_residual_rejects_nonpositive_sigma(sample_image: Image.Image) -> None:
    with pytest.raises(ValueError, match="sigma"):
        noise_residual(sample_image, sigma=0)


def test_noise_residual_rejects_nonpositive_amplification(
    sample_image: Image.Image,
) -> None:
    with pytest.raises(ValueError, match="amplification"):
        noise_residual(sample_image, amplification=-1)
