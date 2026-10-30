import json
from collections.abc import Callable
from pathlib import Path

from helpers import make_blob_writable, plugin_config, update_settings

from photoproof.contracts.analysis import AnalysisRequest, AnalysisResult, InputDescriptor
from photoproof.contracts.enums import RunStatus
from photoproof.core.bootstrap import Application
from photoproof.core.controller import AnalysisController

Factory = Callable[..., Application]


def _run(app: Application, sha: str = "a" * 64, **fields: object) -> AnalysisResult:
    request = AnalysisRequest(target=InputDescriptor(role="target", sha256=sha, size=1), **fields)  # type: ignore[arg-type]
    return AnalysisController(app).analyze(request)


def _artifact(app: Application) -> Path:
    return next(app.storage.iter_blobs("artifacts"))


def test_hit_after_completed_run(make_app: Factory) -> None:
    app = make_app()
    first, second = _run(app), _run(app)
    assert not first.cache_hit and second.cache_hit and second.status is RunStatus.COMPLETED
    assert second.outputs == first.outputs
    manifest = json.loads(Path(second.manifest_path or "").read_text())
    assert manifest["cache"]["hit"] is True


def test_key_depends_on_more_than_image_hash(make_app: Factory, app_root: Path) -> None:
    app = make_app()
    _run(app)
    assert not _run(app, sha="b" * 64).cache_hit  # different input
    assert not _run(app, profile="research").cache_hit  # different profile
    plugin_config(app_root, "demo.configurable", behavior="ok")
    assert not _run(make_app()).cache_hit  # plugin configuration changed
    update_settings(app_root, scheduling={"random_seed": 7})
    assert not _run(make_app()).cache_hit  # effective settings changed


def test_bypass(make_app: Factory) -> None:
    app = make_app()
    _run(app)
    assert not _run(app, use_cache=False).cache_hit


def test_disabled_by_flag(make_app: Factory, app_root: Path) -> None:
    update_settings(app_root, features={"flags": {"cache.run_level": False}})
    app = make_app()
    _run(app)
    assert not _run(app).cache_hit


def test_failed_or_partial_runs_not_cached(make_app: Factory, app_root: Path) -> None:
    plugin_config(app_root, "demo.configurable", behavior="raise")
    app = make_app()
    assert _run(app).status is RunStatus.INCOMPLETE
    assert not _run(app).cache_hit
    assert list((app.storage.root / "cache" / "runs").glob("*.json")) == []


def test_missing_artifact_invalidates(make_app: Factory) -> None:
    app = make_app()
    _run(app)
    artifact = _artifact(app)
    make_blob_writable(artifact)
    artifact.unlink()
    result = _run(app)
    assert not result.cache_hit and result.status is RunStatus.COMPLETED  # recomputed


def test_corrupted_artifact_invalidates(make_app: Factory) -> None:
    app = make_app()
    _run(app)
    artifact = _artifact(app)
    make_blob_writable(artifact)
    artifact.write_bytes(b"tampered")
    # The recomputation cannot overwrite a corrupt blob silently, so the source plugin fails
    # and the run is not reported as a cached success.
    result = _run(app)
    assert not result.cache_hit


def test_corrupted_entry_invalidates(make_app: Factory) -> None:
    app = make_app()
    _run(app)
    entry = next((app.storage.root / "cache" / "runs").glob("*.json"))
    entry.write_text("{ truncated")
    assert not _run(app).cache_hit
