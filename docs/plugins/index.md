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
    "inputs": ["image.working"],  # contracts consumed
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

## Decoded and working images

The run resolves the media contracts from stored evidence, once per run, and hands them to
plugins that declare them in `inputs`:

- **`input.original_bytes`** — the stored original bytes, immune to decoding.
- **`image.decoded`** — the pixels as decoded, handed over as a read-only NumPy array.
- **`image.working`** — the normalized working image (sRGB, EXIF orientation applied,
  every transform recorded), also read-only.

A run that cannot decode an image skips the plugins that declared it with
`dependency_unavailable` and records the reason in the manifest; the run is `INCOMPLETE`,
never clean evidence. Plugins read these through `ctx.inputs`. A `ctx.image()` accessor,
`image.working_hdr` and full lazy resolution are planned in #12. Each plugin chooses:

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

Detector algorithms receive NumPy arrays through the media inputs
(`image.decoded`, `image.working`) in `ctx.inputs`. The shared convention is
defined in `photoproof.imaging.arrays`:

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

The `copymove.mask` plugin generates pixel-level source/target masks from
verified copy-move matches using bounded ZNCC correlation.

* Outputs: `copymove.mask` (NPZ artifact with mask, source_mask, target_mask)
  and `copymove.mask_stats` (summary statistics)
* Config: `window_size` (default 11), `threshold` (default 0.5),
  `min_cluster_size` (default 5)

The `visual.sharpness` plugin maps local focus and sharpness using the
variance of the Laplacian, with optional contrast normalization and robust-z
inconsistency detection. It is a weak visual cue for copy-move detection.

* Outputs: `visual.sharpness_map` (per-block log Laplacian variance) and
  `visual.sharpness_anomaly` (robust-z inconsistency map)
* Config: `block` (default 32), `contrast_normalize` (default true)
* Uses OpenCV Laplacian on luminance; documented depth-of-field false positives

The `visual.edges` plugin characterizes object boundaries using Canny edge
detection and Sobel gradient analysis. It estimates edge width (the blur
profile across the edge) and flags boundaries that are abnormally sharp or
soft relative to the image's typical edges.

* Outputs: `visual.edge_width_map` (per-pixel edge width) and
  `visual.edge_segments` (contour segment list with mean width)
* Config: `sigma` (default 1.0), `profile_length` (default 15),
  `min_segment_length` (default 20)
* Complementary to #25: #25 measures blur inside blocks, this measures
  transition profiles across boundaries

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

## Scaffolding new plugins

Use the `new-plugin` CLI command to generate a consistent plugin skeleton:

```
python main.py new-plugin <family>.<name>
```

This creates:

- `src/photoproof/plugins/<family>/<name>/__init__.py` — complete `METADATA`
- `src/photoproof/plugins/<family>/<name>/algorithm.py` — pure NumPy stub (raises `NotImplementedError`)
- `src/photoproof/plugins/<family>/<name>/processor.py` — context → algorithm → outputs wiring
- `src/photoproof/plugins/<family>/<name>/schema.py` — pydantic config model
- `tests/unit/plugins/test_<family>_<name>.py` — metadata validity + algorithm tests (one `xfail`)
- `docs/plugins/<family>-<name>.md` — detector page template

Options:

| Option | Meaning |
|---|---|
| `--priority N` | Plugin scheduling priority (default depends on family) |
| `--required` | Mark the plugin as required at the pipeline level |
| `--no-enabled-default` | Disable the plugin by default |
| `--description TEXT` | Short description for METADATA |

After generating, add the docs page to the `mkdocs.yml` nav, write the algorithm, run ruff and mypy, bump the version and update the changelog.

## Fused region segmentation

`fusion.regions` runs after `fusion.rules` and segments its heuristic map using
8-connected hysteresis, elliptical opening and closing, hole filling, and a
minimum component area. Thresholds are **provisional and uncalibrated** until #37;
a score is not a probability. This step supplies localization, not a verdict.

Configuration in `configs/plugins/fusion.regions.yaml`: `high` (0.70), `low` (0.30),
`kernel_size` (3, odd, 1-21), `min_area_fraction` (0.001),
`polygon_tolerance` (1 pixel), and `max_regions` (50, at most 100).
`high` must be at least 0.5, the minimum score covered by upstream evidence
summaries; `low` must be positive and no greater than `high`.

- `fusion.mask`: immutable content-addressed grayscale PNG artifact, background 0,
  foreground 255. Convert nonzero pixels to a boolean mask for evaluation.
- `fusion.regions`: deterministic list with IDs, simplified polygons, exclusive
  `[x0, y0, x1, y1]` bounding boxes, pixel area, max/mean heuristic score,
  contributing detector references, and a full-image flag. References come from
  upstream `fusion.evidence` entries intersecting the original hysteresis components
  that survive in the cleaned region. This retains a seed's references even when
  opening removes the seed itself. Split regions share the original component's
  references; merged regions union them. These are component-level references, not
  claims that every retained pixel has detector support. Unavailable detectors never
  contribute. Upstream evidence is retained without modification.
  A retained region without contributing detector references is rejected before
  publishing a localization artifact; malformed upstream evidence is never repaired.
- `fusion.segmentation`: status (`unavailable`, `no_regions`, or `segmented`),
  grid shape, region count, truncation and full-image flags. A missing map yields
  no mask; a valid all-zero map yields a zero PNG. Neither implies authenticity.

Coordinates and PNG dimensions refer to the **fused map grid**, which can be
smaller than the original image. Consumers must scale explicitly for display.
The region list is capped by descending pixel area (scan-order ties); the PNG
retains all area-filtered components. A full-image region may reflect global
processing and must be interpreted with the upstream evidence and assessment.
Invalid/nonfinite maps fail visibly rather than becoming empty evidence.

## Conservative evidence assessment

`fusion.assessment` version 2.0.0 consumes actual normalized coverage and accepted fusion
outputs. It applies configured thresholds, excludes weak/invalid maps from sufficient
coverage and yields `inconclusive` for unavailable evidence. The fixed categories remain
separate from pipeline RunStatus. See [ADR-005](../planning/adr-005-evidence-sufficiency.md)
for coverage states, precedence, provisional limitations, historical migration and the
remaining measured-comparison integration. No accuracy or calibrated-confidence claim is made.

## Rule-based evidence fusion

`fusion.rules` consumes available normalized maps and valid masks. Its version 2.0.0
rules apply configured weights, gate weak cues on accepted support and combine each
family once. `fusion.score_info` explicitly labels the map `heuristic_score` and
`uncalibrated`. Missing detector entries retain their unavailable reason with a null
score. Region summaries include effective detector references, raw maximum detector
scores and weighted contributions. See [ADR-004](../planning/adr-004-rule-based-fusion.md)
for the complete formula, provisional defaults, limitations and migration from 1.0.0.

## Normalized evidence availability

`fusion.normalize` version 2.0.0 produces layers only from usable numeric scalar or
nonempty two-dimensional array inputs (including serialized numeric grids). Null,
unsupported structured values, unread artifact references and empty outputs are
listed in `fusion.unavailable`; they never become zero-score layers. Artifact references
are not pixel data: downstream availability remains incomplete until the detector
integration supplies a readable numerical contract. No filesystem capability is added.
Nonfinite scores/maps fail visibly. Original detector outputs remain unchanged.

Per-detector `method`, `z0`, positive `k` and `strength_class` are configured in
`configs/plugins/fusion.normalize.yaml`, with a rationale beside every default.
Unknown detector names and invalid parameters fail validation. `robust_z` maps via
median/MAD and logistic squashing. Constant maps have no relative anomaly and yield
zero; this does not establish a clean image. `identity` preserves already normalized
unit-interval maps; verified copy-move masks use this method so sparse binary masks
are not erased by zero MAD. Unverified keypoints, match counts and statistics are
weak context; they are not verified localization. All outputs explicitly retain
`calibration: uncalibrated`. These defaults are provisional until #37.

Migration: version 1.0.0 could emit invalid zero layers for unsupported outputs and
ignored mapping configuration. Historical manifests remain unchanged; re-analyze to
obtain corrected evidence. Cache fingerprints include the changed plugin version and
implementation. Consumers must use the unavailable list rather than substitute zeros.

Raw noise residuals, absolute noise levels, feature counts and summaries are weak
context, not measured spatial inconsistency. Configuration may lower a detector's
published strength class but cannot promote it (for example, ELA cannot become strong
through YAML). Any scientific reclassification requires a reviewed, versioned rule change.

## Safe preview support

`support.preview` (priority 5) consumes `image.working` and produces the
`support.preview` PNG artifact plus `support.preview_info` (dimensions, derived
label, metadata-removal policy and retained alpha). Missing working pixels skip
the plugin visibly; no preview substitutes for evidence. Configure `max_edge` in
`configs/plugins/support.preview.yaml`. The reusable `imaging.preview.make_preview`
API accepts a normalized `WorkingImage` and an explicit artifact writer.

## Reference comparison media

Plugin API 1.3.0 adds read-only `image.reference_decoded` and
`image.reference_working`, resolved from verified stored reference evidence once per
unique content hash. Existing target contracts keep their meaning. A plugin must declare
these inputs to access them; missing reference media is unavailable, never zero pixels.
The host persists relationship checks in `comparison.preflight`. See
[ADR-013](../planning/adr-013-reference-comparison-preflight.md) for shared budgets,
coordinate conventions, identical-input handling and trust limitations.

## Reading a declared upstream artifact

Plugin API 1.4.0 adds `ctx.artifact("noise.residual", max_bytes=32 << 20)`, returning
verified immutable bytes only for a resolved declared input. The host checks producer
identity, current-run writer records, declared/actual size and SHA-256. It rejects
undeclared, forged, cross-run, missing or corrupt references. No runtime path is supplied.
The effective ceiling is bounded by `resources.max_artifact_read_bytes` independently of
raw-image ingestion limits. Legacy contexts without this capability fail visibly.

A verified artifact still requires its documented parser. Numerical consumers must disable
pickle and check header shapes, dtypes and allocation limits; a matching hash does not make
an arbitrary format safe. This API is for trusted plugins and does not implement process
isolation. See [ADR-014](../planning/adr-014-verified-artifact-inputs.md) for authorization,
cache integration, migration and remaining executor work.

Structured artifact collections use an explicit logical selector, such as
`ctx.artifact("fusion.layers", selector=(0, "map"))`. Only references already included
at that location in the declared producer output are authorized. Selectors contain at
most eight JSON keys/nonnegative indices and never resolve a filesystem path.

## Bounded numerical artifact parsing

`photoproof.contracts.arrays.decode_npy` parses verified NPY bytes without pickle
or filesystem access. It accepts plain bool/integer/float arrays in NPY versions
1 and 2, validates dimensions, element counts, dtype, header size and exact payload
length before constructing a read-only view. Its default ceiling is 64 MiB and
16,777,216 elements; callers may impose smaller limits. Invalid, object, structured
or complex arrays fail visibly. Hash verification remains the byte broker's job;
this parser does not establish forensic meaning or calibration.

`noise.variance_map` version 1.1.1 requires API 1.5.0 and consumes the actual
`noise.residual` NPY artifact using this broker/parser. Its output names and
validity-aware summary remain unchanged. The parser does not make unsupported
normalization inputs available or resolve the large JSON-map transport limit.

## Image coordinate contract

API 1.5.0 provides declared `image.geometry` and `image.reference_geometry` JSON
records, also retained in available run outputs/manifests. Sizes use [width,height];
the `decoded_to_working` 3x3 matrix maps zero-based decoded pixel centres [x,y,1]
to normalized working pixel centres. It describes the orientation actually applied,
including flips, rather than guessing from shape. Records carry source hashes and
normalization provenance. See [ADR-015](../planning/adr-015-image-coordinate-contract.md).

This does not register two images or define detector block/cell footprints. Consumers
must retain their sampling grid and valid mask and explicitly project into the working
space. Optional declared media is resolved when available; missing geometry remains
unavailable and is never replaced by an assumed identity matrix.

## Numerical encoding and spatial grid projection

`encode_npy` pairs with `decode_npy`: it serializes plain numerical arrays with
pickle disabled and enforces element/byte ceilings, including the NPY header,
before the artifact writer is called. The noise variance producer uses this
encoder. Encoding bounds do not constrain the upstream algorithm's total RSS.

`SamplingGrid` and `project_scores` preserve explicit measured cell footprints,
partial border centres and boolean validity during decoded-to-working projection.
Unsupported points receive an invalid mask, never zero-score evidence. The pure
projection API is available; production normalization/fusion wiring remains in
progress. See [ADR-016](../planning/adr-016-spatial-evidence-grids.md).

Binary evidence-layer transport is prototyped in
[ADR-017](../planning/adr-017-binary-evidence-layer-wire.md). Its score and validity
artifacts keep explicit source and sampling provenance. Controller transport probes
work; production normalization/fusion migration remains in progress.
