# Plugin development

A plugin is one forensic (or support) step: it declares what it needs and produces,
and the framework decides whether and when it runs. **Plugin API version: 1.0.0.**

!!! note
    No production plugin exists yet. Working examples live in
    `tests/fixtures/mockplugins/demo/`.

## Directory structure

```text
src/photoproof/plugins/
└── compression/            category (no METADATA)
    └── ela/                plugin package
        ├── __init__.py     METADATA = {...}
        ├── processor.py    def run(ctx) -> dict
        └── schema.py       optional pydantic config model
```

Discovery walks `photoproof.plugins` recursively in sorted package-name order. A
package with a `METADATA` attribute is a plugin; one without is a category namespace.
Discovery imports `__init__.py` only. `processor.py` is imported lazily when the
plugin first runs.

## Metadata

```python
METADATA = {
    "id": "compression.ela",  # unique, dotted lowercase
    "name": "Error Level Analysis",
    "description": "JPEG recompression residual map.",
    "version": "1.0.0",  # plugin SemVer, independent of the app
    "api_version": "1.0.0",  # plugin API it was written for
    "priority": 10,  # lower runs earlier; >= 0, no maximum
    "required": False,  # pipeline-level importance (see failure policy)
    "enabled_default": True,
    "requires": ["ingest.decode"],  # must SUCCEED first; outputs usable as inputs
    "after": ["metadata.exif"],  # ordering only
    "entry_point": "processor:run",  # module-in-package:function
    "inputs": ["image.working_rgb"],  # contracts consumed
    "outputs": ["compression.ela_map"],  # contracts produced (exactly these keys)
    "timeout_seconds": 30,  # default: scheduling.default_plugin_timeout_seconds
    "supported_formats": ["jpeg"],  # empty = format-independent
    "deterministic": True,
    "config_schema": "schema:Config",  # optional; validates configs/plugins/<id>.yaml
    "python_dependencies": ["numpy"],  # versions recorded in manifests and cache keys
}
```

Validation rejects (and reports the plugin as *unavailable*): unknown keys, bad
SemVer, negative priority, self-dependency, an ID in both `requires` and `after`,
empty `outputs`, outputs named `input.*`, malformed entry points, an incompatible API
version, or a missing entry module. Duplicate IDs across packages make the whole
registry invalid (`RegistryError`).

## Decoded and working images (planned)

`imaging/normalization.py` already turns a decoded image into a working image: sRGB, with EXIF
orientation applied, and a log of every transform. Plugins cannot reach any of these yet. #12
will expose them, and each plugin will choose:

- **`image.working`** for detectors that compare pixels across regions or images.
- **`image.decoded`** for compression and noise detectors. Colour conversion changes pixel
  statistics, so ELA, DCT and noise-residual analysis should use the unconverted pixels.
- **`image.working_hdr`** for detectors that can use more than 8 bits. It exists only when the
  source has more than 8 bits per channel.

| Field of `image.working_hdr` | Meaning |
|---|---|
| `dtype` | Always `uint16`, stored little-endian |
| `mode` | `I;16`, `RGB;16` or `RGBA;16` |
| `bit_depth` | Bits actually used, for example 10, 12 or 16. Values are not rescaled |
| `transfer` | `srgb`, `bt709`, `pq`, `hlg` or `linear`, or `icc` if an ICC profile defines it |
| `primaries` | `bt709`, `p3` or `bt2020`, or `icc` if an ICC profile defines them |
| `icc_profile` | The profile bytes when `transfer` and `primaries` are `icc`, otherwise empty |
| `transforms` | What was assumed or applied, for example `transfer_assumed` |

Code values are unchanged apart from orientation. A plugin that needs floats converts them itself,
because it knows which conversion its maths needs. `image.working` is always 8-bit: high-depth
sources reach it through a documented, per-channel tone curve, recorded in `transforms`.

## Array conventions

Detector algorithms receive NumPy arrays through `PluginContext.image`. The shared
convention is defined in `photoproof.imaging.arrays`:

* Working arrays: `H x W x C`, C-contiguous, RGB channel order, `uint8` for 8-bit
  or `uint16` for high bit depth; grayscale as `H x W`.
* Algorithms convert to `float32` internally via `to_float32(arr)`, which scales
  `uint8` to `[0, 1]` by dividing by 255 and `uint16` by dividing by 65535.
* Arrays handed to plugins are read-only (`arr.flags.writeable = False`); mutation
  raises `ValueError`. Use `as_read_only(arr)` or `ensure_read_only(arr)` to enforce
  this before handing arrays to plugins.
* `validate_image(arr)` checks shape, dtype, contiguity, NaN values and zero size,
  returning an `ImageSpec` or raising `InputRejectedError`.
* `to_luma(arr)` converts RGB to single-channel BT.601 luma as `H x W` `float32`.

Synthetic forgery generators with exact ground-truth masks live in
`tests/fixtures/synthetic.py` and are exposed to tests through the `synthetic`
pytest fixture. Generators include `textured_scene`, `copy_move`, `splice`,
`noise_splice`, `blur_patch`, `jpeg`, `double_jpeg`, `misaligned_jpeg_patch`
and `with_exif`.

## Block statistics

Regional detectors share a common block-tiling and robust-statistics library in
`photoproof.imaging.stats`:

* `tile(array, block, stride, border)` splits a 2-D array into blocks with
  per-block coordinates.
* `block_reduce(array, block, stride, func)` applies a reduction (mean, median,
  MAD, etc.) to each block.
* `robust_zscore(values, mask)` computes robust z-scores using the median/MAD
  estimator; returns `degenerate=True` when MAD = 0 instead of dividing by zero.
* `AnomalyMap` bundles per-block values, block/stride geometry, a validity mask
  and notes for serialization and visualization.
* `upsample(anomaly_map, image_shape, mode)` lifts block-level maps to full
  image resolution for display.

## Metadata forensics

The `metadata.exif` plugin extracts EXIF and XMP metadata and reports provenance
inconsistencies as typed findings. Metadata is trivially editable and often
stripped, so findings are reported as context, never as proof. GPS coordinates
are never emitted. Findings include:

* `metadata_editor_detected` — editing software tags found (info)
* `metadata_impossible_date` — EXIF date outside plausible range (low)
* `metadata_dimension_mismatch` — EXIF dimensions disagree with actual (medium)
* `metadata_thumbnail_mismatch` — embedded thumbnail differs from downscaled main (medium)
* `metadata_absent` — no metadata container found (info, not suspicious)
* `metadata_oversized_exif` — EXIF block exceeds size limit (low)
* `metadata_ifd_loop` — EXIF IFD nesting exceeds limit (low)

Each finding carries a severity (`info`/`low`/`medium`) and a false-positive note.

## Compression forensics

The `compression.ela` plugin implements Error Level Analysis as a supporting signal.
It recompresses the image at a controlled JPEG quality and computes the absolute
pixel difference. Regions with different compression histories show different error
levels.

* Config: `quality` (1-100, default 95), `aggregate` (max|mean), `block` (default 16),
  `edge_suppression` (bool, default true)
* Outputs: `compression.ela_map` (8-bit PNG artifact), `compression.ela_anomaly`
  (AnomalyMap of block-level robust z-scores)
* Applicable to JPEG primary; non-JPEG inputs run with `context: no_jpeg_history`
* Explicitly labelled supporting evidence only - never a standalone forgery verdict

The `compression.jpeg_dct` plugin reads JPEG quantization tables from DQT markers
and performs 8x8 DCT on decoded blocks to detect double-compression patterns via
histogram periodicity analysis.

* Outputs: `compression.jpeg_tables` (tables, estimated quality, standard_table flag)
  and `compression.dq_map` (AnomalyMap of per-block double-compression likelihood)
* Applicable to JPEG only; `NOT_APPLICABLE` for other formats
* Pure-Python JPEG bitstream parsing - no native decoder bindings required

The `compression.jpeg_grid` plugin estimates the dominant 8x8 blocking-grid origin
of the image and maps regions whose blocking artifacts align to a different grid,
which is characteristic of content pasted from another JPEG or of cropping.

* Outputs: `compression.grid_origin` (`{dx, dy, confidence}`) and
  `compression.grid_map` (AnomalyMap: higher = locally aligned to a different phase)
* Config: `window` (default 64), `stride` (default 32)
* Uses luminance channel; low-confidence results for high-quality or non-JPEG inputs

## Noise Forensics

The `noise.residual` plugin extracts high-frequency noise residuals via wavelet-based
denoising (BayesShrink or Wiener thresholding). The residual captures sensor noise
and post-processing fingerprints that spliced regions often lack.

* Outputs: `noise.residual` (float32 NPZ artifact) and `noise.sigma_global`
  (global noise sigma estimate via MAD)
* Config: `wavelet` (default `db8`), `levels` (default 4), `method`
  (`bayesshrink`|`wiener`), `channel` (`luma`|`rgb`)
* Uses pure-Python JPEG bitstream parsing via PyWavelets

The `noise.variance_map` plugin estimates local noise variance per block from the
noise residual and builds a noise-consistency map highlighting regions whose noise
level is inconsistent with the rest of the image.

* Outputs: `noise.sigma_map` (per-block sigma map) and
  `noise.consistency_map` (robust z-score map)
* Config: `block` (default 32), `intensity_bins` (default 8),
  `texture_threshold` (default 15.0), `saturation_threshold` (default 0.98),
  `min_valid_fraction` (default 0.30)
* Provides coverage fraction and confidence level (high/medium/low)

## Copy-Move Forgery

The `copymove.sift_keypoints` plugin detects SIFT keypoints and computes descriptors
on the decoded image. This is the first stage of the copy-move detection chain:
keypoints → matches → clusters → verified transforms → masks.

* Outputs: `copymove.keypoints` (NPZ artifact with keypoints and descriptors) and
  `copymove.keypoint_stats` (summary statistics)
* Config: `max_keypoints` (default 20000), `contrast_threshold` (default 0.04),
  `edge_threshold` (default 10.0), `rootsift` (default true),
  `max_edge` (default 3000)
* Uses OpenCV's SIFT implementation (available since OpenCV 4.4)

The `copymove.matching` plugin matches SIFT descriptors using KNN and generalized
Lowe ratio filtering (g2NN). It performs dependency-aware matching with spatial
constraints and symmetric deduplication.

* Outputs: `copymove.matches` (NPZ artifact with index pairs and distances) and
  `copymove.match_stats` (summary statistics)
* Config: `k` (default 10), `ratio` (default 0.5), `min_spatial_distance`
  (default 10), `max_pairs` (default 50000)
* Uses OpenCV FLANN or brute-force matching depending on descriptor count

The `copymove.clustering` plugin groups matched keypoint pairs into clusters using
hierarchical clustering (Ward linkage) or DBSCAN on displacement vectors.

* Outputs: `copymove.clusters` (cluster list with source/target bounding boxes,
  displacement, size) and `copymove.match_stats` (summary statistics)
* Config: `method` (default `hierarchical`), `cut_threshold` (default 0.5),
  `eps` (default 0.5), `min_matches_per_cluster` (default 4)

## Supported formats

A plugin with a non-empty `supported_formats` is `NOT_APPLICABLE`, without being called, if an
image it declares in `inputs` has any other format. It is checked against `input.target` if it
declares neither image. An input whose format could not be identified counts as unsupported.
Leave the list empty for a format-independent plugin.

## Contracts

Contract names are dotted identifiers. Run inputs are reserved:

| Contract | Value |
|---|---|
| `input.target` | `InputDescriptor` as JSON: `role`, `sha256`, `size`, `media_format` |
| `input.reference` | Same, only in comparison mode. A plugin that declares it is `NOT_APPLICABLE` without a reference. |

Every other input must be an output of a plugin in `requires`. Each output contract
has exactly one producer.

!!! info "Decoded pixels are not yet available"
    Plugins currently receive input *descriptors*, not pixels. Exposing decoded
    working images through the context is a backlog item that precedes the first
    detector.

## The execution context

```python
from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    ctx.inputs  # only the declared input contracts
    ctx.config  # validated config model instance, or None
    ctx.seed  # scheduling.random_seed — use it for any randomness
    ctx.logger  # logger named photoproof.plugins.<id>
    ref = ctx.write_artifact("map.png", png_bytes, "image/png")  # -> ArtifactRef
    return {"compression.ela_map": ref}
```

Plugins get no filesystem paths. Binary output goes through `write_artifact`, which
stores it content-addressed and read-only under `ARE/artifacts/`; artifact names must
be unique within one plugin run. Return values must match `outputs` exactly, be
JSON-serializable or `ArtifactRef`s, and total at most 1 MiB. `ctx.inputs` is a private
deep copy.

To decline an input, raise `photoproof.contracts.exceptions.NotApplicableError`.
Raise any other exception to fail; the framework records it. Do not catch and hide
errors.

## Priorities and dependencies

See [Execution lifecycle](../architecture/lifecycle.md) for the ordering algorithm,
dependency semantics, the state machine and failure policies. In short:

- choose a priority level by convention (0, 10, 20, 30, 90, 100);
- a dependency must not have a later priority than its dependent;
- use `requires` when you consume an output and `after` for pure ordering.

## Testing a plugin

- Unit-test the algorithm on small synthetic arrays.
- Add a fixture under `tests/fixtures/` only if you are licensed to redistribute it.
- Integration-test through `AnalysisController` with the `make_app` fixture, as in
  `tests/integration/test_orchestration.py`.
- Cover the failure paths: malformed input, `NotApplicableError`, timeouts.

## Compatibility and versioning

- Bump the plugin's `version` when its outputs or behaviour change (PATCH for fixes,
  MINOR for compatible additions, MAJOR for changed output semantics).
- The implementation digest (SHA-256 over every file in the plugin package, including
  subpackages and data files, excluding bytecode) is recorded in manifests and cache
  keys, so any change to what the plugin ships invalidates cached results.
- A plugin is compatible with host API `X.Z.*` if it was built for `X.Y.*` with
  `Y <= Z`. During `0.x` the minor version must match exactly.
