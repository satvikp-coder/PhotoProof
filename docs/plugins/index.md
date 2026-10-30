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
| `transfer` | `srgb`, `bt709`, `pq`, `hlg` or `linear` |
| `primaries` | `bt709`, `p3` or `bt2020` |
| `transforms` | What was assumed or applied, for example `transfer_assumed` |

Code values are unchanged apart from orientation. A plugin that needs floats converts them itself,
because it knows which conversion its maths needs. `image.working` is always 8-bit: high-depth
sources reach it through a documented, per-channel tone curve, recorded in `transforms`.

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
