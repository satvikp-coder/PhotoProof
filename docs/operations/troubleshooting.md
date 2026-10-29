# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Python 3.x is not supported` (exit 3) | Interpreter outside 3.11–3.15 | Run with a supported interpreter: `python3.12 main.py` |
| `.venv uses Python …, which is unsupported` | venv built with another interpreter | `python3.12 main.py --repair` |
| `dependency installation failed` (exit 5) | Network, or no wheel for your platform | Check connectivity. `--only-binary :all:` is deliberate: report the platform in an issue rather than building from source. |
| `photoproof is not importable` | Broken editable install; on macOS possibly hidden `.pth` files | `python main.py --force-setup` (the launcher clears hidden flags) |
| `ModuleNotFoundError: photoproof` when running `.venv/bin/pytest` directly on macOS | The checkout is in an iCloud-synced folder (`~/Documents`, `~/Desktop`), which keeps re-applying the hidden flag to `.pth` files; Python ≥ 3.13 ignores hidden `.pth` files | Move the checkout outside iCloud-synced folders, or run tools with `PYTHONPATH=src`. `python main.py` clears the flag on every launch. |
| `manifest … does not match the SHA-256 recorded` | The manifest file was edited after the run | Treat the run as untrustworthy; re-run the analysis |
| `warning: plugins.policy` at startup | A blacklist, denylist or `required_plugins` entry names no installed plugin | Fix the typo; a misspelled blacklist entry blocks nothing |
| `configuration error` (exit 4) | Invalid `settings.yaml`, profile or `app.toml` | The message names the file and key; see [settings](../configuration/settings.md) |
| `unsupported environment override PHOTOPROOF_…` | Unknown `PHOTOPROOF_*` variable set | Unset it; only the documented variables are allowed |
| `Preflight failed` (exit 6) | A check at or above `health.preflight_fail_on` | `python main.py --doctor`, then `--repair` if it is repairable |
| `needs migration` | Database schema older than the code | `python main.py --repair` (backup is created first) |
| `newer than this release supports` | Database written by a newer PhotoProof | Upgrade PhotoProof; do not downgrade data |
| Analysis returns `NO_DETECTORS` | No detectors are installed yet | Expected in 0.1.0 |
| Analysis returns `BLOCKED` | A required plugin is disabled | Read `blocked_reasons`; check the blacklist, flags and profile |
