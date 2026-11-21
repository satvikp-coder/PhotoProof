# ADR-015: Explicit decoded-to-working image coordinates

## Status
Accepted for the remaining PP-032 evidence normalization integration. API 1.5.0.

## Context
Detectors can measure decoded raster pixels while visualization uses an EXIF-oriented
working preview. Shape matching cannot distinguish mirrored images or rotations of a
square image. Resizing a map without its source coordinate transform can mislocalize
evidence. Original evidence bytes must remain unchanged.

## Decision
The host provides `image.geometry` and, in comparison mode, `image.reference_geometry`.
`ImageGeometry` in contracts/images.py documents their JSON fields. Each records schema
version 1, source SHA-256, decoded/working [width,height], original and actually applied
EXIF orientations, normalization transform notes, and a 3x3 integer homogeneous matrix
`decoded_to_working`. Coordinates are zero-based pixel centres [x,y,1]. This is not a
box-edge transform: consumers must transform individual grid sample locations or apply
an explicit cell-footprint convention when projecting block maps and polygons.

All eight EXIF orientations have explicit matrices including width/height translations.
The host derives the applied orientation from the actual WorkingImage normalization
result, not merely untrusted metadata. Disabled or ignored invalid orientation yields
an identity matrix with the source value and normalization note retained. Inconsistent
working dimensions fail visibly instead of guessing a transform.

The controller resolves optional declared media as well as required media, normalizes
once per role, and passes geometry only to declaring plugins. Comparison geometry keeps
target/reference hashes and transforms separate; identical content can share pixels.
Available geometry is recorded in run outputs and the manifest for traceability.
Unavailable geometry is absent with an explicit dependency reason, never an invented
identity transform. Existing missing/failed analysis semantics remain in force.

## Alternatives
Inferring orientation from image dimensions fails for flips and square images. Parsing
EXIF independently in each consumer risks divergent interpretation and duplicates
untrusted-input processing. Treating decoded and working coordinates as interchangeable
produces incorrect visual localization. The host already owns the authoritative decode
and normalization results and can supply a small deterministic geometry record.

## Compatibility
API 1.5.0 adds two run-input names and a typed JSON structure; existing pixel contracts,
context construction and stored manifests remain unchanged. Host API cache identity
invalidates results computed under earlier execution contracts. Consumers must require
API 1.5.0 before relying on the fields. Application release version is unchanged.

## Limits and verification
The matrix describes spatial orientation only, not photometric inversion, image
registration, crop/resize operations or calibration. A detector must still record its
block/stride/origin/validity and coordinate space; that bulk evidence-layer transport is
remaining PP-032 work. This addition alone does not complete PP-032 or PP-033.
Controller tests compare every decoded sample against its mapped working sample for
all eight EXIF orientations using actual isolated JPEG decoding. Unit tests cover
policy-disabled/invalid orientation and inconsistent dimensions; comparison tests keep
roles distinct. No dataset accuracy or authenticity claim is made.
