"""Read-only shell and actual service integration, isolated from developer state."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from helpers import APP_VERSION, update_settings

from photoproof.cli import main
from photoproof.contracts.enums import Severity
from photoproof.core.bootstrap import Application
from photoproof.core.config.loader import load_identity, load_settings
from photoproof.core.health.checks import CheckResult, HealthRegistry
from photoproof.core.pipeline.registry import UnavailablePlugin
from photoproof.versioning.version import SemVer
from WebKit.app import NAVIGATION, create_app, serve


@pytest.mark.parametrize("page", [name for name, _label in NAVIGATION])
def test_pages_render_identity_navigation_and_limitations(app_root: Path, page: str) -> None:
    with TestClient(create_app(root=app_root), base_url="http://127.0.0.1") as client:
        response = client.get(f"/{page}")
        assert response.status_code == 200
        assert f"Version {APP_VERSION}" in response.text
        assert 'aria-label="Primary navigation"' in response.text
        assert 'class="skip-link" href="#main"' in response.text
        assert '<main id="main" tabindex="-1">' in response.text
        assert response.text.count('aria-current="page"') == 1
        assert 'lang="en"' in response.text and "<h1>" in response.text
        assert "Foundation stage" in response.text and "no forensic verdicts" in response.text
        assert "<script" not in response.text and "<form" not in response.text
        for name, label in NAVIGATION:
            assert f"/{name}" in response.text and label in response.text


def test_identity_comes_from_configuration(app_root: Path) -> None:
    path = app_root / "app.toml"
    text = path.read_text().replace(
        'release_channel = "development"', 'release_channel = "preview"'
    )
    # Same package identity with a different display capitalization; no hardcoded name.
    path.write_text(text.replace('name = "PhotoProof"', 'name = "PHOTOPROOF"'))
    original = SemVer.parse(APP_VERSION)
    configured_version = f"{original.major + 1}.0.0"
    path.write_text(
        path.read_text().replace(
            f'version = "{APP_VERSION}"', f'version = "{configured_version}"', 1
        )
    )
    with (
        patch(
            "photoproof.core.health.checks.distribution_version", return_value=configured_version
        ),
        TestClient(create_app(root=app_root), base_url="http://127.0.0.1") as client,
    ):
        response = client.get("/about")
        assert "PHOTOPROOF" in response.text and "Preview" in response.text
        assert f"Version {configured_version}" in response.text


def test_diagnostics_uses_real_policy_and_redacts_details(
    app_root: Path,
    make_app: Callable[..., Application],
) -> None:
    update_settings(app_root, plugins={"blacklist": ["demo.source"]})
    core = make_app(start=False)
    core.registry.unavailable.append(UnavailablePlugin("secret/path", "broken.plugin", "SECRET"))
    with TestClient(create_app(services=core), base_url="http://127.0.0.1") as client:
        response = client.get("/diagnostics?ignore-blacklist=true")
        assert response.status_code == 200
        assert "demo.source" in response.text
        assert "blacklist" in response.text and "Disabled" in response.text
        assert str(app_root) not in response.text
        assert "allow_blacklist_bypass" not in response.text
        assert "broken.plugin" in response.text
        assert "SECRET" not in response.text and "secret/path" not in response.text
        assert "Check" in response.text


def test_unavailable_health_is_not_success(app_root: Path) -> None:
    registry = HealthRegistry()
    registry.register("test", lambda _app: CheckResult("test", Severity.CRITICAL, "secret/path"))
    with TestClient(create_app(root=app_root), base_url="http://127.0.0.1") as client:
        with patch("WebKit.components.status.default_registry", return_value=registry):
            response = client.get("/diagnostics")
        assert "Attention required" in response.text and "Critical" in response.text
        assert "secret/path" not in response.text and "Checks passed" not in response.text
        with patch("WebKit.components.status.default_registry", return_value=HealthRegistry()):
            assert "Health information is unavailable" in client.get("/diagnostics").text


def test_default_loopback_and_explicit_network_warning(app_root: Path) -> None:
    settings = load_settings(app_root, load_identity(app_root), environ={})
    assert (settings.ui.host, settings.ui.port) == ("127.0.0.1", 8501)
    update_settings(app_root, ui={"host": "192.0.2.1", "port": 9876})
    with TestClient(create_app(root=app_root), base_url="http://127.0.0.1") as client:
        assert "Unauthenticated network access" in client.get("/analyze").text


def test_cli_web_uses_configured_server_and_rejects_override(app_root: Path) -> None:
    update_settings(app_root, ui={"host": "127.0.0.1", "port": 9876})
    with patch("WebKit.app.uvicorn.run") as run:
        assert main(["web"], root=app_root) == 0
        assert run.call_args.kwargs["host"] == "127.0.0.1"
        assert run.call_args.kwargs["port"] == 9876
        assert run.call_args.kwargs["proxy_headers"] is False
    with patch("WebKit.app.serve") as web:
        assert main(["--ignore-blacklist", "web"], root=app_root) == 1
        web.assert_not_called()


def test_explicit_network_bind_warns(make_app: Callable[..., Application]) -> None:
    core = make_app(start=False)
    core.settings = core.settings.model_copy(
        update={"ui": core.settings.ui.model_copy(update={"host": "192.0.2.1"})}
    )
    with patch("WebKit.app.uvicorn.run"), patch("WebKit.app._LOG.warning") as warning:
        serve(core)
    warning.assert_called_once()
