"""
eventlog.py - optional diagnostic log of web terminal take-overs and tab events.

Off unless config.yaml has server.event_log: true. Every copy of the server on the
machine appends to the same file (next to the owner file), so one file shows what
all copies did and in what order. Used to track down stray browser tabs.

Logging never raises: if the file cannot be written, the event is skipped.
"""

import os
import threading
import time

from .ownership import OWNER_DIR

LOG_PATH = OWNER_DIR / "web.log"
MAX_BYTES = 1024 * 1024        # rotate to web.log.1 past this size (~2 MB total)

_enabled = False
_client = ""
_lock = threading.Lock()


def configure(enabled: bool):
    """Switch logging on or off (from config at startup)."""
    global _enabled
    _enabled = bool(enabled)


def set_client(label: str):
    """Name of the Claude client this copy serves, added to every line."""
    global _client
    _client = label or ""


def _fmt(value) -> str:
    if isinstance(value, float):
        return "%.2f" % value
    text = str(value)
    if not text or any(c in text for c in ' "='):
        return '"%s"' % text.replace('"', "'")
    return text


def log_event(event: str, **details):
    """Append one line: time, pid, client, event and key=value details."""
    if not _enabled:
        return
    try:
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) + ".%03d" % (now % 1 * 1000)
        parts = [stamp, "pid=%d" % os.getpid(), "client=%s" % _fmt(_client), "event=%s" % event]
        parts += ["%s=%s" % (k, _fmt(v)) for k, v in details.items()]
        line = " ".join(parts) + "\n"
        with _lock:
            OWNER_DIR.mkdir(parents=True, exist_ok=True)
            try:
                if LOG_PATH.stat().st_size > MAX_BYTES:
                    os.replace(LOG_PATH, LOG_PATH.with_name("web.log.1"))
            except OSError:
                pass        # no file yet, or another copy is rotating it
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
