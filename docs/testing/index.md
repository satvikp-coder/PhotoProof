# Testing

| Suite | Focus |
|---|---|
| `tests/unit` | Versioning, configuration validation, feature flags, policy precedence, registry, planner, state machine, executor semantics, storage, database, events/logging, manifests, launcher (mocked), governance script |
| `tests/integration` | Full orchestration through `AnalysisController` with mock plugins: manifests, persistence, comparison mode, blacklist and bypass, blocking, cache behaviour, health checks and repairs |
| `tests/resilience` | Plugin exceptions, invalid outputs, timeouts, interruption, subscriber failures, persistence failure, missing runtime directories, partial writes, corrupted configuration |
| `tests/security` | Input size limits, configuration cannot add plugin locations, no filesystem access in the plugin context, manifest path traversal, production-only restrictions |
| `tests/e2e` | Real subprocesses: `main.py --version`, CLI `status`/`doctor`/`analyze`/`manifest`/`config` against a temporary root |
| `tests/fixtures/mockplugins` | Trusted test plugins: valid graph, broken metadata, duplicates |

Fixtures (`tests/conftest.py`): `app_root` copies the real configuration into a
temporary directory; `make_app` bootstraps and starts an application on it;
`helpers.registry_of` builds throwaway plugin packages from metadata dictionaries.
No test touches the real `.venv/`, `ARE/` or settings.

Coverage is reported (branch coverage) but there is no arbitrary percentage gate.
Critical configuration, policy, planner and executor branches have explicit tests.

## Copy-move ground truth

The synthetic `copy_move` helper applies `translate=(dy,dx)` to the source origin,
including negative offsets, and records the actual bounding boxes. A zero-offset,
unrotated, unscaled copy is unchanged and has an all-zero ground-truth mask. The
0/1/2 role mask cannot represent overlapping source/target roles, so overlapping
nonidentity requests are rejected, as are out-of-image placements. Scaling is
bounded before allocating the resized patch. The input pixels are never modified.

Older generated fixtures always placed the target near a fixed corner, regardless
of the requested translation. Regenerate those fixtures before using their declared
translation as evaluation ground truth; do not relabel old measurements or claim
previous accuracy results remain validated.

## Not covered by automated tests

- Real dependency installation inside a fresh venv is exercised by the CI
  *launcher smoke* job, not by pytest.
- Memory detection on Windows (`GlobalMemoryStatusEx`) and the macOS `UF_HIDDEN` fix
  are covered only on their respective CI runners.
- POSIX permission assertions are skipped on Windows. Symlink-escape tests need
  symlink privileges on Windows.
- Native-code crashes in the application cannot occur yet (no native decoders). The
  child-process runner is tested against real crashes.
- A real segfault opens a macOS crash dialog, so that test runs in CI only on macOS. Locally,
  `killed_abruptly` covers the same path.
