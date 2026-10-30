# WebKit

The server-rendered web foundation uses FastAPI, Jinja2 and local CSS. Run
`python main.py web`, then open `http://127.0.0.1:8501`. Identity, version and release
channel come from `app.toml`; the binding comes from validated `settings.yaml` `ui.*`.
See [ADR-011](../docs/architecture/decisions.md#adr-011-server-rendered-webkit-foundation).

Analyze, Compare and History are explicitly labelled placeholders. Diagnostics shows
real quick health checks and installed plugin policy; About explains limitations.
No uploads, analysis, comparisons, history browsing or forensic verdicts exist in this
interface yet. The shell works without JavaScript and follows the system light/dark
appearance. No external font, script or stylesheet requests are made.

## Structure and extension points

- `app.py`: `create_app(root=..., services=...)`, lifespan, routes and `serve()`.
- `pages/`: individual page templates, including safe errors and honest placeholders.
- `components/`: reusable layout/placeholder templates and redacted status projections.
- `static/`: packaged local stylesheet; no directory listing or external symlink access.
- `security.py`: CSP, other HTTP headers, host validation and signed CSRF tokens.

The factory bootstraps and owns core services on its ASGI thread. The CLI can inject
already-started services on that same thread. SQLite is thread-bound: use async
endpoints for these existing synchronous service calls; do not hand the shared
database to a worker thread. One Uvicorn process runs with no reload, proxy-header
trust or access-query logging. Quick diagnostic checks are synchronous and may block
the event loop briefly; long analysis jobs belong in the later job-management issue.

Issues #39–#45 can replace page bodies and add routers without replacing this layout.
Use core controllers, registries and contracts, never algorithms in WebKit. Every
request reaching `decide_all` passes `origin=OverrideOrigin.WEB`, `override=None`.
There are no repair, config-dump or blacklist-bypass routes. The CLI also rejects
`--ignore-blacklist web` before starting services.

## Security contract

Loopback is the default. Non-loopback IPs must be configured explicitly and show an
unauthenticated-network warning both in the UI and server log. Production still rejects
non-loopback binding. There is no authentication: never expose this to an untrusted
network. Host checks reject arbitrary DNS names (including on wildcard binds), and
forwarded headers cannot change the trusted origin.

All responses, including errors and static files, receive CSP, `nosniff`, no-referrer,
frame denial, a restrictive permissions policy and `Cache-Control: no-store`. Error
pages never show exception details, even if development debugging is enabled. Health
messages/details and unavailable-plugin error reasons are not sent to the browser;
use the local doctor for details. Jinja autoescapes text.

No state-changing forms ship in this issue. A defensive CSRF boundary already rejects
unsafe methods unless a same-origin `Origin` and matching signed cookie/header token
are present. The readable token is in `<meta name="csrf-token">`; future HTMX/forms
must submit it in `X-CSRF-Token`. The cookie is HttpOnly, SameSite=Strict, Secure on
HTTPS, signed with a per-process random key and expires after one hour. Cross-origin,
missing, expired and forged tokens must be rejected before routing. Changing forms
must not perform mutations through GET. Authentication, upload-size limits, streaming
ingestion and authorization must be designed with those later features, not implied
by CSRF. HTMX is deliberately deferred until an interaction needs it.

## Validation and accessibility

`pytest tests/integration/test_web.py tests/security/test_web_boundary.py` tests the
application in process against temporary roots. Tests never use real runtime data.
`mypy` covers WebKit and `bandit -c pyproject.toml -r src WebKit main.py` checks it.
The optional browser smoke test and keyboard checklist are in
[the accessibility guide](../docs/testing/web-accessibility.md).

Dependencies are hash-locked using CONTRIBUTING.md's uv commands. FastAPI (MIT)
provides routes/ASGI integration; Jinja2 (BSD-3-Clause) provides autoescaped SSR; Uvicorn
(BSD-3-Clause), without native-speed extras, serves HTTP. Dev-only httpx2
(BSD-3-Clause) is the current Starlette TestClient backend. Pure Python dependencies
and existing wheel-based native dependencies preserve the supported OS/Python matrix.
