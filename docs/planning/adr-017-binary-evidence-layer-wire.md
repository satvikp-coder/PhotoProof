# ADR-017: Binary evidence-layer wire format

## Status
Prototype under active PP-032 implementation. Controller transport probes pass;
production normalization/fusion/assessment/visualization migration remains pending.

## Decision
Version-2 evidence-layer records retain detector output ID, source SHA-256,
coordinate space (decoded_pixels or global), explicit SamplingGrid metadata,
regions, strength class, calibration and notes. Score and boolean-validity arrays
are separate canonical NPY artifacts, never bulk JSON grids. Source metadata must
match the consuming image geometry. Native sampling describes decoded pixel units;
regions use exclusive [y0,x0,y1,x1] decoded-pixel boxes. Global context has exactly
one score, no grid or spatial regions and weak strength; it is not localization.

`write_evidence_layer` validates actual finite unit-interval data and measured
validity before encoding. Missing maps/all-invalid masks require an unavailable
record, not invented zeros. The writer uses fixed ordinal artifact names, bounded
metadata, pickle-free NPY and explicit uncalibrated semantics. Calibrated claims
are unsupported until verified calibration data and a versioned contract exist.

`read_evidence_layer` reads only named declared collection members through the host
artifact broker's exact key/index selectors. It validates version, expected source,
field types, float32 scores, boolean validity and explicit sampling. Immutable NPY
views retain verified bytes. Corrupt or wrong-source records fail visibly; no path,
arbitrary digest lookup, regeneration or evidence repair is provided.

## Verification and limits
A real controller producer-consumer probe transports a score artifact larger than
1 MiB while its manifest metadata stays below 4 KiB. Stored artifact hashes reach
the manifest, input bytes stay unchanged and returned arrays are read-only. Other
controller probes reject changed source/calibration metadata and corrupted blobs.
Unit checks reject absent/nonfinite/out-of-range maps, invalid masks/regions/notes,
unsupported calibration claims and global-to-spatial strength promotion.

These are transport tests using synthetic pixel probes, not forensic detectors or
accuracy benchmarks. They do not complete PP-032/PP-033. Application producers and
consumers must migrate together with plugin version/migration notes before this
wire format is enabled in production. Pipeline applicability and block/stride
metadata must remain honest; contextual statistics are not localized measurements.
