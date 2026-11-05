"""
ownership.py - which powershell-terminal process owns the web terminal port.

Every Claude client (the Desktop app, each Claude Code session) runs its own copy
of this server, but only one can serve http://localhost:<port>. The rule is
"latest user wins": a copy that needs the port asks the current owner to release
it, then binds it.

The owner writes a small file with its pid and a random token. Only a process that
can read that file can ask the owner to release, and the token travels in a custom
header, which a web page in the browser cannot send to localhost without a CORS
preflight that the server never answers.
"""

import ctypes
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-PT-Token"

# Outside any install folder, so every copy on the machine finds the same file.
OWNER_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "powershell-terminal"


def owner_path(port: int) -> Path:
    """Owner file for one port."""
    return OWNER_DIR / ("owner-%d.json" % int(port))


def new_token() -> str:
    """Fresh secret for one ownership period."""
    return secrets.token_hex(16)


def write_owner(port: int, token: str) -> None:
    """Record this process as the port owner (atomic replace)."""
    try:
        OWNER_DIR.mkdir(parents=True, exist_ok=True)
        path = owner_path(port)
        tmp = path.with_suffix(".tmp.%d" % os.getpid())
        tmp.write_text(json.dumps({"pid": os.getpid(), "token": token,
                                   "started": time.time()}), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        logger.exception("could not write owner file")


def read_owner(port: int) -> Optional[dict]:
    """Current owner record, or None if missing/unreadable."""
    try:
        return json.loads(owner_path(port).read_text(encoding="utf-8"))
    except Exception:
        return None


def remove_owner(port: int, token: str) -> None:
    """Delete the owner file, but only if it is still ours (a newer owner may
    already have replaced it)."""
    info = read_owner(port)
    if info and info.get("token") == token:
        try:
            owner_path(port).unlink()
        except Exception:
            pass


def pid_alive(pid: int) -> bool:
    """Windows-safe liveness check. (os.kill(pid, 0) would TERMINATE the process
    on Windows, so it is not usable here.)"""
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def request_release(host: str, port: int, timeout: float = 5.0) -> Tuple[bool, str]:
    """Ask the current owner to give up the port.

    Returns (accepted, reason). accepted=False means the port is held by something
    we cannot talk to (another program, or an owner that did not answer)."""
    info = read_owner(port)
    if not info:
        return False, "port is held by a process with no owner record (another program?)"
    pid = info.get("pid")
    if pid == os.getpid():
        return False, "owner record points at this process"
    if not pid_alive(pid):
        return False, "owner record is stale (pid %s is gone) and the port is held by another program" % pid
    url = "http://%s:%d/api/release" % (host, int(port))
    req = urllib.request.Request(url, data=b"", method="POST",
                                 headers={TOKEN_HEADER: info.get("token", "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                logger.info("owner pid %s accepted release of port %s", pid, port)
                return True, "released by pid %s" % pid
            return False, "owner answered HTTP %s" % resp.status
    except urllib.error.HTTPError as e:
        return False, "owner refused release (HTTP %s)" % e.code
    except Exception as e:
        return False, "owner pid %s did not answer: %s" % (pid, e)
