"""
web_terminal_guard.py - who may open the terminal websocket.

Browsers do not apply the same-origin rule to websockets: any web page can
connect to ws://localhost:<port>. But the browser always sends the page's
Origin header and a page cannot fake it, so we accept only our own page:

- Origin must be http(s)://<local host>:<our port>
- Host must be <local host>:<our port>. This blocks DNS rebinding, where an
  attacker's domain resolves to 127.0.0.1 and Origin and Host both use it.
"""

from typing import Mapping, Optional, Tuple
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
ANY_ADDRESS = frozenset({"", "0.0.0.0", "::"})


def allowed_hosts(config_host: str) -> frozenset:
    """Host names our own page can be served from: loopback, plus the
    configured host if it is a specific name (never 0.0.0.0)."""
    host = (config_host or "").strip().lower().strip("[]")
    if host in ANY_ADDRESS:
        return LOOPBACK_HOSTS
    return LOOPBACK_HOSTS | {host}


def _split(url: str, default_port: int) -> Tuple[Optional[str], Optional[int], str]:
    """(host, port, scheme) from a URL; host is None if it cannot be parsed.
    urlsplit handles IPv6 brackets: "[::1]:8090" -> ("::1", 8090)."""
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:          # e.g. port is not a number
        return None, None, ""
    return host, (port if port is not None else default_port), parts.scheme


def check_ws(headers: Mapping[str, str], config_host: str, port: int) -> Tuple[bool, str]:
    """Is this websocket handshake from our own page? Returns (ok, reason)."""
    hosts = allowed_hosts(config_host)

    origin = headers.get("origin")
    if not origin:
        return False, "missing origin"
    o_host, o_port, scheme = _split(origin, 80)
    if scheme == "https" and urlsplit(origin).port is None:
        o_port = 443
    if scheme not in ("http", "https"):
        return False, "bad origin"
    if o_host not in hosts or o_port != port:
        return False, "foreign origin"

    host_header = headers.get("host")
    if not host_header:
        return False, "missing host"
    h_host, h_port, _ = _split("//" + host_header, 80)
    if h_host not in hosts or h_port != port:
        return False, "foreign host"

    return True, "ok"
