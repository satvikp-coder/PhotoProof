"""Seeded synthetic forgery generators with exact ground-truth masks.

All generators return ``(image, mask, meta)``:

* ``image`` — an ``H x W x C`` ``uint8`` RGB NumPy array, C-contiguous.
* ``mask`` — an ``H x W`` ``uint8`` array where 0 = background,
  1 = source region, 2 = target region (copy-move) or 1 = manipulated region.
* ``meta`` — a dict with generator-specific details (transforms, parameters, seed).

Generators only process programmatic data; they never read external files.
Every generator is seeded so the same seed produces identical bytes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

from photoproof.imaging.arrays import validate_image


@dataclass(frozen=True)
class ForgeryFixture:
    """A synthetic image with known ground truth."""

    image: np.ndarray
    mask: np.ndarray
    meta: dict[str, object]


def _gaussian_kernel(size: int, sigma: float) -> np.ndarray:
    """1-D Gaussian kernel of odd ``size``."""
    if size % 2 == 0:
        size += 1
    ax = np.arange(-size // 2 + 1, size // 2 + 1)
    kernel = np.exp(-(ax**2) / (2 * sigma**2))
    return kernel / kernel.sum()


def _separable_filter(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Apply a separable 1-D kernel along both axes with edge reflection.

    Handles both 2-D (grayscale) and 3-D (colour) arrays by filtering each
    channel independently.
    """
    pad = int(len(kernel) // 2)
    pad_width = [(pad, pad), (pad, pad)] if image.ndim == 2 else [(pad, pad), (pad, pad), (0, 0)]
    padded = np.pad(image, pad_width=pad_width, mode="reflect")
    if image.ndim == 2:
        rows = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), 1, padded)
        cols = np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="valid"), 0, rows)
        return cols
    channels = []
    for c in range(image.shape[2]):
        channel = padded[:, :, c]
        rows = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), 1, channel)
        cols = np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="valid"), 0, rows)
        channels.append(cols)
    return np.stack(channels, axis=2)


def textured_scene(
    h: int, w: int, seed: int | None = None, *, noise_sigma: float = 0.12
) -> np.ndarray:
    """Generate a natural-looking textured scene as ``H x W x C`` ``uint8`` RGB.

    Built from filtered noise plus simple geometric shapes so keypoint
    detectors and noise statistics behave realistically. All values stay in
    ``[0, 255]``.
    """
    rng = np.random.default_rng(seed)
    base = rng.normal(0.5, noise_sigma, size=(h, w))
    kernel = _gaussian_kernel(9, 1.6)
    base = _separable_filter(base, kernel)
    base = (base - base.min()) / (base.max() - base.min() + 1e-9)

    image = np.stack([base, base, base], axis=2) * 255.0

    # Add a few simple shapes for structure.
    yy, xx = np.mgrid[0:h, 0:w]
    min_dim = max(2, min(h, w))
    for _ in range(max(1, min_dim // 60)):
        cy = int(rng.integers(max(1, int(h * 0.15)), max(2, int(h * 0.85))))
        cx = int(rng.integers(max(1, int(w * 0.15)), max(2, int(w * 0.85))))
        radius_low = max(1, int(min_dim * 0.04))
        radius_high = max(radius_low + 1, int(min_dim * 0.12))
        radius = int(rng.integers(radius_low, radius_high))
        color = float(rng.uniform(0.3, 0.9)) * 255.0
        dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        region = dist < radius
        image[region] = color

    image = np.clip(image, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(image)


def copy_move(
    img: np.ndarray,
    *,
    translate: tuple[int, int],
    rotate_deg: float = 0.0,
    scale: float = 1.0,
) -> ForgeryFixture:
    """Paste a transformed copy of a source region elsewhere in the image.

    Returns ``(image, mask, meta)`` where the mask marks the source region as
    1 and the target region as 2.
    """
    spec = validate_image(img)
    if spec.channels != 3:
        raise ValueError(f"copy_move expects an RGB image (3 channels), got {spec.channels}")
    h, w = img.shape[:2]

    # Source region: central square of roughly 1/9 of the image.
    src_size = min(h, w) // 3
    src_y0 = h // 4
    src_x0 = w // 4
    source = np.ascontiguousarray(
        img[src_y0 : src_y0 + src_size, src_x0 : src_x0 + src_size]
    ).copy()

    # Transform: rotate then scale.
    src_img = Image.fromarray(source)
    if rotate_deg != 0.0:
        src_img = src_img.rotate(rotate_deg, resample=Image.BICUBIC, expand=True)
    if scale != 1.0:
        new_w = max(1, int(src_img.width * scale))
        new_h = max(1, int(src_img.height * scale))
        src_img = src_img.resize((new_w, new_h), resample=Image.BICUBIC)
    target_arr = np.asarray(src_img.convert("RGB"))
    th, tw = target_arr.shape[:2]

    # Place target in the bottom-right corner, clipped to the image bounds.
    tgt_y0 = h - th - src_size // 4
    tgt_x0 = w - tw - src_size // 4
    tgt_y0 = max(0, min(tgt_y0, h - th))
    tgt_x0 = max(0, min(tgt_x0, w - tw))

    out = img.copy()
    out[tgt_y0 : tgt_y0 + th, tgt_x0 : tgt_x0 + tw] = target_arr

    mask = np.zeros((h, w), dtype=np.uint8)
    mask[src_y0 : src_y0 + src_size, src_x0 : src_x0 + src_size] = 1
    mask[tgt_y0 : tgt_y0 + th, tgt_x0 : tgt_x0 + tw] = 2

    meta = {
        "generator": "copy_move",
        "seed": None,
        "source_bbox": [src_y0, src_x0, src_y0 + src_size, src_x0 + src_size],
        "target_bbox": [tgt_y0, tgt_x0, tgt_y0 + th, tgt_x0 + tw],
        "translate": translate,
        "rotate_deg": rotate_deg,
        "scale": scale,
    }
    return ForgeryFixture(image=out, mask=mask, meta=meta)


def splice(
    host: np.ndarray, donor: np.ndarray, region: tuple[int, int, int, int]
) -> ForgeryFixture:
    """Paste a donor patch into a host region.

    ``region`` is ``(y0, x0, y1, x1)`` in host coordinates. The donor is
    resized to fit. Returns ``(image, mask, meta)`` with the manipulated
    region marked as 1.
    """
    validate_image(host)
    donor = np.ascontiguousarray(donor)
    validate_image(donor)
    h, w = host.shape[:2]
    y0, x0, y1, x1 = region
    if not (0 <= y0 < y1 <= h and 0 <= x0 < x1 <= w):
        raise ValueError(f"region {region} is outside the {h}x{w} host image")
    patch_h, patch_w = y1 - y0, x1 - x0
    donor_img = Image.fromarray(donor).resize((patch_w, patch_h), resample=Image.BICUBIC)
    patch = np.asarray(donor_img.convert("RGB"))

    out = host.copy()
    out[y0:y1, x0:x1] = patch

    mask = np.zeros((h, w), dtype=np.uint8)
    mask[y0:y1, x0:x1] = 1

    meta = {
        "generator": "splice",
        "region": [y0, x0, y1, x1],
    }
    return ForgeryFixture(image=out, mask=mask, meta=meta)


def noise_splice(img: np.ndarray, sigma_in: float, sigma_out: float) -> ForgeryFixture:
    """Splice by pasting a noised copy of a central region into the same image.

    A central square region is copied, noised with ``sigma_out`` and pasted
    back displaced. The mask marks the spliced region as 1. This creates a
    local noise-variance inconsistency.
    """
    validate_image(img)
    h, w = img.shape[:2]
    size = min(h, w) // 4
    y0, x0 = h // 4, w // 4
    source = np.ascontiguousarray(img[y0 : y0 + size, x0 : x0 + size]).copy().astype(np.float32)

    rng = np.random.default_rng()
    noised = source + rng.normal(0.0, sigma_out * 255.0, size=source.shape)
    noised = np.clip(noised, 0, 255).astype(np.uint8)

    tgt_y0, tgt_x0 = h - size - size // 4, w - size - size // 4
    out = img.copy()
    out[tgt_y0 : tgt_y0 + size, tgt_x0 : tgt_x0 + size] = noised

    mask = np.zeros((h, w), dtype=np.uint8)
    mask[tgt_y0 : tgt_y0 + size, tgt_x0 : tgt_x0 + size] = 1

    meta = {
        "generator": "noise_splice",
        "sigma_in": sigma_in,
        "sigma_out": sigma_out,
        "splice_region": [tgt_y0, tgt_x0, tgt_y0 + size, tgt_x0 + size],
    }
    return ForgeryFixture(image=out, mask=mask, meta=meta)


def blur_patch(img: np.ndarray, sigma: float) -> ForgeryFixture:
    """Blur a central square patch to simulate local smoothing/removal.

    The mask marks the blurred region as 1.
    """
    validate_image(img)
    h, w = img.shape[:2]
    size = min(h, w) // 5
    y0, x0 = h // 3, w // 3
    patch = np.ascontiguousarray(img[y0 : y0 + size, x0 : x0 + size]).astype(np.float32)
    kernel = _gaussian_kernel(max(3, int(6 * sigma) | 1), sigma)
    blurred = _separable_filter(patch, kernel)
    blurred = np.clip(blurred, 0, 255).astype(np.uint8)

    out = img.copy()
    out[y0 : y0 + size, x0 : x0 + size] = blurred

    mask = np.zeros((h, w), dtype=np.uint8)
    mask[y0 : y0 + size, x0 : x0 + size] = 1

    meta = {
        "generator": "blur_patch",
        "sigma": sigma,
        "blur_region": [y0, x0, y0 + size, x0 + size],
    }
    return ForgeryFixture(image=out, mask=mask, meta=meta)


def jpeg(img: np.ndarray, quality: int) -> tuple[np.ndarray, dict[str, object]]:
    """Re-encode an RGB image as JPEG at ``quality`` and return the decoded array.

    Returns ``(array, meta)``. The array is ``H x W x C`` ``uint8`` RGB.
    """
    validate_image(img)
    if not 1 <= quality <= 100:
        raise ValueError(f"JPEG quality must be 1-100, got {quality}")
    pil = Image.fromarray(img)
    buffer = io.BytesIO()
    pil.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    reloaded = Image.open(buffer).convert("RGB")
    out = np.asarray(reloaded)
    out = np.ascontiguousarray(out)
    meta = {
        "generator": "jpeg",
        "quality": quality,
    }
    return out, meta


def double_jpeg(
    img: np.ndarray, q1: int, q2: int, region: tuple[int, int, int, int] | None = None
) -> tuple[np.ndarray, dict[str, object]]:
    """Double-compress an image: first at ``q1``, then optionally a region at ``q2``.

    Returns ``(array, meta)``. A double-compressed region creates a detectable
    quantization-table inconsistency.
    """
    validate_image(img)
    if not 1 <= q1 <= 100 or not 1 <= q2 <= 100:
        raise ValueError(f"JPEG qualities must be 1-100, got q1={q1}, q2={q2}")

    intermediate, _ = jpeg(img, q1)
    if region is None:
        return intermediate, {
            "generator": "double_jpeg",
            "q1": q1,
            "q2": q2,
            "region": None,
        }

    h, w = img.shape[:2]
    y0, x0, y1, x1 = region
    if not (0 <= y0 < y1 <= h and 0 <= x0 < x1 <= w):
        raise ValueError(f"region {region} is outside the {h}x{w} image")
    patch = Image.fromarray(intermediate[y0:y1, x0:x1])
    buffer = io.BytesIO()
    patch.save(buffer, format="JPEG", quality=q2)
    buffer.seek(0)
    repatched = np.asarray(Image.open(buffer).convert("RGB"))

    out = intermediate.copy()
    out[y0:y1, x0:x1] = repatched
    out = np.ascontiguousarray(out)

    meta = {
        "generator": "double_jpeg",
        "q1": q1,
        "q2": q2,
        "region": [y0, x0, y1, x1],
    }
    return out, meta


def misaligned_jpeg_patch(
    img: np.ndarray, offset: tuple[int, int]
) -> tuple[np.ndarray, dict[str, object]]:
    """Embed a JPEG patch whose 8x8 grid is misaligned by ``offset`` pixels.

    Returns ``(array, meta)``. The host is JPEG-compressed; a sub-region is
    re-encoded at a different quality and pasted with a grid offset, creating
    local 8x8-grid misalignment.
    """
    validate_image(img)
    h, w = img.shape[:2]
    dy, dx = offset
    size = min(h, w) // 6
    y0, x0 = h // 3, w // 3

    host_jpeg, _ = jpeg(img, 85)
    patch = np.ascontiguousarray(host_jpeg[y0 : y0 + size, x0 : x0 + size])
    patch_jpeg, _ = jpeg(patch, 75)

    out = host_jpeg.copy()
    # Paste with an intentional grid offset so the 8x8 phase differs.
    py0 = max(0, y0 + dy)
    px0 = max(0, x0 + dx)
    py1 = min(h, py0 + size)
    px1 = min(w, px0 + size)
    out[py0:py1, px0:px1] = patch_jpeg[: py1 - py0, : px1 - px0]
    out = np.ascontiguousarray(out)

    meta = {
        "generator": "misaligned_jpeg_patch",
        "offset": [dy, dx],
        "patch_region": [y0, x0, y0 + size, x0 + size],
    }
    return out, meta


def with_exif(img: np.ndarray, **tags: str | int) -> tuple[np.ndarray, dict[str, object]]:
    """Attach EXIF tags to a re-encoded JPEG and return the decoded array.

    Returns ``(array, meta)``. EXIF can be inspected by metadata detectors.
    """
    validate_image(img)
    pil = Image.fromarray(img)
    buffer = io.BytesIO()
    exif = pil.getexif()
    for key, value in tags.items():
        try:
            exif[int(key)] = value
        except (TypeError, ValueError):
            exif[key] = value
    pil.save(buffer, format="JPEG", quality=95, exif=exif)
    buffer.seek(0)
    reloaded = Image.open(buffer).convert("RGB")
    out = np.ascontiguousarray(np.asarray(reloaded))
    meta = {
        "generator": "with_exif",
        "tags": {str(k): v for k, v in tags.items()},
    }
    return out, meta
