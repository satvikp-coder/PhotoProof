# Launcher and environment setup

`main.py` uses only the standard library, so it can run before any dependency exists.
It **requires an already installed Python 3.11–3.15**; it never installs Python, never
uses administrator rights and only downloads the hash-verified packages listed in
`requirements.lock`.

## Commands

| Command | Behaviour | Exit codes |
|---|---|---|
| `python main.py` | Full bootstrap, preflight, then `status` | 0, 3, 4, 5, 6 |
| `python main.py <command> [args]` | Same, then forwards to the CLI (`plugins`, `config`, `analyze IMG`, `manifest RUN_ID`, …) | CLI codes |
| `python main.py --version` | Print `PhotoProof <version>` from `app.toml`; nothing else | 0, 3, 4 |
| `python main.py --doctor` | Launcher diagnostics + application `doctor`; changes nothing | 0, 6 |
| `python main.py --repair [--dry-run]` | Controlled repair: may rebuild an incompatible `.venv`, reinstalls dependencies, runs application repairs | 0, 6 |
| `python main.py --force-setup` | Reinstall locked dependencies even if the fingerprint matches | 0, 5 |
| `python main.py --ignore-blacklist <command>` | Audited bypass of `plugins.blacklist` | CLI codes |

Exit codes: `0` ok · `2` usage · `3` unsupported interpreter/OS/architecture/hardware ·
`4` identity or configuration invalid · `5` dependency installation failed ·
`6` preflight/health failure · `7` analysis did not complete · `130` interrupted.

## Bootstrapping sequence

1. Locate the repository root (directory of `main.py`).
2. Read `app.toml`; validate identity and `src/<package>/`.
3. Validate the interpreter against `[python]`.
4. Detect OS and CPU architecture against `[platform]`.
5. Check CPU cores, total memory (Linux `/proc/meminfo`, macOS `sysctl hw.memsize`,
   Windows `GlobalMemoryStatusEx`; otherwise *unknown*) and free disk.
6. Inspect `.venv/`; create it if missing. An existing venv with an unsupported Python
   is **not** deleted on a normal launch — `--repair` is required.
7. Verify every line of `requirements.lock` is `==`-pinned and hashed.
8. Compute the environment fingerprint: SHA-256 of lockfile + `pyproject.toml` + venv
   Python version + app version. If it differs from `.venv/.photoproof-env.json`,
   install with `pip install --require-hashes --no-deps --only-binary :all:` and then
   `pip install --no-deps --no-build-isolation --no-index -e .`.
9. On macOS, clear the `UF_HIDDEN` flag on `.pth` files (Python ≥ 3.13 ignores hidden
   `.pth` files, which would silently break the editable install).
10. Verify the package **imports** at the `app.toml` version.
11. Start the requested command (`status` by default). Commands that start the
    application (`status`, `analyze`, `manifest`, `preflight`) first run preflight:
    quick health checks, SAFE auto-repair (creates `ARE/`), re-check. `doctor`,
    `repair`, `config` and `plugins` deliberately skip preflight and its repairs.

Launcher flags must come **before** the command. Everything from the command onwards
goes to the CLI unchanged: `python main.py repair --dry-run` is a dry run.

Hard requirements fail; recommended resources warn; the GPU is informational.
`--doctor` also lists packages installed in `.venv` that no lockfile pins.
Set `PHOTOPROOF_LAUNCHER_VERBOSE=1` to print the launcher report on every start.

## Durable queue lease ownership

Jobs use single-host UTC lease deadlines. SQLite parses both legacy space-separated
and ISO timestamps when comparing deadlines; string ordering is never used for
expiry. A claim alone consumes an attempt. A failed or expired claim returns to the
queue only while `attempts < max_attempts`; recovery does not consume a second
attempt. The default is one attempt, so forensic analyses are not blindly retried.
Concurrent duplicate submissions share one idempotency key and one job ID.

### Queue API migration (MAJOR)

Completion, failure and renewal now require the **original Job returned by claim**
as a keyword argument. This prevents an old worker from changing a newer claim,
even when the worker identifier is reused. Keep the original token; a later lookup
is not a substitute for ownership. All SQL transitions also check the current owner,
attempt and a still-live lease. Repeating the same valid completion is harmless.

```python
job = queue.claim("worker-1")
if job is not None:
    # Execute this claimed request, renewing the lease as needed.
    queue.renew(job.job_id, claim=job)
    queue.complete(job.job_id, claim=job)
    # On execution failure instead: queue.fail(job.job_id, message, claim=job)
```

Callers that previously used `complete(job_id)`, `fail(job_id, message)` or published
state from a fresh queue without the claim token must migrate as above. A lost
claim raises `StorageError` for completion/failure and returns false for renewal;
the caller must stop publishing that attempt's state.

WAL remains enabled for writer connections, with a busy timeout and atomic claims.
Keep the WAL/SHM sidecars with a live database and use PhotoProof's SQLite backup
API for a consistent backup rather than copying a live database file by itself.
Queue and controller results remain distinct from forensic assessment: an execution
state never proves an image's provenance.

## Reference comparison input checks

Use `python main.py analyze TARGET --reference REFERENCE --profile comparison`.
Both files pass ingestion, verified evidence storage and isolated decoding. The manifest
records `comparison.preflight` with the reference trust statement, both hashes, working
pixel/byte equality, shape/aspect relationships and size warnings. A reference is
user-supplied comparison material. Differences do not identify which file to trust.

Identical decodable bytes produce `inputs are identical` and comparison plugins are
NOT_APPLICABLE; no change evidence is invented. Other selected detectors retain their
normal pipeline-health outcomes. Missing or rejected reference pixels yield INCOMPLETE
with an unavailable preflight. Combined image pixels share one configured budget rather
than each receiving a full allowance. Registration and interpretation remain separate
implementation stages; preflight alone is not a forensic assessment.
