# ADR-013: Verified reference inputs and comparison preflight

## Status
Accepted for #27; plugin API addition 1.3.0. Registration and interpretation remain separate issues.

## Context
A reference descriptor alone is not usable pixel evidence. Both roles must pass the same
verified evidence/isolated decoder path and remain distinguishable. A reference is
user-supplied comparison material; its relationship to a target does not establish which
version should be trusted.

## Decision
Comparison requests resolve both stored inputs before comparison execution. Each unique
content hash is decoded once. Equal hashes reuse immutable decoded/working buffers; no
second allocation or evidence modification is needed. Other inputs use independent decoder
calls. The host supplies read-only `image.reference_decoded` and
`image.reference_working`, with the existing target media contracts unchanged. Plugins may
only see declared inputs. These host-reserved additions and `comparison.preflight` require
a plugin API MINOR bump to 1.3.0; older supported plugins remain compatible.

The combined pixel ceiling is the smaller of `resources.max_image_pixels` and
`DecodeLimits.max_decoded_bytes // 12`. Twelve bytes conservatively reserve up to eight
bytes of decoded 16-bit RGBA plus four working bytes per unique pixel. The reference
worker receives the remaining pixel/byte limits, checked from its header before pixel
allocation. This bounds retained image buffers, not whole-process RSS or transient
normalization/codec allocations. Existing isolated decoder resource enforcement remains
in use. Input bytes remain separately capped per file. No new codec or native parser is
introduced outside the existing image path.

`comparison.preflight` is a reserved input and persisted output with:

- `reference_statement`: `reference: user-supplied comparison material`;
- both hashes, byte equality and normalized-working-pixel equality;
- normalized target/reference shapes and `coordinate_space: normalized_working_images`;
- target/reference width, height and area ratios, absolute aspect-ratio difference;
- a warning for area ratios over eight in either direction, without arbitrary rejection;
- status/message or an explicit unavailable reason.

Identical verified, decodable bytes produce the informational message `inputs are
identical`. Plugins in the comparison namespace become NOT_APPLICABLE without running;
this preflight satisfies even required comparison tasks without fabricating outputs. Other
required failures retain normal abort/degrade policy. A comparison-only run can complete;
overall pipeline health can still be incomplete if unrelated selected detectors fail.
Undecodable identical streams do not take this shortcut. Re-encoded equal working pixels
are recorded but do not silently skip comparison or metadata differences.

Missing/invalid reference media yields an unavailable preflight and incomplete run, never
zero pixels. A policy-blocked run records the reference statement with status not_run.
Historical manifests are not rewritten. Cache identity already includes both hashes and
settings; media-resolution failures cannot create completed cache entries.

Before decoding, descriptor sizes and any claimed formats must match the verified bytes.
Contradictory descriptors yield unavailable comparison media; an unspecified optional
format is resolved from the decoded evidence and recorded in the manifest. Run cache
identity includes the host plugin API so an execution-contract upgrade cannot reuse
results computed under older media availability semantics.

## Alternatives
Treating a reference descriptor as a trusted image or comparing unregistered pixels was
rejected. Giving each image the full memory budget could retain twice the configured
image buffers. A fixed half-budget for each input would unnecessarily reject valid highly
asymmetric pairs; sequential remaining-budget checks retain that use case.

## Consequences
Registration (#28), SSIM (#29), change masks (#30) and measured interpretation (#31) still
need implementation. Preflight differences alone never constitute a forgery finding.
No dependency, original-evidence mutation or plugin filesystem capability is added.
