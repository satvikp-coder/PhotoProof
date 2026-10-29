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
    "supported_formats": ["jpeg"],  # empty = format-independent (enforcement planned)
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
stores it content-addressed and read-only under `ARE/artifacts/`. Return values must
match `outputs` exactly and be JSON-serializable or `ArtifactRef`s.

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
- The implementation digest (SHA-256 of the package's `*.py` files) is recorded in
  manifests and cache keys, so any code change invalidates cached results.
- A plugin is compatible with host API `X.Z.*` if it was built for `X.Y.*` with
  `Y <= Z`. During `0.x` the minor version must match exactly.
