"""Shared HTTP layer: one client, per-host rate limiting, robots.txt respect.

Every outbound request in the prototype goes through here so the politeness
rules are enforced in one place rather than at each call site.
"""

from __future__ import annotations

import time
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from .settings import settings

_cfg = settings()
TIMEOUT = float(_cfg["request_timeout"])
THROTTLE = float(_cfg["rate_limit_seconds"])
UA = _cfg["user_agent"]

_last_hit: dict[str, float] = {}
_robots: dict[str, robotparser.RobotFileParser | None] = {}

_client = httpx.Client(
    timeout=TIMEOUT,
    follow_redirects=True,
    headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
)


class FetchError(RuntimeError):
    pass


# Transient transport errors retried once
_TRANSIENT = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)
RETRIES = 1
RETRY_BACKOFF_S = 0.5


def _throttle(url: str) -> None:
    host = urlparse(url).netloc.lower()
    wait = THROTTLE - (time.monotonic() - _last_hit.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.monotonic()


def get(url: str, *, timeout: float | None = None, check_robots: bool = True) -> httpx.Response:
    """GET with rate limiting, an optional robots.txt gate, and one retry.

    Only transient transport errors are retried; 4xx/5xx is returned as-is.
    """
    if check_robots and not robots_allowed(url):
        raise FetchError(f"disallowed by robots.txt: {url}")
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        _throttle(url)
        try:
            return _client.get(url, timeout=timeout or TIMEOUT)
        except _TRANSIENT as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF_S * (attempt + 1))
    raise FetchError(f"{type(last).__name__}: {last}") from last


def post(url: str, **kwargs) -> httpx.Response:
    """POST with rate limiting."""
    kwargs.pop("check_robots", None)
    timeout = kwargs.pop("timeout", TIMEOUT)
    _throttle(url)
    try:
        return _client.post(url, timeout=timeout, **kwargs)
    except httpx.HTTPError as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc


def robots_allowed(url: str) -> bool:
    """Cached robots.txt lookup. Absent or unreadable file => allowed."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in _robots:
        parser = robotparser.RobotFileParser()
        parser.set_url(f"{origin}/robots.txt")
        try:
            _throttle(f"{origin}/robots.txt")
            parser.read()
            _robots[origin] = parser
        except Exception:
            _robots[origin] = None
    parser = _robots[origin]
    if parser is None:
        return True
    return parser.can_fetch(UA, url)
