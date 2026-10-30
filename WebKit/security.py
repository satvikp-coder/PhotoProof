"""HTTP boundary: signed CSRF tokens, host checks and security headers."""

import hashlib
import hmac
import secrets
import time
from ipaddress import ip_address

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}
COOKIE_NAME = "photoproof_csrf"
TOKEN_LIFETIME = 3600


def is_loopback(host: str) -> bool:
    """Only literal loopback addresses and localhost count as local bindings."""
    return host == "localhost" or ip_address(host).is_loopback


class WebSecurityMiddleware(BaseHTTPMiddleware):
    """Reject unsafe requests before routing; never trust forwarded host headers."""

    def __init__(self, app: ASGIApp, *, host: str) -> None:
        super().__init__(app)
        self.host = host
        self.secret = secrets.token_bytes(32)

    def _sign(self, value: str) -> str:
        return hmac.new(self.secret, value.encode("ascii"), hashlib.sha256).hexdigest()

    def _new_token(self) -> str:
        value = f"{secrets.token_hex(32)}.{int(time.time())}"
        return f"{value}.{self._sign(value)}"

    def _valid_token(self, token: str) -> bool:
        if not token.isascii() or len(token) > 160:
            return False
        try:
            nonce, timestamp, signature = token.split(".")
            age = time.time() - int(timestamp)
            return (
                len(nonce) == 64
                and 0 <= age <= TOKEN_LIFETIME
                and hmac.compare_digest(signature, self._sign(f"{nonce}.{timestamp}"))
            )
        except (ValueError, UnicodeError):
            return False

    def _valid_host(self, request: Request) -> bool:
        try:
            host = request.url.hostname
            if host in {"localhost", "127.0.0.1", "::1", self.host}:
                return True
            # Wildcard binds accept literal addresses, never arbitrary DNS hostnames.
            return ip_address(self.host).is_unspecified and ip_address(host or "").version in {4, 6}
        except ValueError:
            return False

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self._valid_host(request):
            return HTMLResponse("Invalid request host.", status_code=400, headers=SECURITY_HEADERS)
        cookie = request.cookies.get(COOKIE_NAME, "")
        valid_cookie = self._valid_token(cookie)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("x-csrf-token", "")
            origin = request.headers.get("origin", "")
            expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if not (
                valid_cookie
                and supplied.isascii()
                and hmac.compare_digest(cookie, supplied)
                and origin == expected_origin
            ):
                return HTMLResponse(
                    "Request verification failed. Reload the page and try again.",
                    status_code=403,
                    headers=SECURITY_HEADERS,
                )
        request.state.csrf_token = cookie if valid_cookie else self._new_token()
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if not valid_cookie:
            response.set_cookie(
                COOKIE_NAME,
                request.state.csrf_token,
                max_age=TOKEN_LIFETIME,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
            )
        return response
