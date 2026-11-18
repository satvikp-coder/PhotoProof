# ADR-014: Bounded verified reads of declared artifact inputs

## Status
Accepted design for PP-084 (#153). Local implementation currently builds on #27;
final integration and API allocation must be verified after that prerequisite lands.

## Context
Pixel producers persist derived arrays as artifacts. Consumers need actual bytes, not
reference dictionaries masquerading as numerical arrays. The framework must retain
provenance and integrity checks without exposing arbitrary runtime paths.

## Decision
Plugin API 1.4.0 adds an optional `ArtifactReader` callable and
`PluginContext.artifact(input_name, *, max_bytes=64 << 20, selector=()) -> bytes`. Existing context
constructors remain valid; contexts without a broker fail visibly when reads are requested.
No path, URL or arbitrary digest argument is accepted by the supported API. A
selector is at most eight explicit JSON string keys/nonnegative list indices within
that already-declared input, for example `(0, "map")` in an evidence-layer collection.
It is not a filesystem path or an executable query. The selected reference must have
been included at that exact location by the successful producer; wildcards, negative
indices and grants added by later context mutation are rejected.

The controller constructs a capability per invocation from resolved declared inputs, the
execution plan's output producers, and the current execution's staged writer records.
A reference must match the producing contract and an exact ArtifactRef actually written
by that producer in this run. Executor output resolution supplies only successful upstream
outputs. Authorization records are bound before plugin invocation; context mutations cannot
add a new grant. Whole-run cache hits do not invoke plugins. Any future per-plugin cache
must explicitly register verified reused artifacts; possession of a historical hash alone
is not authority to read it.

Effective byte ceiling is the smaller of the caller's requested maximum and
`resources.max_artifact_read_bytes` (default 64 MiB, bounded to 2 GiB). This is separate
from raw-image ingestion size: a small compressed image may have a larger derived array.
The broker checks declared size before opening, opens only a regular non-symlink blob
through RuntimeStorage paths, checks observed size, reads bounded chunks and verifies
actual size/SHA-256 before returning immutable bytes. A file changed during reading is
rejected; existing bytes are never repaired, replaced or deleted.

The operation bounds bytes read, not total process RSS. Array parsing remains the consuming
plugin's responsibility: numerical loaders must disable pickle, validate format/header
sizes and bound array allocations. Hash verification alone does not establish a benign
format or calibrated forensic meaning. A supported API capability is not a Python sandbox;
trusted in-process code retains the executor's existing limitations. Process isolation and
the parent/child broker adaptation remain #52.

## Alternatives
Passing runtime paths or arbitrary hash lookup would broaden access beyond declared
current-run evidence. Treating metadata dictionaries as arrays invents invalid measurements.
Recomputing every upstream algorithm in each consumer loses direct artifact consumption
and wastes work. A bounded parent-owned byte operation preserves the existing output
contracts and can later be carried over an IPC protocol.

## Compatibility and migration
API 1.4.0 follows the reference-media additions in 1.3.0. Application release version is
unchanged. Existing plugins that do not read artifacts retain behavior. New consumers
must declare the artifact contract and require host API 1.4.0. New optional configuration
has a default, so older settings remain valid; older hosts may reject the new key, which
must be removed when rolling back. No historical manifests or stored evidence are rewritten.
