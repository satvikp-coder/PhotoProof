"""main.py tests. Everything runs in temporary directories with subprocess mocked."""

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from helpers import REPO

_spec = importlib.util.spec_from_file_location("photoproof_launcher", REPO / "main.py")
assert _spec and _spec.loader
launcher: ModuleType = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = launcher  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(launcher)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    target = tmp_path / "checkout"
    (target / "src" / "photoproof").mkdir(parents=True)
    (target / "src" / "photoproof" / "__init__.py").write_text("")
    for name in ("app.toml", "pyproject.toml", "requirements.lock"):
        shutil.copy(REPO / name, target / name)
    return target


@pytest.fixture
def identity(root: Path) -> dict[str, Any]:
    return launcher.load_identity(root)  # type: ignore[no-any-return]


def test_version(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert launcher.main(["--version"], root=root) == 0
    assert capsys.readouterr().out.strip() == "PhotoProof 0.1.0"


def test_identity_mismatch(root: Path) -> None:
    app_toml = root / "app.toml"
    app_toml.write_text(app_toml.read_text().replace('name = "PhotoProof"', 'name = "Other"'))
    with pytest.raises(launcher.LauncherError) as error:
        launcher.load_identity(root)
    assert error.value.code == launcher.EXIT_IDENTITY


def test_missing_package_directory(root: Path) -> None:
    shutil.rmtree(root / "src")
    with pytest.raises(launcher.LauncherError, match="src/photoproof"):
        launcher.load_identity(root)


@pytest.mark.parametrize("version", [(3, 10), (3, 14), (4, 0)])
def test_wrong_interpreter(identity: dict[str, Any], version: tuple[int, int]) -> None:
    with pytest.raises(launcher.LauncherError) as error:
        launcher.check_python(identity, version)
    assert error.value.code == launcher.EXIT_UNSUPPORTED


def test_supported_interpreter(identity: dict[str, Any]) -> None:
    assert launcher.check_python(identity, (3, 12)) == "3.12"


def test_platform_checks(identity: dict[str, Any]) -> None:
    assert launcher.check_platform(identity, "Linux", "aarch64") == ("Linux", "arm64")
    assert launcher.check_platform(identity, "Windows", "AMD64") == ("Windows", "x86_64")
    with pytest.raises(launcher.LauncherError, match="operating system"):
        launcher.check_platform(identity, "FreeBSD", "x86_64")
    with pytest.raises(launcher.LauncherError, match="architecture"):
        launcher.check_platform(identity, "Linux", "riscv64")


def test_hardware_unknown_memory_is_reported_not_invented(
    identity: dict[str, Any], root: Path
) -> None:
    report = launcher.Report()
    launcher.check_hardware(identity, root, report, cpu_count=4, memory_mb=None, free_mb=5000)
    assert ("warn", "memory", "unknown on this platform") in report.rows


def test_hardware_hard_requirements(identity: dict[str, Any], root: Path) -> None:
    with pytest.raises(launcher.LauncherError, match="memory"):
        launcher.check_hardware(
            identity, root, launcher.Report(), cpu_count=4, memory_mb=10, free_mb=5000
        )
    with pytest.raises(launcher.LauncherError, match="disk"):
        launcher.check_hardware(
            identity, root, launcher.Report(), cpu_count=4, memory_mb=8000, free_mb=1
        )


def test_missing_lockfile(root: Path) -> None:
    (root / "requirements.lock").unlink()
    with pytest.raises(launcher.LauncherError) as error:
        launcher.verify_lockfile(root)
    assert error.value.code == launcher.EXIT_DEPS


def test_unhashed_lockfile(root: Path) -> None:
    (root / "requirements.lock").write_text("pydantic==2.9.0\n")
    with pytest.raises(launcher.LauncherError, match="not pinned with a hash"):
        launcher.verify_lockfile(root)


def test_fingerprint_detects_drift(root: Path) -> None:
    lock = launcher.verify_lockfile(root)
    base = launcher.environment_fingerprint(root, lock, "3.12", "0.1.0")
    assert launcher.environment_fingerprint(root, lock, "3.13", "0.1.0") != base
    assert launcher.environment_fingerprint(root, lock, "3.12", "0.1.1") != base
    assert launcher.environment_fingerprint(root, "x" * 64, "3.12", "0.1.0") != base


class FakeEnv:
    """Stands in for venv creation, pip and the venv interpreter."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, venv_python_version: str | None) -> None:
        self.created = False
        self.pip_calls: list[tuple[str, ...]] = []
        self.version = venv_python_version
        monkeypatch.setattr(launcher, "venv_version", lambda venv: self.version)
        monkeypatch.setattr(launcher, "create_venv", self._create)
        monkeypatch.setattr(launcher, "_pip", lambda python, *args: self.pip_calls.append(args))
        monkeypatch.setattr(launcher, "installed_version", lambda python, package: "0.1.0")

    def _create(self, venv: Path) -> None:
        venv.mkdir()
        self.created = True
        self.version = "3.12"


def test_missing_venv_is_created_and_installed(
    root: Path, identity: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = FakeEnv(monkeypatch, None)
    launcher.ensure_environment(
        root, identity, force=False, rebuild=False, report=launcher.Report()
    )
    assert env.created and len(env.pip_calls) == 2
    assert "--require-hashes" in env.pip_calls[0]


def test_unchanged_environment_is_not_reinstalled(
    root: Path, identity: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = FakeEnv(monkeypatch, None)
    launcher.ensure_environment(
        root, identity, force=False, rebuild=False, report=launcher.Report()
    )
    env.pip_calls.clear()
    launcher.ensure_environment(
        root, identity, force=False, rebuild=False, report=launcher.Report()
    )
    assert env.pip_calls == []
    launcher.ensure_environment(root, identity, force=True, rebuild=False, report=launcher.Report())
    assert len(env.pip_calls) == 2


def test_incompatible_venv_is_not_destroyed_without_repair(
    root: Path, identity: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / ".venv").mkdir()
    (root / ".venv" / "marker").write_text("keep")
    FakeEnv(monkeypatch, "3.9")
    with pytest.raises(launcher.LauncherError, match="--repair"):
        launcher.ensure_environment(
            root, identity, force=False, rebuild=False, report=launcher.Report()
        )
    assert (root / ".venv" / "marker").exists()


def test_incompatible_venv_rebuilt_on_repair(
    root: Path, identity: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / ".venv").mkdir()
    env = FakeEnv(monkeypatch, "3.9")
    launcher.ensure_environment(root, identity, force=False, rebuild=True, report=launcher.Report())
    assert env.created


def test_package_version_mismatch(
    root: Path, identity: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeEnv(monkeypatch, None)
    monkeypatch.setattr(launcher, "installed_version", lambda python, package: "0.0.9")
    with pytest.raises(launcher.LauncherError, match=r"not importable at version 0\.1\.0"):
        launcher.ensure_environment(
            root, identity, force=False, rebuild=False, report=launcher.Report()
        )


def _patch_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "check_python", lambda identity, version_info=None: "3.12")
    monkeypatch.setattr(launcher, "check_platform", lambda identity: ("Linux", "x86_64"))
    monkeypatch.setattr(launcher, "check_hardware", lambda *a, **k: None)


def test_preflight_failure_stops_launch(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_host(monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(launcher, "ensure_environment", lambda *a, **k: Path("python"))
    monkeypatch.setattr(launcher, "run_app", lambda python, args: calls.append(args) or 6)
    assert launcher.main([], root=root) == launcher.EXIT_PREFLIGHT
    assert calls == [["preflight"]]


def test_commands_and_bypass_are_forwarded(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_host(monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(launcher, "ensure_environment", lambda *a, **k: Path("python"))
    monkeypatch.setattr(launcher, "run_app", lambda python, args: calls.append(args) or 0)
    assert launcher.main(["--ignore-blacklist", "plugins", "--json"], root=root) == 0
    assert calls == [["preflight"], ["--ignore-blacklist", "plugins", "--json"]]


def test_repair_dry_run_changes_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_host(monkeypatch)
    monkeypatch.setattr(launcher, "venv_version", lambda venv: None)
    assert launcher.main(["--repair", "--dry-run"], root=root) == 0
    assert "rebuild .venv" in capsys.readouterr().out
    assert not (root / ".venv").exists()


def test_doctor_without_venv(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_host(monkeypatch)
    monkeypatch.setattr(launcher, "venv_version", lambda venv: None)
    assert launcher.main(["--doctor"], root=root) == launcher.EXIT_PREFLIGHT
    assert "missing or broken" in capsys.readouterr().out
    assert not (root / ".venv").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="UF_HIDDEN is macOS-specific")
def test_hidden_pth_flag_cleared(tmp_path: Path) -> None:
    import os
    import stat

    site = tmp_path / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    pth = site / "_photoproof.pth"
    pth.write_text("/src\n")
    os.chflags(pth, stat.UF_HIDDEN)
    assert launcher.unhide_pth_files(tmp_path) == ["_photoproof.pth"]
    assert not pth.stat().st_flags & stat.UF_HIDDEN
