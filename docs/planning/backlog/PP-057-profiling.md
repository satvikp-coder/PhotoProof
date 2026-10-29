---
id: PP-057
title: "perf(core): profile resource usage and optimize hot paths"
milestone: M6
labels: [type:enhancement, priority:P3, area:performance, effort:medium, status:blocked]
depends_on: [PP-024, PP-019]
---
## Summary

Add per-plugin resource metrics (wall time, CPU time, peak RSS) to manifests, build a
reproducible benchmark run, and optimize the measured hot paths. Measure first, then
optimize.

## Problem Statement

Once several detectors exist, total runtime and memory will determine usability. Without
measurements, optimization is guesswork.

## Objective

Resource metrics in every manifest, a `tools/benchmark.py` with stored baselines, and
documented optimizations with before/after numbers.

## Technical Background

`time.perf_counter`, `time.process_time` and `resource.getrusage` (POSIX; on Windows use
`psutil`, or record "unknown"). Peak memory per plugin is only measurable precisely with
process isolation (PP-052). In-process, use tracemalloc for Python allocations and
document its limits. Profile with `cProfile` and `py-spy`.

## Functional Requirements

- Manifest `executions[*].resources`: `wall_ms`, `cpu_ms`, `peak_rss_mb | null`.
  Excluded from the computational fingerprint.
- `tools/benchmark.py`: a fixed generated image set (sizes 2/12/24/48 MP) × the standard
  profile; outputs a Markdown table. Baselines committed as summaries only.
- At least the top two hot paths are optimized, with numbers.

## Technical Implementation

The executor records metrics. Benchmarks run locally and in an optional manual CI
workflow (not on every PR).

## Integration Requirements

The metrics feed the developer diagnostics (PP-049) and documentation.

## Security and Edge Cases

Metrics are excluded from fingerprints and cache keys (they are volatile). The benchmark
must not require datasets.

## Testing Requirements

Unit test that metrics are present and excluded from the fingerprint. Benchmark smoke
test on a tiny image.

## Acceptance Criteria

- [ ] Manifests include per-plugin resource metrics, excluded from fingerprints (test).
- [ ] Benchmark baselines are documented for four image sizes.
- [ ] Optimizations are backed by before/after measurements in the PR.

## Expected Outcome

Known, improving performance characteristics.

## Documentation Requirements

`docs/planning/` performance page; operations docs (metrics fields).

## Dependencies

Representative detectors (PP-024, PP-019).

## Priority and Effort

P3, medium.
