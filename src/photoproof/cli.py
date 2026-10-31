"""Command-line interface. ``python main.py <command>`` forwards here.

Exit codes: 0 success · 1 runtime error · 2 usage error · 4 configuration/identity
error · 6 health/preflight failure · 7 analysis did not complete (BLOCKED, ABORTED,
INCOMPLETE).
"""

import argparse
import json
import sys
from collections.abc import Sequence
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
from photoproof.core.health.checks import CheckResult, default_registry
from photoproof.core.health.repair import preflight, publish_findings, run_repairs
from photoproof.core.services.housekeeping import Housekeeping
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
    housekeeping = sub.add_parser("housekeeping", help="plan runtime cleanup; dry run by default")
    housekeeping.add_argument(
        "--apply", action="store_true", help="explicitly authorize eligible deletions"
    )
    manifest = sub.add_parser("manifest", help="show an execution manifest")
    manifest.add_argument("run_id")
    manifest.add_argument("--json", action="store_true")
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


def cmd_manifest(app: Application, run_id: str, as_json: bool) -> int:
    path = app.storage.path("manifests", f"{run_id}.json")
    manifest = app.storage.read_json("manifests", f"{run_id}.json")
    run = app.database().get_run(run_id)
    recorded = run.get("manifest_sha256") if run else None
    if recorded is None:
        print(
            "warning: no database record of this manifest's hash; cannot verify it", file=sys.stderr
        )
    elif sha256_file(path) != recorded:
        print(
            f"error: {path} does not match the SHA-256 recorded for run {run_id}; "
            "the manifest was modified after the run",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if as_json or not isinstance(manifest, dict):
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"run {manifest['run_id']}  status {manifest['status']}  profile {manifest['profile']}")
    print(f"  application   {manifest['application']}")
    print(f"  created       {manifest['created_at']}")
    print(f"  pipeline      {manifest['pipeline_fingerprint']}")
    print(f"  fingerprint   {manifest['computational_fingerprint']}")
    print(f"  cache         {manifest['cache']}")
    print(f"  override      {manifest['blacklist_override']}")
    print("  order         " + (" → ".join(manifest["execution_order"]) or "(none)"))
    for pid, ex in manifest["executions"].items():
        print(f"    {ex['state']:<15} {pid} {ex.get('failure_category') or ''}")
    for reason in manifest["blocked_reasons"]:
        print(f"  blocked: {reason}")
    return EXIT_OK


def cmd_housekeeping(app: Application, *, apply: bool) -> int:
    service = Housekeeping(app)
    plan = service.plan()
    print("Housekeeping: " + ("APPLY explicitly authorized" if apply else "DRY RUN; no changes"))
    print("Protected: original evidence, retained references, audit trails, backups, unknown files")
    for item in plan.candidates:
        print(
            f"  {item.data_class} {item.sha256}: {item.size} bytes; "
            f"{item.age_days:.2f} days; {item.reason}"
        )
    for finding in plan.blocked:
        print(f"  blocked: {finding}")
    print(f"Projected reclaimed: {plan.reclaimed_bytes} bytes")
    if apply:
        removed = service.apply(plan, authorized=True)
        print(f"Deleted {len(removed)} approved files")
    return EXIT_ERROR if plan.blocked else EXIT_OK


def main(argv: Sequence[str] | None = None, *, root: Path = DEFAULT_ROOT) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "status"
    try:
        app = bootstrap(root)
    except PhotoProofError as error:  # identity, settings, profiles, duplicate plugins
        print(f"configuration error:\n{error}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        if command == "housekeeping":
            return cmd_housekeeping(app, apply=args.apply)
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
        if command == "manifest":
            return cmd_manifest(app, args.run_id, args.json)
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
