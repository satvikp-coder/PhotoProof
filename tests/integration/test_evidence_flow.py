"""Evidence through the real CLI: storage, manifests, deduplication and health."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from photoproof.cli import main as cli_main
from photoproof.core.bootstrap import Application, bootstrap
from photoproof.core.database import store as database_store
from photoproof.core.health.checks import default_registry


def _invoke(root: Path, *args: str) -> int:
    return cli_main(list(args), root=root)


def objects(root: Path) -> list[Path]:
    return sorted(p for p in (root / "ARE" / "objects").glob("??/*") if p.is_file())


def analyze_json(root: Path, capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, object]:
    assert _invoke(root, "analyze", *args, "--json") == 0
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


def inspect(root: Path, use: Callable[[Application], object]) -> object:
    app = bootstrap(root, environ={})
    try:
        return use(app)
    finally:
        app.stop()


def test_analyze_stores_the_original_and_manifest_hash_matches(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = analyze_json(app_root, capsys, str(target_file))
    sha = hashlib.sha256(target_file.read_bytes()).hexdigest()
    assert [p.name for p in objects(app_root)] == [sha]
    assert objects(app_root)[0].read_bytes() == target_file.read_bytes()
    manifest = json.loads(Path(str(result["manifest_path"])).read_text())
    assert sha in json.dumps(manifest["inputs"])


def test_analyzing_twice_is_one_blob_and_two_linked_submissions(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = analyze_json(app_root, capsys, str(target_file))
    second = analyze_json(app_root, capsys, str(target_file))
    sha = hashlib.sha256(target_file.read_bytes()).hexdigest()
    assert len(objects(app_root)) == 1
    rows = inspect(app_root, lambda app: app.database().submissions(sha))
    assert [r["run_id"] for r in rows] == [first["run_id"], second["run_id"]]  # type: ignore[attr-defined]


def test_reference_is_stored_too(
    app_root: Path, target_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reference = tmp_path / "reference.bin"
    reference.write_bytes(b"different reference bytes")
    analyze_json(
        app_root, capsys, str(target_file), "--reference", str(reference), "--profile", "comparison"
    )
    assert len(objects(app_root)) == 2


def test_mistyped_profile_leaves_no_copy_behind(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _invoke(app_root, "analyze", str(target_file), "--profile", "typo") != 0
    assert objects(app_root) == []


def test_reference_under_a_single_image_profile_leaves_no_copy_behind(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _invoke(app_root, "analyze", str(target_file), "--reference", str(target_file))
    assert code != 0
    assert objects(app_root) == []


def test_empty_input_is_rejected_by_the_cli(
    app_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.touch()
    assert _invoke(app_root, "analyze", str(empty)) != 0
    assert "empty" in capsys.readouterr().err
    assert objects(app_root) == []


def test_tampered_original_is_critical_in_doctor(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    analyze_json(app_root, capsys, str(target_file))
    blob = objects(app_root)[0]
    blob.chmod(0o600)
    blob.write_bytes(b"forged")

    def check(app: Application) -> object:
        return default_registry().run(app, "storage.integrity")

    result = inspect(app_root, check)
    assert result.severity.value == "critical"  # type: ignore[attr-defined]
    assert _invoke(app_root, "doctor") != 0


def test_deleted_original_is_critical_in_doctor(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    analyze_json(app_root, capsys, str(target_file))
    blob = objects(app_root)[0]
    blob.chmod(0o600)
    blob.unlink()
    result = inspect(app_root, lambda app: default_registry().run(app, "storage.integrity"))
    assert result.severity.value == "critical"  # type: ignore[attr-defined]
    assert "missing" in " ".join(result.details)  # type: ignore[attr-defined]


def test_doctor_does_not_create_the_database(app_root: Path) -> None:
    def check(app: Application) -> bool:
        app.storage.ensure_layout()
        default_registry().run(app, "storage.integrity")
        return app.database_path.exists()

    assert inspect(app_root, check) is False


def test_repair_migrates_an_older_database_then_analyze_works(
    app_root: Path, target_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = app_root / "ARE" / "database" / "photoproof.db"
    database.parent.mkdir(parents=True)
    conn = sqlite3.connect(database)
    conn.executescript(database_store.MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    assert _invoke(app_root, "analyze", str(target_file)) != 0  # needs --repair first
    assert objects(app_root) == []
    assert _invoke(app_root, "repair") == 0
    assert (database.parent / "photoproof.db.v1.bak").is_file()
    assert _invoke(app_root, "analyze", str(target_file)) == 0
    assert len(objects(app_root)) == 1
