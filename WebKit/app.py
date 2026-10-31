"""Application factory and server entry point for the read-only web foundation."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from photoproof.contracts.exceptions import ConfigurationError
from photoproof.core.bootstrap import Application
from WebKit.components.status import StatusCache
from WebKit.security import SECURITY_HEADERS, WebSecurityMiddleware, is_loopback

_ASSETS = Path(__file__).resolve().parent
_LOG = logging.getLogger("photoproof.web")
NAVIGATION = (
    ("analyze", "Analyze"),
    ("compare", "Compare"),
    ("history", "History"),
    ("diagnostics", "Diagnostics"),
    ("about", "About"),
)
_COPY = {
    "analyze": ("Your investigation starts here.", "Analysis workspace"),
    "compare": ("A second perspective.", "Comparison workspace"),
    "history": ("A record you can return to.", "Analysis history"),
    "diagnostics": ("Know your environment.", "System diagnostics"),
    "about": ("Built for careful examination.", "About this application"),
}


def create_app(*, services: Application) -> FastAPI:
    """Create routes around already-started injected core services; the caller owns them."""
    if not services.started:
        raise ConfigurationError(
            "WebKit requires started core services; run preflight and start first"
        )
    templates = Jinja2Templates(directory=str(_ASSETS))
    diagnostics = StatusCache()

    @asynccontextmanager
    async def lifespan(web: FastAPI) -> AsyncIterator[None]:
        web.state.services = services
        yield

    # Debug tracebacks remain disabled even in development: errors may contain evidence paths.
    web = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, debug=False)
    host = services.settings.ui.host
    web.add_middleware(WebSecurityMiddleware, host=host)
    web.mount(
        "/static", StaticFiles(directory=_ASSETS / "static", follow_symlink=False), name="static"
    )

    def render(
        request: Request,
        page: str,
        *,
        status: int = 200,
        extra: dict[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HTMLResponse:
        core: Application = request.app.state.services
        context: dict[str, object] = {
            "identity": core.identity.application,
            "navigation": NAVIGATION,
            "page": page,
            "csrf_token": getattr(request.state, "csrf_token", ""),
            "title": _COPY.get(page, ("Page unavailable.", "Request error"))[0],
            "eyebrow": _COPY.get(page, ("", "Request error"))[1],
        }
        if extra:
            context.update(extra)
        return templates.TemplateResponse(
            request=request,
            name=f"pages/{page}.html",
            context=context,
            status_code=status,
            headers={**(headers or {}), **SECURITY_HEADERS},
        )

    @web.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def index(request: Request) -> RedirectResponse:
        return RedirectResponse(request.url_for("analyze"), status_code=303)

    def endpoint(page: str) -> Callable[[Request], Awaitable[HTMLResponse]]:
        async def view(request: Request) -> HTMLResponse:
            extra = await diagnostics.get(services) if page == "diagnostics" else None
            return render(request, page, extra=extra)

        return view

    for page, _label in NAVIGATION:
        web.add_api_route(f"/{page}", endpoint(page), methods=["GET", "HEAD"], name=page)

    @web.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> HTMLResponse:
        return render(request, "error", status=error.status_code, headers=error.headers)

    @web.exception_handler(Exception)
    async def server_error(request: Request, error: Exception) -> HTMLResponse:
        _LOG.error("Web request failed: %s", type(error).__name__, exc_info=error)
        return render(request, "error", status=500)

    return web


def serve(services: Application) -> None:
    """Run one local ASGI process using validated binding settings, without reload."""
    if not is_loopback(services.settings.ui.host):
        raise ConfigurationError("ui.host must remain loopback until authentication exists")
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(services=services),
            host=services.settings.ui.host,
            port=services.settings.ui.port,
            proxy_headers=False,
            server_header=False,
            access_log=False,
        )
    )
    try:
        server.run()
    except SystemExit as error:
        if error.code not in {None, 0}:
            raise ConfigurationError(
                "cannot start web server; check ui.host/ui.port and local logs"
            ) from None
        raise
    if not server.started:
        raise ConfigurationError("cannot bind web server; change ui.port in settings.yaml")
