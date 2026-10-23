"""Protections for running on the public internet: rate limiting, same-origin checks and response headers.

Everything here is in memory, which is fine because the service runs as one process (see OPERATIONS.md).
Client IPs are never written to disk or to the logs.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from urllib.parse import urlsplit

from starlette.requests import Request

# Inline <script> and style="" attributes are used by the templates, so 'unsafe-inline' is needed for both.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "object-src 'none'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # Job URLs are capability links: never leak them to another site through the Referer header.
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class RateLimiter:
    """At most `limit` events per `window_s` seconds for each key (a sliding window)."""

    def __init__(self, limit: int, window_s: float = 3600):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record an event for `key` and return True, or return False if the key is over the limit."""
        if self.limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            hits = self._hits.setdefault(key, deque())
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]


def client_ip(request: Request) -> str:
    """The client's address. Behind a proxy, uvicorn sets it from X-Forwarded-For (see FORWARDED_ALLOW_IPS)."""
    return request.client.host if request.client else "unknown"


def same_origin(request: Request) -> bool:
    """False for a form POST that a browser sent from another site (CSRF).

    Browsers send Sec-Fetch-Site and Origin on every POST. Clients that send neither (curl, scripts) are not
    browsers acting on someone's behalf, so they are allowed.
    """
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return origin != "null" and urlsplit(origin).netloc == request.headers.get("host")
