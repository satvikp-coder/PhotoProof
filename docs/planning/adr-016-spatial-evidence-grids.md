# ADR-016: Measured-cell projection for spatial evidence

## Status
Accepted projection algorithm under active PP-032 implementation. Bulk wire-format,
producer/consumer migration and full controller integration remain incomplete.

## Context
A numerical map's dimensions alone do not identify where it measured the image.
Detectors use different block sizes and strides, sometimes overlapping windows or
partial border cells. Decoded and working rasters may also differ by EXIF orientation.
Interpolating scores or stretching unsupported borders can invent spatial evidence.

## Decision
`SamplingGrid` describes decoded-image size, first covered pixel origin, stride and
block footprint as x/y pairs. `project_scores` takes finite unit-interval scores, a
matching boolean validity mask, this grid, the host image geometry and an explicit
working-grid output shape. It returns scores AND measured-support validity. Invalid
fill zeros are never evidence and must always travel with that mask.

Output sample centres cover the working raster uniformly. The exact inverse signed
permutation from the EXIF geometry maps each working coordinate into decoded space.
The nearest measured cell centre supplies the score, with deterministic ties to the
higher index. Centres of partial border cells use their clipped real footprint.
A sample is valid only inside that selected cell's footprint and when the cell is
valid; a more distant valid cell is never substituted. Sampling gaps remain invalid.
Overlapping windows use nearest centre, never score-max selection or interpolated
scores. Scalar global context must be represented separately, never as spatial cells.

The transform must map source pixel corners exactly onto working bounds. Unknown
geometry versions, malformed/sheared/overflowing transforms, invalid sampling, cells
outside the source image, invalid scores/masks and excessive output counts are rejected.
The implementation uses separable one-dimensional coordinate axes for EXIF transforms,
avoiding a full floating coordinate mesh. It bounds output elements, not total process
RSS or concurrent plugin memory. Native array bytes still require broker and NPY limits.

## Compatibility and pending integration
This pure helper currently builds on unmerged ADR015/API1.5 geometry. It is not yet
used by production normalization/fusion. The pending evidence-layer format must retain
source hash, sampling grid, calibration, strength, map/valid-mask artifacts and their
producer provenance; downstream projection must preserve these masks through fusion,
assessment, segmentation and visualization. Changes to existing map wire formats need
versioned plugin migrations and coordinated consumer tests before merge. Large maps
must use binary artifacts rather than exceed the manifest JSON ceiling.

## Alternatives
Stretching every map over the full image ignores unmeasured borders and offsets.
Interpolated or maximum-neighbour scores create measurements that no detector made.
Discarding spatial metadata or treating global feature statistics as localization is
not an acceptable shortcut. Regenerating all upstream algorithms loses direct evidence
consumption and provenance.

## Verification
Twenty-one deterministic tests cover all eight EXIF transforms, partial cells, sparse
coverage, invalid masks, sampling failures, output ceilings and malformed transforms.
These prove geometric behavior, not forensic accuracy or calibration.
