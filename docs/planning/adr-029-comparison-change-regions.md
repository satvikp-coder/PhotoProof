# ADR-029: Discrete measured comparison differences

## Status

Controller integration for issue30 on an unmerged branch stacked on algorithmPR176
and SSIM integrationPR175. Actual generated images pass verified ingestion, isolated
decoding, registration, SSIM, change extraction and a downstream artifact consumer.
Independent review and main integration remain required. Reports/UI and issue31
interpretation are not implemented by this stage.

## Decision

Consume the actual full-resolution dissimilarity D and its explicit validity mask.
Reject nonfinite/out-of-range scores, mismatched dimensions, nonboolean support and
nonzero invalid storage fill. No measured cells raises NotApplicableError; it never
becomes a no-differences result. Admit at most4,194,304pixels before numerical copies.

Default threshold selection is Otsu over measured scores quantized to nearest uint8.
Convert the selected threshold back by dividing by255, then take the maximum with
the configured minimum0.15. Apply a strict greater-than comparison to the original
float scores. This avoids flagging zero scores when an explicit threshold0 is used.
A configured fixed float in[0,1] overrides automatic selection and its floor; this is
an explicit sensitivity choice, recorded with the actual threshold and method.

Perform elliptical opening then closing with an odd kernel (default3, hard maximum9).
Clip to validity after each operation so cleanup cannot fill unknown holes. Cleanup
can remove thin differences or add supported neighboring pixels; recorded region
means include all final retained pixels, not only threshold seeds. It is a provisional
engineering sensitivity, not a calibrated manipulation detector.

Use8-connected OpenCV SAUF labels, then reject components below the configured
`min_region_area` fraction of the entire target frame. The default0.0005 is0.05percent;
round upward to at least one pixel. It is a fraction, not an ambiguous pixel-count
parameter. Derive final raster with a label lookup and region sums in one pass rather
than rescanning the image for each component.

Each region has the actual component label id, bbox[x,y,width,height] in target
working coordinates, pixel area, target-area fraction, mean D and category`difference`.
Order by descending area then top/left. Default table cap256, hard1000; a truncated
table explicitly records total and reported counts while the raster retains every
accepted component. No region is labelled as manipulation. A retained area covering
at least80percent of measured support is flagged low specificity; this is a documented
descriptive rule, not an accuracy claim.

No retained pixels yields `no_differences_detected` at the configured sensitivity
and measured support. It does not clear unmeasured regions or establish authenticity.
Record initial threshold count, removed small-component count, actual thresholds,
kernel, measured/target/changed counts and truncation. Recompression quality,
registration error and chosen thresholds can change the result.

## Validation and limits

API1.12 adds typed DifferenceRecord/DifferenceRegion, encode_change_mask and
read_changes. The summary carries both source hashes, target working size and a
digest of all four exact upstream similarity artifacts. Its finite, bounded facts
record actual threshold/floor/quantization, cleanup, filtering, counts and limitations.
Companion references contain the PNG mask and int32 component-label NPY. Positive
labels preserve their actual component ids, including gaps after area filtering;
unselected/background pixels remain zero and do not imply unknown support was tested.

The reader validates schema, sources, frames, ownership and byte/element bounds before
any numerical read. It reads actual upstream similarity/registration support through
declared broker selectors, rejects labels outside measured coverage and recomputes
counts, area fractions, means and true bounding boxes. Extrema are aggregated once
in bounded row chunks, avoiding a full-image rescan per reported component. Counts,
status, truncation, low specificity and sensitivity must agree with actual pixels.

The PNG encoder handles only bounded derived boolean pixels. It uses a fixed minimal
grayscale8-bit noninterlaced IHDR/one-IDAT/IEND format with zero row filters and no
metadata. No native image-byte decoder is invoked. The reader checks exact chunks,
CRC, dimensions/mode, bounded deflate output and every decoded pixel against the
label raster. Trailing/oversized/compressed inconsistencies are rejected. Other valid
PNG encodings are outside this specific versioned derived-mask wire schema. An
independent Pillow test confirms ordinary PNG viewers can read the generated mask.

`comparison.change_mask` runs at priority30 and explicitly requires registration and
SSIM. The production comparison profile requires this plugin and its mask/region
outputs. Unsupported registration therefore cannot silently produce an empty clean
comparison: missing required outputs abort that profile. Constant-image CLI fixtures
assert this outcome while preserving both ingested sources. Mock-only framework
probes own explicit fixture profiles rather than weakening production requirements.

Future main integration must reconcile pending API capabilities and keep the highest
host version. This branch is stacked for review and must never merge into its feature
base; retarget main only after parent integration and green main, then rerun all
required checks and obtain legitimate independent review. No bypass is authorized.

Synthetic controls pass real SSIM output into extraction and record planted-edit IoU.
Separate tests recompress unchanged synthetic luminance images to actual JPEG at
qualities75/85/95 before SSIM; default sensitivity returns no regions on those
fixtures. Other controls cover unknown support, actual Otsu separation/floor, explicit
sensitivity, cleanup clipping, small components, raster-preserving metadata truncation,
zero threshold, malformed inputs and configuration ceilings. This is not a general
JPEG false-positive evaluation or a forensic dataset benchmark.

Numerical admission bounds allocations but is not OS RSS/process isolation. No
original evidence mutation, filesystem operations, image-byte decoder, new dependency,
model or dataset is introduced in this algorithm. Test-only JPEG decoding uses
generated bytes; production decoding remains the existing isolated ingestion path.
