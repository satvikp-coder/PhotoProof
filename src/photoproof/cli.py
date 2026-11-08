"""Command-line interface. ``python main.py <command>`` forwards here.

Exit codes: 0 success · 1 runtime error · 2 usage error · 4 configuration/identity
error · 6 health/preflight failure · 7 analysis did not complete (BLOCKED, ABORTED,
INCOMPLETE).
"""

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.enums import RunStatus
from photoproof.contracts.events import Event, EventType
from photoproof.contracts.exceptions import (
    ConfigurationError,
    InputRejectedError,
    PhotoProofError,
    PolicyError,
    StorageError,
)
from photoproof.core.bootstrap import DEFAULT_ROOT, Application, bootstrap
from photoproof.core.config.policy import (
    BlacklistOverride,
    OverrideOrigin,
    authorize_blacklist_override,
    decide_all,
)
from photoproof.core.controller import AnalysisController
from photoproof.core.database.store import Database
from photoproof.core.health.checks import CheckResult, default_registry
from photoproof.core.health.repair import preflight, publish_findings, run_repairs
from photoproof.core.pipeline.manifest_tools import (
    diff_manifests,
    fingerprint_matches,
    manifest_graph,
    manifest_timeline,
)
from photoproof.core.reporting import build_report, render_html, render_json
from photoproof.core.scaffold import ScaffoldError, generate
from photoproof.core.services.housekeeping import HousekeepingService
from photoproof.core.storage.store import sha256_file

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_CONFIG, EXIT_HEALTH, EXIT_NOT_COMPLETED = 0, 1, 2, 4, 6, 7
_MARK = {"ok": "✓", "warning": "!", "error": "✗", "critical": "✗✗"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photoproof", description="PhotoProof command line")
    parser.add_argument(
        "--ignore-blacklist",
        action="store_true",
        help="administrative: bypass plugins.blacklist for this invocation (audited)",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="show identity and installed detectors (default)")
    sub.add_parser("version", help="print the application version")
    doctor = sub.add_parser("doctor", help="diagnose health; changes nothing")
    doctor.add_argument("--json", action="store_true")
    repair = sub.add_parser("repair", help="apply safe and controlled repairs")
    repair.add_argument("--dry-run", action="store_true")
    sub.add_parser("preflight", help="startup checks with safe auto-repair (used by main.py)")
    plugins = sub.add_parser("plugins", help="list plugins and effective policy")
    plugins.add_argument("--profile")
    plugins.add_argument("--json", action="store_true")
    config = sub.add_parser("config", help="print effective settings and feature flags")
    config.add_argument("--json", action="store_true")
    analyze = sub.add_parser("analyze", help="run the analysis pipeline on an image")
    analyze.add_argument("target", type=Path)
    analyze.add_argument("--reference", type=Path)
    analyze.add_argument("--profile")
    analyze.add_argument("--no-cache", action="store_true")
    analyze.add_argument("--progress", action="store_true", help="print plugin progress to stderr")
    analyze.add_argument("--json", action="store_true")
    manifest = sub.add_parser("manifest", help="show an execution manifest")
    manifest.add_argument("run_id")
    manifest.add_argument("--json", action="store_true")
    manifest.add_argument(
        "--graph",
        choices=["mermaid", "dot"],
        help="print the dependency graph as Mermaid or DOT",
    )
    manifest.add_argument(
        "--timeline",
        action="store_true",
        help="print a per-plugin start and duration table",
    )
    manifest_diff = sub.add_parser("manifest-diff", help="compare two execution manifests")
    manifest_diff.add_argument("run_a")
    manifest_diff.add_argument("run_b")
    manifest_diff.add_argument("--json", action="store_true", help="output JSON instead of text")
    housekeeping = sub.add_parser(
        "housekeeping", help="report and apply storage retention and quotas"
    )
    housekeeping.add_argument(
        "--apply",
        action="store_true",
        help="apply deletions (default is dry run)",
    )
    backup_cmd = sub.add_parser("backup", help="create a verified backup of the SQLite database")
    backup_cmd.add_argument(
        "--target",
        type=Path,
        help="backup destination (default: ARE/database/backups/<timestamp>.db)",
    )
    restore = sub.add_parser("restore", help="restore the SQLite database from a verified backup")
    restore.add_argument(
        "backup",
        type=Path,
        help="backup file to restore from",
    )
    restore.add_argument(
        "--dry-run",
        action="store_true",
        help="verify the backup and report what would be restored, without changing anything",
    )
    report_cmd = sub.add_parser("report", help="generate a forensic report for a run")
    report_cmd.add_argument("run_id", help="run ID to report on")
    report_cmd.add_argument(
        "--format",
        choices=["html", "json"],
        default="html",
        help="output format (default: html)",
    )
    report_cmd.add_argument(
        "--output",
        type=Path,
        help="write report to this file (default: stdout)",
    )
    new_plugin = sub.add_parser(
        "new-plugin", help="generate a plugin skeleton from a family and name"
    )
    new_plugin.add_argument("plugin_id", help="plugin id, e.g. copymove.ela")
    new_plugin.add_argument(
        "--priority",
        type=int,
        help="plugin priority (default depends on family)",
    )
    new_plugin.add_argument(
        "--required",
        action="store_true",
        help="mark the plugin as required",
    )
    new_plugin.add_argument(
        "--no-enabled-default",
        action="store_true",
        help="disable the plugin by default",
    )
    new_plugin.add_argument(
        "--description",
        help="short description of the plugin",
    )
    diagnose = sub.add_parser(
        "diagnose", help="show diagnostics for failed or skipped plugins in a run"
    )
    diagnose.add_argument("run_id", help="run ID to diagnose")
    diagnose.add_argument(
        "--plugin",
        help="show detail for one plugin (default: list all non-success plugins)",
    )
    return parser


def _print_checks(results: Sequence[CheckResult]) -> None:
    for result in results:
        print(f"  {_MARK[result.severity.value]:<2} {result.check_id:<22} {result.message}")
        for detail in result.details[:10]:
            print(f"       - {detail}")
        if not result.ok and result.repair:
            print(f"       repair ({result.repair.category.value}): {result.repair.description}")


def _override(args: argparse.Namespace) -> BlacklistOverride | None:
    return BlacklistOverride(OverrideOrigin.LOCAL_CLI) if args.ignore_blacklist else None


def _start(app: Application) -> int | None:
    report = preflight(app, default_registry(deep=False))
    for record in report.repairs:
        print(
            f"repair {record.outcome}: {record.check_id}: {record.changes or record.action}",
            file=sys.stderr,
        )
    if not report.passed:
        publish_findings(app, report.results)  # audit trail only; persistence is not up
        print("Preflight failed:", file=sys.stderr)
        _print_checks(report.blocking)
        print("Run `python main.py --doctor` for details.", file=sys.stderr)
        return EXIT_HEALTH
    app.start()
    publish_findings(app, report.results)
    for result in report.results:  # non-blocking findings must not be invisible
        if not result.ok:
            print(f"warning: {result.check_id}: {result.message}", file=sys.stderr)
    return None


def cmd_status(app: Application) -> int:
    ident = app.identity.application
    print(f"{ident.name} {ident.version} ({ident.release_channel})")
    print(f"  environment : {app.settings.environment.mode}")
    print(f"  profile     : {app.settings.profiles.default} (available: {', '.join(app.profiles)})")
    print(f"  plugin API  : {ident.plugin_api_version}")
    print(
        f"  plugins     : {len(app.registry)} registered, "
        f"{len(app.registry.unavailable)} unavailable"
    )
    if not len(app.registry):
        print(
            "\nNo production forensic detectors are installed. Analyses will report "
            "NO_DETECTORS; no authenticity verdict is produced."
        )
    print("\nCommands: doctor · repair · plugins · config · analyze <image> · manifest <run-id>")
    return EXIT_OK


def cmd_doctor(app: Application, as_json: bool) -> int:
    results = default_registry().run_all(app)
    if as_json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        print(f"{app.identity.application.name} doctor")
        _print_checks(results)
    worst = max((r.severity.rank for r in results), default=0)
    return EXIT_HEALTH if worst >= 2 else EXIT_OK  # error or critical


def cmd_repair(app: Application, dry_run: bool) -> int:
    registry = default_registry()
    records = run_repairs(app, registry, allow_controlled=True, dry_run=dry_run)
    if not records:
        print("Nothing to repair.")
    for record in records:
        verified = "" if record.verified is None else f" (verified: {record.verified})"
        print(
            f"  [{record.outcome}] {record.check_id} ({record.category}): "
            f"{record.changes or record.action}{verified}"
        )
    if dry_run:
        return EXIT_OK
    remaining = [r for r in registry.run_all(app) if r.severity.rank >= 2]
    return EXIT_HEALTH if remaining else EXIT_OK


def cmd_plugins(
    app: Application, profile: str | None, override: BlacklistOverride | None, as_json: bool
) -> int:
    name = profile or app.settings.profiles.default
    if name not in app.profiles:
        raise ConfigurationError(f"unknown profile {name!r}; known: {sorted(app.profiles)}")
    selected = app.profiles[name]
    decisions = decide_all(
        (p.metadata for p in app.registry),
        settings=app.settings,
        flags=app.flags,
        profile=selected,
        override=authorize_blacklist_override(app.settings, override),
    )
    rows = [
        {
            "id": p.id,
            "version": p.metadata.version,
            "priority": p.metadata.priority,
            "required": p.metadata.required,
            "enabled": decisions[p.id].enabled,
            "decided_by": decisions[p.id].source.value,
            "reason": decisions[p.id].reason,
        }
        for p in app.registry
    ]
    unavailable = [vars(u) for u in app.registry.unavailable]
    if as_json:
        print(json.dumps({"plugins": rows, "unavailable": unavailable}, indent=2))
        return EXIT_OK
    if not rows and not unavailable:
        print("No plugins installed.")
    for row in rows:
        state = "enabled " if row["enabled"] else "disabled"
        print(
            f"  {state} {row['id']:<32} v{row['version']:<8} p{row['priority']:<4} {row['reason']}"
        )
    for item in unavailable:
        print(f"  unavail. {item['plugin_id'] or item['package']}: {item['reason']}")
    return EXIT_OK


def cmd_config(app: Application, as_json: bool) -> int:
    payload = {
        "settings": app.settings.model_dump(mode="json"),
        "feature_flags": app.flags.snapshot(),
    }
    print(json.dumps(payload, indent=2 if as_json else 1, sort_keys=True))
    return EXIT_OK


def cmd_analyze(app: Application, args: argparse.Namespace) -> int:
    if args.progress or app.flags.is_enabled("events.progress"):

        def progress(event: Event) -> None:
            print(
                f"  {event.type.value:<17} {event.plugin_id or ''} {event.data.get('state', '')}",
                file=sys.stderr,
            )

        app.bus.subscribe(
            progress,
            EventType.PLUGIN_STARTED,
            EventType.PLUGIN_COMPLETED,
            EventType.PLUGIN_FAILED,
            EventType.PLUGIN_SKIPPED,
            name="cli-progress",
        )
    controller = AnalysisController(app)
    # Before ingesting, so a mistyped profile does not leave a copy of the image behind.
    controller.resolve_profile(args.profile, has_reference=args.reference is not None)
    ingestion = app.ingestion()
    target, target_record = ingestion.ingest_path(args.target, role="target")
    reference, reference_record = (
        ingestion.ingest_path(args.reference, role="reference") if args.reference else (None, None)
    )
    request = AnalysisRequest(
        target=target,
        reference=reference,
        profile=args.profile,
        use_cache=not args.no_cache,
    )
    result = controller.analyze(request, override=_override(args))
    app.database().link_submissions(
        [record.submission_id for record in (target_record, reference_record) if record],
        result.run_id,
    )
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(
            f"run {result.run_id}: {result.status.value}"
            f"{' (cached)' if result.cache_hit else ''} — profile {result.profile}"
        )
        for plugin in result.plugins:
            note = (
                f" [{plugin.failure_category}] {plugin.message}" if plugin.failure_category else ""
            )
            print(f"  {plugin.state:<15} {plugin.plugin_id}{note}")
        for reason in result.blocked_reasons:
            print(f"  blocked: {reason}")
        if result.status is RunStatus.NO_DETECTORS:
            print(
                "  No forensic detectors are installed and enabled. This is not evidence of "
                "authenticity."
            )
        print(f"  manifest: {result.manifest_path}")
    ok = result.status in {RunStatus.COMPLETED, RunStatus.NO_DETECTORS}
    return EXIT_OK if ok else EXIT_NOT_COMPLETED


def cmd_housekeeping(app: Application, apply: bool) -> int:
    service = HousekeepingService(
        app.storage,
        app.database_or_none(),
        app.settings.retention,
        app.settings.quotas,
    )
    report = service.run(apply=apply)
    print(json.dumps(report.to_dict(), indent=2))
    if report.errors:
        return EXIT_ERROR
    return EXIT_OK


def cmd_backup(app: Application, target: Path | None) -> int:
    """Create a verified backup of the SQLite database."""
    app.storage.ensure_layout()
    backup_dir = app.storage.path("database", "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    target_path = target or backup_dir / f"{timestamp}.db"

    db = app.database()
    db.backup(target_path)
    manifest = Database.verify_backup(target_path)
    manifest["created_at"] = datetime.now(UTC).isoformat()
    manifest_path = target_path.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    print(f"backup created: {target_path}")
    print(f"manifest: {manifest_path}")
    print(f"verified: {manifest['verified']}")
    return EXIT_OK if manifest["verified"] else EXIT_ERROR


def cmd_restore(app: Application, backup: Path, dry_run: bool) -> int:
    """Restore the SQLite database from a verified backup."""
    manifest = Database.verify_backup(backup)
    if not manifest["verified"]:
        problems = []
        if manifest["integrity_check"] != "ok":
            problems.append(f"integrity_check={manifest['integrity_check']}")
        fk_issues = manifest["foreign_key_issues"]
        if isinstance(fk_issues, list):
            problems.append(f"foreign_key_issues={len(fk_issues)}")
        print(
            f"error: backup {backup} is not verified: {'; '.join(problems)}",
            file=sys.stderr,
        )
        return EXIT_ERROR

    app.storage.ensure_layout()
    backup_dir = app.storage.path("database", "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    current_db = app.database_path
    pre_restore = backup_dir / f"{timestamp}.db.pre-restore"

    if dry_run:
        print(f"would restore {backup} -> {current_db}")
        print(f"would back up current database to {pre_restore}")
        print(f"verified backup: {manifest['sha256']} (schema {manifest['schema_version']})")
        return EXIT_OK

    # Back up the current database first (if it exists)
    if current_db.is_file():
        app.database().backup(pre_restore)
        print(f"backed up current database to {pre_restore}")

    # Atomically swap
    shutil.copy2(backup, current_db)
    print(f"restored {backup} -> {current_db}")
    return EXIT_OK


def cmd_report(app: Application, run_id: str, fmt: str, output: Path | None) -> int:
    """Generate a forensic report for a run."""
    app.storage.ensure_layout()
    manifest = app.storage.read_json("manifests", f"{run_id}.json")
    if not isinstance(manifest, dict):
        print(f"error: manifest for run {run_id} is not a JSON object", file=sys.stderr)
        return EXIT_ERROR
    report = build_report(manifest)

    content = render_html(report) if fmt == "html" else render_json(report)

    if output is not None:
        output.write_text(content, encoding="utf-8")
        print(f"report written to {output}")
    else:
        print(content)
    return EXIT_OK


def cmd_new_plugin(args: argparse.Namespace) -> int:
    """Generate a plugin skeleton from a family and name."""
    try:
        target = generate(
            args.plugin_id,
            base=Path.cwd(),
            priority=args.priority,
            required=args.required,
            enabled_default=not args.no_enabled_default,
            description=args.description,
        )
    except ScaffoldError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE

    print(f"plugin scaffold created at {target}")
    print("Next steps:")
    print("  1. Add the docs page to the mkdocs.yml nav")
    print("  2. Write the algorithm in algorithm.py")
    print("  3. Run ruff and mypy on the generated files")
    print("  4. Bump the version and update the changelog")
    return EXIT_OK


def _failure_hint(ex: dict[str, object]) -> str:
    """Return an actionable hint for a failed/skipped plugin execution."""
    category = ex.get("failure_category")
    message = ex.get("message") or ""
    if category == "timeout":
        return f"execution exceeded the effective timeout; {message}"
    if category == "dependency_unavailable":
        deps = ex.get("dependency_status") or {}
        if not isinstance(deps, dict):
            deps = {}
        chain = ", ".join(f"{k}={v}" for k, v in deps.items()) or "no dependencies recorded"
        return f"dependency unavailable: {chain}"
    if category == "config_error":
        return f"configuration error: {message}"
    if category == "invalid_output":
        return f"output did not match declared contract: {message}"
    if category == "load_error":
        return f"plugin could not be loaded: {message}"
    if category == "interrupted":
        return "run was interrupted before this plugin finished"
    if category == "aborted_by_policy":
        return "plugin was aborted by policy"
    if category == "exception":
        return f"unhandled exception: {message}"
    return str(message or "no further detail")


def cmd_diagnose(app: Application, run_id: str, plugin_id: str | None) -> int:
    """Show diagnostics for failed or skipped plugins in a run."""
    app.storage.ensure_layout()
    manifest = app.storage.read_json("manifests", f"{run_id}.json")
    if not isinstance(manifest, dict):
        print(f"error: manifest for run {run_id} is not a JSON object", file=sys.stderr)
        return EXIT_ERROR

    executions = manifest.get("executions", {}) or {}
    if not executions:
        print(f"no plugin executions recorded for run {run_id}")
        return EXIT_OK

    non_success = {
        pid: ex
        for pid, ex in executions.items()
        if ex.get("state") not in {"completed", "not_applicable"}
    }

    if not non_success:
        print(f"run {run_id}: all plugins completed successfully")
        return EXIT_OK

    if plugin_id is not None:
        ex = non_success.get(plugin_id)
        if ex is None:
            print(
                f"plugin {plugin_id!r} not found among non-success plugins for run {run_id}",
                file=sys.stderr,
            )
            return EXIT_USAGE
        state = ex.get("state", "unknown")
        category = ex.get("failure_category") or "none"
        message = ex.get("message") or "none"
        started = ex.get("started_at") or "not started"
        finished = ex.get("finished_at") or "not finished"
        deps = ex.get("dependency_status") or {}
        dep_str = ", ".join(f"{k}={v}" for k, v in deps.items()) or "none"
        print(f"=== Plugin: {plugin_id} ===")
        print(f"state: {state}")
        print(f"failure category: {category}")
        print(f"hint: {_failure_hint(ex)}")
        print(f"message: {message}")
        print(f"started: {started}")
        print(f"finished: {finished}")
        print(f"dependency status: {dep_str}")
        if ex.get("exception_type"):
            print(f"exception type: {ex['exception_type']}")
        if ex.get("traceback"):
            print("\n--- traceback ---")
            print(ex["traceback"])
        # Re-execution hint
        profile = manifest.get("profile", "default")
        print(f"\nRe-run: python main.py analyze <target> --no-cache --profile {profile}")
        return EXIT_OK

    # List all non-success plugins
    print(f"Run {run_id}: {len(non_success)} non-success plugin(s)")
    print()
    for pid in sorted(non_success):
        ex = non_success[pid]
        state = ex.get("state", "unknown")
        category = ex.get("failure_category") or "none"
        print(f"  {state:<15} {pid:<40} {category}")
    print()
    print("Use `photoproof diagnose <run-id> --plugin <id>` for detail and traceback.")
    return EXIT_OK


def cmd_manifest(
    app: Application, run_id: str, as_json: bool, graph: str | None, timeline: bool
) -> int:
    path = app.storage.path("manifests", f"{run_id}.json")
    manifest = app.storage.read_json("manifests", f"{run_id}.json")
    run = app.database().get_run(run_id)
    recorded = run.get("manifest_sha256") if run else None
    if recorded is None:
        print(
            "warning: no database record of this manifest's hash; cannot verify it",
            file=sys.stderr,
        )
    elif sha256_file(path) != recorded:
        print(
            f"error: {path} does not match the SHA-256 recorded for run {run_id}; "
            "the manifest was modified after the run",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if not isinstance(manifest, dict):
        print(f"error: manifest for run {run_id} is not a JSON object", file=sys.stderr)
        return EXIT_ERROR

    if graph:
        print(manifest_graph(manifest, format=graph))
        return EXIT_OK
    if timeline:
        rows = manifest_timeline(manifest)
        if not rows:
            print("no plugin executions recorded")
            return EXIT_OK
        print(f"{'plugin':<40} {'state':<15} {'started':<28} {'duration':<10}")
        print("-" * 95)
        for row in rows:
            started = row["started_at"] or "-"
            dur = f"{row['duration_seconds']:.3f}s" if row["duration_seconds"] is not None else "-"
            print(f"{row['plugin_id']:<40} {row['state']:<15} {started:<28} {dur:<10}")
        return EXIT_OK

    if as_json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"run {manifest['run_id']}  status {manifest['status']}  profile {manifest['profile']}")
    print(f"  application   {manifest['application']}")
    print(f"  created       {manifest['created_at']}")
    print(f"  pipeline      {manifest['pipeline_fingerprint']}")
    print(f"  fingerprint   {manifest['computational_fingerprint']}")
    print(f"  cache         {manifest['cache']}")
    print(f"  override      {manifest['blacklist_override']}")
    print("  order         " + (" -> ".join(manifest["execution_order"]) or "(none)"))
    for pid, ex in manifest["executions"].items():
        print(f"    {ex['state']:<15} {pid} {ex.get('failure_category') or ''}")
    for reason in manifest["blocked_reasons"]:
        print(f"  blocked: {reason}")
    return EXIT_OK


def cmd_manifest_diff(app: Application, run_a: str, run_b: str, as_json: bool) -> int:
    """Compare two execution manifests and report the differences."""
    app.storage.ensure_layout()
    ma = app.storage.read_json("manifests", f"{run_a}.json")
    mb = app.storage.read_json("manifests", f"{run_b}.json")
    if not isinstance(ma, dict) or not isinstance(mb, dict):
        print("error: one or both manifests are not JSON objects", file=sys.stderr)
        return EXIT_ERROR

    if fingerprint_matches(ma, mb):
        print(f"{run_a} and {run_b}: computationally identical (same fingerprint)")
        return EXIT_OK

    diffs = diff_manifests(ma, mb)
    if not diffs:
        print(f"{run_a} and {run_b}: no differences found")
        return EXIT_OK

    if as_json:
        print(json.dumps(diffs, indent=2, sort_keys=True, default=str))
        return EXIT_OK

    print(f"Manifest diff: {run_a} vs {run_b}")
    print()
    for category in (
        "identity",
        "inputs",
        "configuration",
        "plugins",
        "graph",
        "states",
        "outputs",
    ):
        entries = diffs.get(category)
        if not entries:
            continue
        print(f"## {category}")
        for field, (va, vb) in entries.items():
            print(f"  {field}:")
            print(f"    {run_a}: {va}")
            print(f"    {run_b}: {vb}")
        print()
    return EXIT_OK


def main(argv: Sequence[str] | None = None, *, root: Path = DEFAULT_ROOT) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "status"
    try:
        app = bootstrap(root)
    except PhotoProofError as error:  # identity, settings, profiles, duplicate plugins
        print(f"configuration error:\n{error}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        if command == "version":
            print(app.identity.application.version)
            return EXIT_OK
        if command == "doctor":
            return cmd_doctor(app, args.json)
        if command == "repair":
            return cmd_repair(app, args.dry_run)
        if command == "config":
            return cmd_config(app, args.json)
        if command == "plugins":
            return cmd_plugins(app, args.profile, _override(args), args.json)
        if (code := _start(app)) is not None:
            return code
        if command == "preflight":
            print("preflight passed")
            return EXIT_OK
        if command == "status":
            return cmd_status(app)
        if command == "analyze":
            return cmd_analyze(app, args)
        if command == "housekeeping":
            return cmd_housekeeping(app, args.apply)
        if command == "manifest":
            return cmd_manifest(app, args.run_id, args.json, args.graph, args.timeline)
        if command == "manifest-diff":
            return cmd_manifest_diff(app, args.run_a, args.run_b, args.json)
        if command == "backup":
            return cmd_backup(app, args.target)
        if command == "restore":
            return cmd_restore(app, args.backup, args.dry_run)
        if command == "report":
            return cmd_report(app, args.run_id, args.format, args.output)
        if command == "new-plugin":
            return cmd_new_plugin(args)
        if command == "diagnose":
            return cmd_diagnose(app, args.run_id, args.plugin)
        return EXIT_USAGE
    except (InputRejectedError, PolicyError, StorageError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    except ConfigurationError as error:
        print(f"configuration error:\n{error}", file=sys.stderr)
        return EXIT_CONFIG
    except PhotoProofError as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    finally:
        app.stop()
