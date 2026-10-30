"""Application factory and server entry point for the read-only web foundation."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from photoproof.core.bootstrap import DEFAULT_ROOT, Application, bootstrap
from photoproof.core.config.loader import load_identity, load_settings
from photoproof.core.health.checks import default_registry
from photoproof.core.health.repair import preflight, publish_findings
from WebKit.components.status import status_context
from WebKit.security import SECURITY_HEADERS, WebSecurityMiddleware, is_loopback

_ASSETS = Path(__file__).resolve().parent
_LOG = logging.getLogger(__name__)
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


def create_app(*, root: Path = DEFAULT_ROOT, services: Application | None = None) -> FastAPI:
    """Create an app; own services are bootstrapped and closed on the ASGI thread.

    Injected services must be used on their SQLite-owning thread (the CLI's Uvicorn
    loop). No background workers or request thread pools touch the shared database.
    """
    templates = Jinja2Templates(directory=str(_ASSETS))

    @asynccontextmanager
    async def lifespan(web: FastAPI) -> AsyncIterator[None]:
        core = services if services is not None else bootstrap(root)
        web.state.services = core
        started_here = not core.started
        try:
            if not core.started:
                report = preflight(core, default_registry(deep=False))
                if not report.passed:
                    raise RuntimeError("Web startup health checks failed. Run the local doctor.")
                core.start()
                publish_findings(core, report.results)
            yield
        finally:
            if services is None or started_here:
                core.stop()

    # Debug tracebacks remain disabled even in development: errors may contain evidence paths.
    web = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, debug=False)
    host = services.settings.ui.host if services else "127.0.0.1"
    if services is None:
        # Configuration reads do not create runtime state or initialize SQLite.
        host = load_settings(root, load_identity(root)).ui.host
    web.add_middleware(WebSecurityMiddleware, host=host)
    web.mount(
        "/static", StaticFiles(directory=_ASSETS / "static", follow_symlink=False), name="static"
    )

    def render(request: Request, page: str, *, status: int = 200) -> HTMLResponse:
        core: Application = request.app.state.services
        context: dict[str, object] = {
            "identity": core.identity.application,
            "navigation": NAVIGATION,
            "page": page,
            "network_warning": not is_loopback(core.settings.ui.host),
            "csrf_token": getattr(request.state, "csrf_token", ""),
            "title": _COPY.get(page, ("Page unavailable.", "Request error"))[0],
            "eyebrow": _COPY.get(page, ("", "Request error"))[1],
        }
        if page == "diagnostics":
            context.update(status_context(core))
        return templates.TemplateResponse(
            request=request,
            name=f"pages/{page}.html",
            context=context,
            status_code=status,
            headers=SECURITY_HEADERS,
        )

    @web.get("/", include_in_schema=False)
    async def index(request: Request) -> RedirectResponse:
        return RedirectResponse(request.url_for("analyze"), status_code=303)

    def endpoint(page: str) -> Callable[[Request], Awaitable[HTMLResponse]]:
        async def view(request: Request) -> HTMLResponse:
            return render(request, page)

        return view

    for page, _label in NAVIGATION:
        web.add_api_route(f"/{page}", endpoint(page), methods=["GET"], name=page)

    @web.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> HTMLResponse:
        return render(request, "error", status=error.status_code)

    @web.exception_handler(Exception)
    async def server_error(request: Request, error: Exception) -> HTMLResponse:
        _LOG.error("Web request failed: %s", type(error).__name__, exc_info=error)
        return render(request, "error", status=500)

    return web


def serve(services: Application) -> None:
    """Run one local ASGI process using validated binding settings, without reload."""
    if not is_loopback(services.settings.ui.host):
        _LOG.warning(
            "Web interface has no authentication; non-loopback binding exposes it to others"
        )
    uvicorn.run(
        create_app(services=services),
        host=services.settings.ui.host,
        port=services.settings.ui.port,
        proxy_headers=False,
        server_header=False,
        access_log=False,
    )
