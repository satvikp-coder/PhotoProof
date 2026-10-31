"""Stored-run reports: verify the exact source bytes before parsing, then publish atomically."""

import json
import re
import stat
from pathlib import Path
from typing import Literal

from photoproof.contracts.exceptions import DatabaseError, ReportError, StorageError
from photoproof.core.database.store import Database
from photoproof.core.reporting.build import build_report, canonical_report_json
from photoproof.core.reporting.render import render_html
from photoproof.core.security.paths import safe_join
from photoproof.core.storage.store import RuntimeStorage, atomic_write, sha256_bytes


def validate_run_id(run_id: str) -> None:
    """Accept only controller-generated UUID hex IDs, never filenames or paths."""
    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ReportError("invalid run ID; use the 32-character lowercase ID printed by analyze")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def generate_report(
    storage: RuntimeStorage, database: Database, run_id: str, *, format: Literal["html", "json"]
) -> Path:
    """Verify stored manifest/blob hashes and persist one complete derived format.

    No inference is based on filesystem contents: missing/corrupt blobs cause refusal,
    leaving existing reports and original sources untouched. Each format is independently atomic.
    """
    validate_run_id(run_id)
    if format not in {"html", "json"}:
        raise ReportError("unsupported report format; select html or json")
    try:
        run = database.get_run(run_id)
        recorded = run.get("manifest_sha256") if run is not None else None
        if not isinstance(recorded, str) or re.fullmatch(r"[0-9a-f]{64}", recorded) is None:
            raise ReportError("run has no recorded manifest hash; inspect the run before reporting")
        # Hash and parse the same byte snapshot, avoiding the CLI manifest command's separate reads.
        data = storage.path("manifests", f"{run_id}.json").read_bytes()
        if sha256_bytes(data) != recorded:
            raise ReportError(
                "manifest integrity verification failed; preserve the source and run doctor"
            )
        manifest: object = json.loads(data, object_pairs_hook=_unique_object)
        if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
            raise ReportError("manifest run identity is inconsistent; report not generated")
        report = build_report(manifest)
        if run is None or run.get("status") != report.status.value or not run.get("finished_at"):
            raise ReportError(
                "database and manifest lifecycle disagree; inspect the run before reporting"
            )
        for item in report.inputs:
            if (
                not storage.verify_blob("objects", item.sha256)
                or storage.blob_path("objects", item.sha256).stat().st_size != item.size
            ):
                raise ReportError(
                    "input evidence is missing or fails hash verification; report not generated"
                )
        for artifact in report.artifacts:
            if (
                not storage.verify_blob("artifacts", artifact.sha256)
                or storage.blob_path("artifacts", artifact.sha256).stat().st_size != artifact.size
            ):
                raise ReportError(
                    "artifact evidence is missing or fails hash verification; report not generated"
                )
        payload = (
            canonical_report_json(report)
            if format == "json"
            else render_html(report).encode("utf-8")
        )
        # Inspect lexical aliases before resolution; writes use safe_join exclusively.
        for parts in (("reports",), ("reports", run_id), ("reports", run_id, f"report.{format}")):
            lexical = storage.root.joinpath(*parts)
            if lexical.exists() or lexical.is_symlink():
                info = lexical.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ReportError(
                        "report destination contains a link; use a private report directory"
                    )
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ReportError(
                        "report destination is hardlinked; investigate before publication"
                    )
        reports = storage.path("reports")
        directory = safe_join(reports, run_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = safe_join(directory, f"report.{format}")
        atomic_write(target, payload)
        return target
    except ReportError:
        raise
    except (OSError, ValueError, StorageError, DatabaseError) as error:
        raise ReportError(
            "report source could not be read or output could not be stored; check "
            "runtime permissions and run doctor"
        ) from error
