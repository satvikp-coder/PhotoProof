# Health checks and repair

The Application Integrity and Recovery Manager (`core/health/`) is fsck-inspired:
checks **never** change anything; repairs are separate, classified actions.

## Checks

| ID | Detects | Severity | Repair |
|---|---|---|---|
| `runtime.directories` | Missing `ARE/` directories | error | SAFE: create (0700) |
| `runtime.permissions` | Runtime directories accessible to other users (POSIX) | warning | SAFE: chmod 0700 |
| `runtime.disk_space` | Free space below `resources.min_free_disk_mb` | error | MANUAL |
| `runtime.stale_temp` | Old files in `tmp/`, interrupted-write `.*.part` leftovers | warning | SAFE: delete exactly those files |
| `database.integrity` | Missing DB (warning, SAFE init); pending migration (error, CONTROLLED: back up then migrate); newer/unknown schema or failed `PRAGMA integrity_check` (critical, MANUAL) | — | — |
| `cache.integrity` | Unparseable entries, missing/corrupt referenced artifacts | warning | SAFE: invalidate entries |
| `storage.integrity` | Blobs in `objects/` (evidence, critical) or `artifacts/` (error) whose hash mismatches | — | MANUAL, never modified |
| `application.version` | Installed package version ≠ `app.toml` | error | CONTROLLED: `python main.py --repair` |
| `plugins.graph` | Invalid plugin graph (error) or unavailable plugins (warning) | — | MANUAL |
| `plugins.policy` | Blacklist, denylist or profile `required_plugins` entries naming unknown plugins (likely typos — a misspelled blacklist entry blocks nothing) | warning | MANUAL |
| `database.stale_runs` | Runs older than one hour that never finished (killed process) | warning | CONTROLLED: mark them `ABORTED` |

**Quick and deep mode.** Preflight (every launch) runs the checks in quick mode:
`storage.integrity` only verifies that blobs sit at well-formed content addresses and
`cache.integrity` only parses entries, so startup time does not grow with stored data.
`doctor` and `repair` run deep mode and re-hash every blob and cached artifact. The
cache always verifies artifacts before reusing them, whatever the mode.

Non-blocking findings are printed as warnings at startup and recorded in the audit
trail and the `health_checks` table.

A check that crashes is itself reported as a critical finding. Checks never create or
change anything (they do not even create a missing database file).

## Repair categories

| Category | When it runs | Examples |
|---|---|---|
| **SAFE** | Automatically during preflight (`recovery.auto_repair_safe`) and on `repair` | Create directories, invalidate cache entries, delete stale temp files |
| **CONTROLLED** | Only on explicit `--repair` | Rebuild `.venv`, migrate the database (after backup), reinstall |
| **MANUAL** | Never automated | Corrupt evidence, unknown schema, low disk, broken plugins |

Rules enforced in code: original evidence is never modified; user data is never
deleted; no recursive deletion is used (temp cleanup deletes named files only, and
refuses anything outside `tmp/` or not a `.part` leftover); databases are only
migrated after a backup is written next to them; database restore from backups is a
MANUAL action.

Every applied repair is re-verified by re-running its check, emits `REPAIR_APPLIED`
(appended to the audit trail) and is reported as `planned`, `applied`, `failed`,
`skipped` or `manual`.

```bash
python main.py --doctor            # inspect
python main.py --repair --dry-run  # what would change
python main.py --repair            # apply SAFE + CONTROLLED
```
