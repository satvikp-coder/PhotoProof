"""HTTP defense tests: no administrative surface, request forgery or error disclosure."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from helpers import update_settings

from photoproof.contracts.exceptions import ConfigurationError, PolicyError
from photoproof.core.bootstrap import Application
from photoproof.core.config.loader import load_identity, load_settings
from photoproof.core.config.policy import BlacklistOverride, OverrideOrigin, decide_all
from photoproof.core.config.validator import UISettings
from WebKit.app import create_app
from WebKit.security import COOKIE_NAME, SECURITY_HEADERS


@pytest.mark.parametrize(
    "path", ["/analyze", "/diagnostics", "/static/app.css", "/static/", "/missing"]
)
def test_headers_on_success_and_error_responses(
    app_root: Path, make_app: Callable[..., Application], path: str
) -> None:
    with TestClient(create_app(services=make_app()), base_url="http://127.0.0.1") as client:
        response = client.get(path)
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value
        assert "unsafe-inline" not in response.headers["content-security-policy"]


def test_csrf_blocks_forged_requests_before_future_state_changes(
    app_root: Path, make_app: Callable[..., Application]
) -> None:
    web = create_app(services=make_app())

    @web.post("/test-mutation")
    async def mutation() -> Response:
        return Response("accepted")

    with TestClient(web, base_url="http://127.0.0.1") as client:
        assert client.post("/test-mutation").status_code == 403
        page = client.get("/analyze")
        token = client.cookies[COOKIE_NAME]
        assert "HttpOnly" in page.headers["set-cookie"]
        assert "SameSite=strict" in page.headers["set-cookie"]
        assert client.post("/test-mutation", headers={"x-csrf-token": token}).status_code == 403
        headers = {"x-csrf-token": token, "origin": "http://127.0.0.1"}
        assert client.post("/test-mutation", headers=headers).status_code == 200
        with patch("WebKit.security.time.time", return_value=int(token.split(".")[1]) + 3601):
            assert client.post("/test-mutation", headers=headers).status_code == 403
        assert (
            client.post(
                "/test-mutation", headers={**headers, "origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert (
            client.post("/test-mutation", headers={**headers, "x-csrf-token": "forged"}).status_code
            == 403
        )
        client.cookies.set(COOKIE_NAME, "forged")
        assert (
            client.post("/test-mutation", headers={**headers, "x-csrf-token": "forged"}).status_code
            == 403
        )


@pytest.mark.parametrize("debug", [False, True])
def test_safe_errors_static_traversal_and_host_checks(
    app_root: Path, make_app: Callable[..., Application], debug: bool
) -> None:
    update_settings(app_root, development={"debug": debug})
    web = create_app(services=make_app())

    @web.get("/test-failure")
    async def failure() -> Response:
        raise RuntimeError("SECRET runtime directory /private/evidence")

    with TestClient(web, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        failure_response = client.get("/test-failure")
        assert failure_response.status_code == 500
        assert "SECRET" not in failure_response.text and "Traceback" not in failure_response.text
        assert failure_response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/static/%2e%2e/app.py").status_code == 404
        assert client.get("/static/").status_code == 404
        invalid_host = client.get("/about", headers={"host": "evil.test"})
        assert invalid_host.status_code == 400
        assert "Content-Security-Policy" in invalid_host.headers
        log = (app_root / "ARE/logs/photoproof.log").read_text(encoding="utf-8")
        assert '"logger": "photoproof.web"' in log and "SECRET runtime directory" in log
        # Untrusted forwarded headers cannot change generated links or CSRF origin.
        assert (
            "evil.test" not in client.get("/about", headers={"x-forwarded-host": "evil.test"}).text
        )


@pytest.mark.parametrize(
    "path", ["/ignore-blacklist", "/admin", "/repair", "/config", "/docs", "/openapi.json"]
)
def test_no_administrative_routes(
    app_root: Path, make_app: Callable[..., Application], path: str
) -> None:
    with TestClient(create_app(services=make_app()), base_url="http://127.0.0.1") as client:
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 403


@pytest.mark.parametrize(
    "host", ["", "http://127.0.0.1", "localhost:8501", "example.com", "::1%eth0"]
)
def test_invalid_binding_host(host: str) -> None:
    with pytest.raises(ValueError):
        UISettings(host=host)


@pytest.mark.parametrize("port", [0, 65536, -1, True, "8501", 8501.5])
def test_invalid_binding_port(port: object) -> None:
    with pytest.raises(ValueError):
        UISettings(port=port)  # type: ignore[arg-type]


def test_production_still_rejects_network_binding(app_root: Path) -> None:
    update_settings(
        app_root,
        environment={"mode": "production"},
        security={"allow_blacklist_bypass": False},
        ui={"host": "192.0.2.1"},
    )
    with pytest.raises(ConfigurationError, match="loopback"):
        load_settings(app_root, load_identity(app_root), environ={})


def test_web_boundary_rejects_even_a_cli_override_token(app_root: Path) -> None:
    settings = load_settings(app_root, load_identity(app_root), environ={})
    with pytest.raises(PolicyError, match="web"):
        decide_all(
            [],
            settings=settings,
            flags=None,
            profile=None,  # type: ignore[arg-type]
            override=BlacklistOverride(OverrideOrigin.LOCAL_CLI),
            origin=OverrideOrigin.WEB,
        )


def test_requests_explicitly_use_web_policy_origin(
    app_root: Path, make_app: Callable[..., Application]
) -> None:
    from WebKit.components.status import decide_all as real_decide_all

    with (
        TestClient(create_app(services=make_app()), base_url="http://127.0.0.1") as client,
        patch("WebKit.components.status.decide_all", wraps=real_decide_all) as decide,
    ):
        assert client.get("/diagnostics").status_code == 200
        assert decide.call_args.kwargs["origin"] is OverrideOrigin.WEB
        assert decide.call_args.kwargs["override"] is None


@pytest.mark.parametrize("mode", ["development", "production"])
@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1", "::", "2001:db8::1"])  # noqa: S104 - rejection only
def test_network_binding_rejected_in_every_mode(app_root: Path, mode: str, host: str) -> None:
    update_settings(
        app_root,
        environment={"mode": mode},
        security={"allow_blacklist_bypass": False},
        ui={"host": host},
    )
    with pytest.raises(ConfigurationError, match="loopback"):
        load_settings(app_root, load_identity(app_root), environ={})


def test_production_accepts_the_same_loopback_range(app_root: Path) -> None:
    update_settings(
        app_root,
        environment={"mode": "production"},
        security={"allow_blacklist_bypass": False},
        ui={"host": "127.0.0.2"},
    )
    assert load_settings(app_root, load_identity(app_root), environ={}).ui.host == "127.0.0.2"


def test_missing_policy_origin_is_not_trusted(app_root: Path) -> None:
    settings = load_settings(app_root, load_identity(app_root), environ={})
    with pytest.raises(PolicyError, match="explicit local CLI"):
        decide_all(
            [],
            settings=settings,
            flags=None,
            profile=None,  # type: ignore[arg-type]
            override=BlacklistOverride(OverrideOrigin.LOCAL_CLI),
        )


def test_csrf_token_refreshes_before_expiry(make_app: Callable[..., Application]) -> None:
    web = create_app(services=make_app())

    @web.post("/test-refresh")
    async def refresh() -> Response:
        return Response("accepted")

    with TestClient(web, base_url="http://127.0.0.1") as client:
        client.get("/about")
        old = client.cookies[COOKIE_NAME]
        with patch("WebKit.security.time.time", return_value=int(old.split(".")[1]) + 1801):
            page = client.get("/about")
            new = client.cookies[COOKIE_NAME]
            assert new != old and new in page.text
            assert "set-cookie" in page.headers
            assert (
                client.post(
                    "/test-refresh", headers={"origin": "http://127.0.0.1", "x-csrf-token": new}
                ).status_code
                == 200
            )
