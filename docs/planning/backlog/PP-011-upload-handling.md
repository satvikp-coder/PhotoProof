---
id: PP-011
title: "feat(core): ingest uploads with size limits, quarantine and format validation"
milestone: M1
labels: [type:feature, priority:P1, area:core, area:security, effort:medium, status:blocked]
depends_on: [PP-001, PP-002]
---
## Summary

Implement the ingestion service used by the CLI and WebKit. It receives bytes (a path or
a stream), applies upload limits, stages them in `quarantine/`, validates the format,
and only then promotes them into the evidence store.

## Problem Statement

There is no single, policy-enforcing entry point for untrusted files. WebKit uploads
(PP-039) will arrive as streams of unknown size and type, and each caller would
otherwise reimplement the checks.

## Objective

`IngestionService.ingest(stream, *, declared_name, role) -> InputDescriptor` is the only
way untrusted bytes enter PhotoProof.

## Technical Background

Defence in depth for uploads: cap the size while streaming (never trust
Content-Length); stage outside the trusted store; identify the type by content rather
than extension; promote atomically. This is a quarantine pattern.

## Functional Requirements

- Streaming size cap (`resources.max_input_bytes`). Abort and delete the staged file
  when it is exceeded.
- Stage under `ARE/quarantine/<random>` (0600). The random name is never derived from
  user input.
- `detect_format` (PP-002) must succeed and the format must be accepted. Otherwise
  reject and delete the staged file.
- On success, move into the evidence store (PP-001) and return an `InputDescriptor` with
  `media_format` set.
- Rejections are recorded in the audit trail (reason, size, detected format; no content).
- Quarantine leftovers older than `runtime.temp_max_age_hours` are a SAFE cleanup in the
  `runtime.stale_temp` health check.

## Technical Implementation

- `src/photoproof/core/ingestion.py`. The CLI `analyze` switches to this service.
- Add `quarantine/` handling to `RuntimeStorage` (safe paths, owner-only).
- New event type `INPUT_REJECTED` (audited). This adds to the contracts enum (MINOR).

## Integration Requirements

CLI now; WebKit later. The controller receives only `InputDescriptor`s that came out of
ingestion.

## Security and Edge Cases

Slow-loris uploads (WebKit applies a per-request timeout; document the boundary). Names
with control characters or path separators. Empty streams. A concurrent ingest of the
same content.

## Testing Requirements

Oversized streams abort early: a test with a generator stream must read at most
`max_input_bytes + chunk` bytes. Unknown formats and disabled formats are rejected.
Quarantine cleanup works. Audit entries are written.

## Acceptance Criteria

- [ ] All CLI inputs flow through `IngestionService`.
- [ ] Oversized streams are rejected without reading more than one chunk past the limit.
- [ ] Unknown or disabled formats are rejected and nothing remains in `quarantine/` or `objects/`.
- [ ] Every rejection produces an `INPUT_REJECTED` audit entry.
- [ ] Stale quarantine files are cleaned by the SAFE repair.

## Expected Outcome

One audited, policy-enforcing doorway for every untrusted file.

## Documentation Requirements

Security model (untrusted input boundaries), operations runtime docs.

## Dependencies

PP-001, PP-002.

## Priority and Effort

P1, medium.
