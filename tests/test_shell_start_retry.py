"""
A failed PowerShell start must be retried on the next tool call
(not marked "started" forever), and the half-started shell closed.

No real PowerShell, port, config or database is used.
  python tests/test_shell_start_retry.py      (no pytest needed)
  python -m pytest tests/test_shell_start_retry.py
"""

import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))   # mcp_server imports its siblings by bare name

import mcp_server


class FakeState:
    """Stands in for SharedTerminalState: start_session returns the given
    results in order and counts calls."""

    def __init__(self, results):
        self.results = list(results)
        self.starts = 0
        self.closes = 0

    def start_session(self, start_dir=None):
        self.starts += 1
        return self.results.pop(0)

    def close_session(self):
        self.closes += 1


class FakeWeb:
    def is_running(self):
        return True     # port already ours: skip the take-over path


def make_server(results):
    """Real PowerShellTerminalMCP object, built without __init__ (which would
    load config, open the database and create the web server)."""
    srv = object.__new__(mcp_server.PowerShellTerminalMCP)
    srv.state = FakeState(results)
    srv.web_server = FakeWeb()
    srv._started = False
    srv._own_lock = threading.RLock()
    srv._project_dir = None
    return srv


def call(srv):
    """Run _ensure_owner; return True if it raised RuntimeError (start failed)."""
    try:
        srv._ensure_owner()
        return False
    except RuntimeError:
        return True


# --- fail on the current code ------------------------------------------------

def test_failed_start_is_retried():
    srv = make_server([False, True])     # 1st start times out, 2nd works
    call(srv)
    call(srv)
    assert srv.state.starts == 2


def test_failed_start_reports_error():
    srv = make_server([False])
    assert call(srv) is True             # the AI gets an error, not a silent pass
    assert srv._started is False


def test_failed_start_closes_half_started_shell():
    srv = make_server([False])
    call(srv)
    assert srv.state.closes == 1         # no stray pwsh.exe left running


# --- must keep working --------------------------------------------------------

def test_good_start_happens_once():
    srv = make_server([True])
    assert call(srv) is False
    call(srv)
    assert srv.state.starts == 1 and srv._started and srv.state.closes == 0


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError:
                failed += 1
                print("FAIL", name)
    print("\n%d failed" % failed)
    sys.exit(1 if failed else 0)
