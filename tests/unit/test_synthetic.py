"""Tests for the synthetic forgery generators in tests.fixtures.synthetic."""

import numpy as np
import pytest

from tests.fixtures.synthetic import (
    blur_patch,
    copy_move,
    double_jpeg,
    jpeg,
    misaligned_jpeg_patch,
    noise_splice,
    splice,
    textured_scene,
    with_exif,
)


def _check_image(image: np.ndarray) -> None:
    assert isinstance(image, np.ndarray)
    assert image.ndim == 3
    assert image.shape[2] == 3
    assert image.dtype == np.uint8
    assert image.flags.c_contiguous


def _check_mask(mask: np.ndarray, h: int, w: int) -> None:
    assert mask.shape == (h, w)
    assert mask.dtype == np.uint8
    assert mask.flags.c_contiguous


def test_textured_scene_deterministic() -> None:
    a = textured_scene(32, 32, seed=7)
    b = textured_scene(32, 32, seed=7)
    assert np.array_equal(a, b)


def test_textured_scene_different_seeds() -> None:
    a = textured_scene(32, 32, seed=1)
    b = textured_scene(32, 32, seed=2)
    assert not np.array_equal(a, b)


def test_textured_scene_valid_image() -> None:
    img = textured_scene(64, 64, seed=3)
    _check_image(img)
    assert img.min() >= 0
    assert img.max() <= 255


def test_copy_move_returns_source_and_target_masks() -> None:
    img = textured_scene(64, 64, seed=5)
    fixture = copy_move(img, translate=(20, 20), rotate_deg=0.0, scale=1.0)
    _check_image(fixture.image)
    _check_mask(fixture.mask, 64, 64)
    assert 1 in fixture.mask  # source present
    assert 2 in fixture.mask  # target present


def test_copy_move_target_matches_source() -> None:
    img = textured_scene(64, 64, seed=9)
    fixture = copy_move(img, translate=(0, 0), rotate_deg=0.0, scale=1.0)
    source_mask = fixture.mask == 1
    target_mask = fixture.mask == 2
    assert source_mask.any()
    assert target_mask.any()
    # The target region should look like the source region (same pixels).
    source_pixels = fixture.image[source_mask]
    target_pixels = fixture.image[target_mask]
    min_n = min(source_pixels.shape[0], target_pixels.shape[0])
    assert np.allclose(source_pixels[:min_n], target_pixels[:min_n], atol=2)


def test_copy_move_invalid_channel_count() -> None:
    arr = np.zeros((8, 8, 1), dtype=np.uint8)
    arr = np.ascontiguousarray(arr)
    with pytest.raises(ValueError, match="3 channels"):
        copy_move(arr, translate=(0, 0))


def test_splice_returns_mask() -> None:
    host = textured_scene(64, 64, seed=11)
    donor = textured_scene(16, 16, seed=13)
    fixture = splice(host, donor, region=(10, 10, 26, 26))
    _check_image(fixture.image)
    _check_mask(fixture.mask, 64, 64)
    assert fixture.mask[10:26, 10:26].all()


def test_splice_region_outside_host() -> None:
    host = textured_scene(16, 16, seed=17)
    donor = textured_scene(8, 8, seed=19)
    with pytest.raises(ValueError, match="outside"):
        splice(host, donor, region=(10, 10, 20, 20))


def test_noise_splice_creates_local_inconsistency() -> None:
    img = textured_scene(64, 64, seed=21)
    fixture = noise_splice(img, sigma_in=0.0, sigma_out=0.5)
    _check_image(fixture.image)
    _check_mask(fixture.mask, 64, 64)
    assert fixture.mask.any()


def test_blur_patch_marks_region() -> None:
    img = textured_scene(64, 64, seed=23)
    fixture = blur_patch(img, sigma=2.0)
    _check_image(fixture.image)
    _check_mask(fixture.mask, 64, 64)
    assert fixture.mask.any()


def test_jpeg_round_trip() -> None:
    img = textured_scene(32, 32, seed=25)
    out, meta = jpeg(img, quality=85)
    _check_image(out)
    assert meta["quality"] == 85


def test_double_jpeg_full_and_region() -> None:
    img = textured_scene(32, 32, seed=27)
    out_full, meta_full = double_jpeg(img, q1=90, q2=75)
    _check_image(out_full)
    assert meta_full["region"] is None

    out_region, meta_region = double_jpeg(img, q1=90, q2=75, region=(8, 8, 24, 24))
    _check_image(out_region)
    assert meta_region["region"] == [8, 8, 24, 24]


def test_double_jpeg_invalid_quality() -> None:
    img = textured_scene(16, 16, seed=29)
    with pytest.raises(ValueError):
        double_jpeg(img, q1=150, q2=75)


def test_misaligned_jpeg_patch() -> None:
    img = textured_scene(64, 64, seed=31)
    out, meta = misaligned_jpeg_patch(img, offset=(3, 5))
    _check_image(out)
    assert meta["offset"] == [3, 5]


def test_with_exif_attaches_tags() -> None:
    img = textured_scene(32, 32, seed=33)
    out, meta = with_exif(img, **{"270": "PhotoProof"})
    _check_image(out)
    assert "tags" in meta


def test_all_generators_return_contiguous() -> None:
    img = textured_scene(48, 48, seed=37)
    generators = [
        lambda: copy_move(img, translate=(0, 0), rotate_deg=0.0, scale=1.0),
        lambda: splice(img, img[8:16, 8:16], region=(20, 20, 28, 28)),
        lambda: noise_splice(img, sigma_in=0.0, sigma_out=0.3),
        lambda: blur_patch(img, sigma=1.5),
    ]
    for gen in generators:
        fixture = gen()
        assert fixture.image.flags.c_contiguous
        assert fixture.mask.flags.c_contiguous
