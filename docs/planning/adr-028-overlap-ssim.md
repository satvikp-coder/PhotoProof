# ADR-028: Measured-window image similarity

## Status

Pure algorithm implemented on an unmerged feature branch for issue29. It has no
METADATA and cannot run as a detector yet. Controller activation, source-bound
artifact transport and the full comparison chain remain separate integration work.
The branch builds on PR173's registration contract; no main integration is claimed.

## Decision

Use existing OpenCV and NumPy. Compare uint8 L/RGB/RGBA aligned working images in
OpenCV's uint8 luminance representation, converted to float32 unit range. This
choice does not measure chromatic changes with equal luminance; report that limitation.
Neither input is a trusted original. Registration and differences do not prove forgery.

Compute local population means, variances and covariance with an11x11 Gaussian
window, sigma1.5 by default (configurable0.5 to5), C1=0.0001 and C2=0.0009.
Clip negative roundoff variances to zero and covariance to its Cauchy bound.
The canonical full-resolution dissimilarity is (1-SSIM)/2. The independent absolute
difference is raw luminance difference before any normalization. Return read-only
float32 arrays and an explicit boolean valid mask. SSIM is descriptive image
similarity, never a calibrated probability or evidence of authenticity.

Require every donor in an11x11 window to be measured. Erode overlap by that window
with outside-frame support false; exclude nonopaque pixels in either input. Unknown
cells have zero storage fill and validity false. No supported window raises the
existing NotApplicableError, rather than returning a clean map. Overlap below20percent
is labelled low confidence. Also report valid fraction separately from raw overlap.

Optional gain/offset fits reference luminance to target luminance using least squares
at at most65,536 deterministic overlap positions. Constant reference uses gain1 and
the measured mean offset. Gains outside0.1 to10 are rejected with an explicit reason;
the unnormalized comparison remains available. Histogram normalization uses256-bin
empirical CDF quantile matching. Record actual gain/offset or the full bounded LUT,
sample count and clipping count. Raw absolute differences remain available to reveal
changes hidden by normalization. Fitting cannot establish an exposure-only cause and
can conceal broad edits; interpretation must retain this limitation.

Residual geometric error can produce thin edge differences. Default radius1 binary
erosion of Canny edge-response support produces a separate suppression mask. Preserve
canonical dissimilarity and validity unchanged: a consumer may offer a separately
labelled suppressed view but must not silently turn removed differences into clean
evidence. Radius0 disables suppression. The mask records every suppressed pixel;
thin genuine edits may also resemble misregistration. This separation is chosen over
destructive erosion of the only numerical map.

`multiscale=true` additionally computes a five-scale global image-quality summary.
The local map remains single-scale, preserving issue29's definition of D. At each
level use an explicit2x2 box low-pass then downsample; discard odd trailing samples
and require all four donors. Pool mean contrast-structure at levels1 to4 and mean
SSIM at level5 with weights0.0448,0.2856,0.3001,0.2363,0.1333. Negative pooled
components are explicitly clamped to zero before fractional powers. Insufficient
fully supported windows at any scale makes only this optional summary unavailable;
never silently change the number of scales. This documented implementation is not
claimed to match every external MS-SSIM library's boundary/filter conventions.

## Bounds and validation

Reject invalid dtype/channel/geometry/mask and frames over4,194,304pixels before
float copies or filtering. Configuration cannot raise the hard pixel or erosion
ceiling. This bounds numerical allocations and work, not OS RSS or plugin isolation.
No image bytes are decoded here; no files, datasets, models or new dependencies.

Synthetic tests compare OpenCV results with independent float64 NumPy direct Gaussian
windows within1e-4, including multiple sigma values. Other controls cover constant
and textured identical images, symmetry, planted edits, gain/offset and nonlinear
histogram corrections, raw-difference preservation, actual multiscale availability,
mask holes/alpha/borders, resource admission and recorded edge suppression. These are
numerical controls, not a forensic accuracy evaluation or calibrated threshold study.

## Sources

- [Wang et al., Image Quality Assessment (2004)](https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf)
- [Wang, Simoncelli and Bovik, Multi-scale SSIM (2003)](https://www.ece.uwaterloo.ca/~z70wang/publications/msssim.pdf)
