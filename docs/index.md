# PhotoProof

PhotoProof is a modular framework for **digital image forgery detection and
localization**, built by a four-person university team.

!!! warning "Current status — foundation only (0.1.0)"
    The plugin execution framework, configuration system, launcher, persistence,
    caching, health checks and automation are implemented and tested.
    **No forensic detector is installed yet.** An analysis today reports
    `NO_DETECTORS` and never produces an authenticity verdict. Every detector,
    image decoder and user interface is tracked as [GitHub issues](https://github.com/satvikp-coder/PhotoProof/issues) and
    summarized in the [roadmap](planning/index.md).

## What works today

| Capability | Status |
|---|---|
| Stdlib-only launcher (`python main.py`) with venv, hash-pinned dependencies, drift detection | Implemented |
| Validated identity (`app.toml`), settings, profiles, feature flags, plugin policy | Implemented |
| Trusted plugin discovery, registry, DAG planner, sequential executor, state machine | Implemented |
| SQLite persistence, content-addressed storage, run-level cache, execution manifests | Implemented |
| Event bus, structured logging, audit trail, health checks, safe/controlled repair | Implemented |
| Image decoding, forensic detectors, evidence fusion, WebKit UI, reports | Planned |

## Start here

- New contributor: [Development](development/index.md) and
  [CONTRIBUTING.md](https://github.com/satvikp-coder/PhotoProof/blob/main/CONTRIBUTING.md)
- Writing a detector: [Plugin development](plugins/index.md)
- Understanding the design: [Architecture](architecture/index.md)
- Running it: [Operations](operations/index.md)

```bash
python3.12 main.py            # set up and start
python3.12 main.py --doctor   # diagnose
python3.12 main.py plugins    # list plugins and effective policy
```
