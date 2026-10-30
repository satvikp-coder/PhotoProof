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
| `python main.py web` | Start the read-only web foundation on validated `ui.host:ui.port` (default `127.0.0.1:8501`); Ctrl+C stops it | CLI codes |
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
    application (`status`, `analyze`, `manifest`, `preflight`, `web`) first run preflight:
    quick health checks, SAFE auto-repair (creates `ARE/`), re-check. `doctor`,
    `repair`, `config` and `plugins` deliberately skip preflight and its repairs.

Launcher flags must come **before** the command. Everything from the command onwards
goes to the CLI unchanged: `python main.py repair --dry-run` is a dry run.

Hard requirements fail; recommended resources warn; the GPU is informational.
`--doctor` also lists packages installed in `.venv` that no lockfile pins.
Set `PHOTOPROOF_LAUNCHER_VERBOSE=1` to print the launcher report on every start.

## Web interface

Run `python main.py web` and open `http://127.0.0.1:8501` (or the configured port).
Analyze, Compare and History are labelled placeholders. Diagnostics displays real
quick health checks and installed plugin policy; use `python main.py --doctor` for
full evidence hashing and local-only error details. Requests do not run repairs.

Only local IP addresses/`localhost` are accepted by default. Configure `ui.host` and
`ui.port` in `settings.yaml` before launch; there are no command-line bind overrides.
The service runs one process without reload, proxy-header trust or HTTP access logs
that could capture query strings. Non-loopback bindings are unauthenticated and
display a warning. Production rejects them until authentication exists. Hostname-based
reverse proxy deployments are outside this foundation's supported boundary.

There are no upload, analysis, repair or blacklist-bypass HTTP routes. In particular,
`python main.py --ignore-blacklist web` is rejected. Error pages contain no tracebacks,
paths or configuration dumps. Inspect local logs/doctor if a generic error persists.
Read [WebKit's guide](https://github.com/satvikp-coder/PhotoProof/blob/main/WebKit/README.md)
in the repository for extension contracts.
