"""Shared fixtures. Every test runs against a temporary copy of the configuration,
so no test touches the developer's real .venv/, ARE/ or settings."""

import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from helpers import REPO, make_blob_writable

from photoproof.core.bootstrap import Application, bootstrap


@pytest.fixture(autouse=True)
def cleanup_private_windows_artifacts(tmp_path: Path) -> Iterator[None]:
    """After assertions, restore owner write rights only on this test's readonly files.

    Pytest cannot clear read-only attributes with chmod alone once a file DACL is
    read-only. Do not follow junctions/symlinks or touch the developer's runtime.
    """
    yield
    if os.name == "nt":
        for directory, subdirs, files in os.walk(tmp_path, followlinks=False):
            base = Path(directory)
            subdirs[:] = [
                name for name in subdirs if not (base / name).lstat().st_file_attributes & 0x400
            ]
            for name in files:
                path = base / name
                attributes = path.lstat().st_file_attributes
                if attributes & 0x1 and not attributes & 0x400:
                    make_blob_writable(path)


@pytest.fixture
def app_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("app.toml", "settings.yaml", "pyproject.toml", "requirements.lock"):
        shutil.copy(REPO / name, root / name)
    shutil.copytree(REPO / "configs", root / "configs")
    return root


@pytest.fixture
def make_app(app_root: Path) -> Iterator[Callable[..., Application]]:
    apps: list[Application] = []

    def factory(package: str = "mockplugins.demo", *, start: bool = True) -> Application:
        app = bootstrap(app_root, plugin_package=package, environ={})
        if start:
            app.storage.ensure_layout()
            app.start()
        apps.append(app)
        return app

    yield factory
    for app in apps:
        app.stop()


@pytest.fixture
def target_file(tmp_path: Path) -> Path:
    path = tmp_path / "target.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0 a JPEG signature is enough for ingestion")
    return path


@pytest.fixture
def settings(app_root: Path):  # type: ignore[no-untyped-def]
    from photoproof.core.config.loader import load_identity, load_settings

    return load_settings(app_root, load_identity(app_root), environ={})
