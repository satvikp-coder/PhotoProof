"""Read-only shell and actual service integration, isolated from developer state."""

import socket
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
def test_pages_render_identity_navigation_and_limitations(
    app_root: Path, make_app: Callable[..., Application], page: str
) -> None:
    with TestClient(create_app(services=make_app()), base_url="http://127.0.0.1") as client:
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


def test_identity_comes_from_configuration(
    app_root: Path, make_app: Callable[..., Application]
) -> None:
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
        TestClient(create_app(services=make_app()), base_url="http://127.0.0.1") as client,
    ):
        response = client.get("/about")
        assert "PHOTOPROOF" in response.text and "Preview" in response.text
        assert f"Version {configured_version}" in response.text


def test_diagnostics_uses_real_policy_and_redacts_details(
    app_root: Path,
    make_app: Callable[..., Application],
) -> None:
    update_settings(app_root, plugins={"blacklist": ["demo.source"]})
    core = make_app()
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


def test_unavailable_health_is_not_success(
    app_root: Path, make_app: Callable[..., Application]
) -> None:
    registry = HealthRegistry()
    registry.register("test", lambda _app: CheckResult("test", Severity.CRITICAL, "secret/path"))
    with TestClient(create_app(services=make_app()), base_url="http://127.0.0.1") as client:
        with (
            patch("WebKit.components.status.default_registry", return_value=registry),
            patch("WebKit.components.status.monotonic", return_value=1),
        ):
            response = client.get("/diagnostics")
        assert "Attention required" in response.text and "Critical" in response.text
        assert "secret/path" not in response.text and "Checks passed" not in response.text
        with (
            patch("WebKit.components.status.default_registry", return_value=HealthRegistry()),
            patch("WebKit.components.status.monotonic", return_value=32),
        ):
            assert "Health information is unavailable" in client.get("/diagnostics").text


def test_default_loopback_and_explicit_local_binding(
    app_root: Path, make_app: Callable[..., Application]
) -> None:
    settings = load_settings(app_root, load_identity(app_root), environ={})
    assert (settings.ui.host, settings.ui.port) == ("127.0.0.1", 8501)
    update_settings(app_root, ui={"host": "127.0.0.2", "port": 9876})
    with TestClient(create_app(services=make_app()), base_url="http://127.0.0.2") as client:
        assert client.get("/analyze").status_code == 200


def test_cli_web_uses_configured_server_and_rejects_override(app_root: Path) -> None:
    update_settings(app_root, ui={"host": "127.0.0.1", "port": 9876})
    with patch("WebKit.app.uvicorn.Server") as server:
        server.return_value.started = True
        assert main(["web"], root=app_root) == 0
        assert server.call_args.args[0].host == "127.0.0.1"
        assert server.call_args.args[0].port == 9876
        assert server.call_args.args[0].proxy_headers is False
    with patch("WebKit.app.serve") as web:
        assert main(["--ignore-blacklist", "web"], root=app_root) == 1
        web.assert_not_called()


def test_explicit_network_bind_is_rejected(make_app: Callable[..., Application]) -> None:
    from photoproof.contracts.exceptions import ConfigurationError

    core = make_app()
    core.settings = core.settings.model_copy(
        update={"ui": core.settings.ui.model_copy(update={"host": "192.0.2.1"})}
    )
    with (
        patch("WebKit.app.uvicorn.Server") as server,
        pytest.raises(ConfigurationError, match="loopback"),
    ):
        serve(core)
    server.assert_not_called()


def test_head_and_method_error_preserve_http_headers(make_app: Callable[..., Application]) -> None:
    with TestClient(create_app(services=make_app()), base_url="http://127.0.0.1") as client:
        assert client.head("/about").status_code == 200
        assert client.head("/about").content == b""
        error = client.options("/about")
        assert error.status_code == 405 and {"GET", "HEAD"} <= set(
            error.headers["allow"].split(", ")
        )
        assert error.headers["x-content-type-options"] == "nosniff"


def test_diagnostics_cache_does_not_block_other_pages(make_app: Callable[..., Application]) -> None:
    core = make_app()
    ready, release = threading.Event(), threading.Event()
    registry = HealthRegistry()

    def slow(_app: Application) -> CheckResult:
        ready.set()
        assert release.wait(10), "test worker was not released"
        return CheckResult("slow", Severity.OK, "safe")

    registry.register("slow", slow)
    with (
        TestClient(create_app(services=core), base_url="http://127.0.0.1") as client,
        patch("WebKit.components.status.default_registry", return_value=registry) as collect,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(client.get, "/diagnostics")
        try:
            assert ready.wait(5)
            assert pool.submit(client.get, "/about").result(timeout=3).status_code == 200
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
        assert client.get("/diagnostics").status_code == 200
        assert collect.call_count == 1
    assert core.database().integrity_problems() == []  # original connection remains usable


def test_busy_port_is_configuration_error(
    app_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        update_settings(app_root, ui={"port": occupied.getsockname()[1]})
        assert main(["web"], root=app_root) == 4
    assert "cannot start web server" in capsys.readouterr().err


def test_factory_requires_started_services(make_app: Callable[..., Application]) -> None:
    from photoproof.contracts.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="started core"):
        create_app(services=make_app(start=False))
