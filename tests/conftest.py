"""Shared fixtures. Every test runs against a temporary copy of the configuration,
so no test touches the developer's real .venv/, ARE/ or settings."""

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from helpers import REPO

from photoproof.core.bootstrap import Application, bootstrap


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
