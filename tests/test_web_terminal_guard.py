"""
Web terminal websocket must only accept its own page (Origin + Host check).
Blocks cross-site websocket hijacking and DNS rebinding.

Runs in memory (Starlette TestClient): no port is opened.
  python tests/test_web_terminal_guard.py      (no pytest needed)
  python -m pytest tests/test_web_terminal_guard.py
"""

import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.web.web_terminal import WebTerminalServer

PORT = 8090


class FakeState:
    """Just enough of SharedTerminalState for the websocket handler."""

    def __init__(self):
        self.web_server_running = False   # broadcast loop exits at once
        self.inputs = []
        self.got_input = threading.Event()

    def send_manual_input(self, data):
        self.inputs.append(data)
        self.got_input.set()

    def clear_output_queue(self): pass
    def get_replay_tail(self): return ""
    def get_output(self): return ""
    def resize(self, cols, rows): pass


def make_server(host="localhost"):
    state = FakeState()
    config = SimpleNamespace(server=SimpleNamespace(host=host, port=PORT))
    return WebTerminalServer(state, config), state


def try_type(origin, base_url="http://localhost:%d" % PORT):
    """Connect, type a command. Returns (accepted, inputs that reached the shell)."""
    server, state = make_server()
    client = TestClient(server._build_app(), base_url=base_url)
    headers = {"origin": origin} if origin else {}
    try:
        ws_url = base_url.replace("http", "ws", 1) + "/ws/terminal"   # full URL so Host is right
        with client.websocket_connect(ws_url, headers=headers) as ws:
            ws.send_json({"type": "terminal_input", "data": "whoami\r"})
            state.got_input.wait(1.0)
            return True, state.inputs
    except WebSocketDisconnect:
        return False, state.inputs


# --- must be REJECTED (these fail on the current code) ----------------------

def test_foreign_origin_rejected():
    accepted, inputs = try_type("https://evil.example")
    assert not accepted and not inputs


def test_missing_origin_rejected():
    accepted, inputs = try_type(None)
    assert not accepted and not inputs


def test_wrong_port_origin_rejected():
    accepted, inputs = try_type("http://localhost:9999")
    assert not accepted and not inputs


def test_dns_rebinding_host_rejected():
    # Page and Host both claim evil.example (rebinding to 127.0.0.1).
    accepted, inputs = try_type("http://evil.example:%d" % PORT,
                                base_url="http://evil.example:%d" % PORT)
    assert not accepted and not inputs


# --- must be ACCEPTED (the real page) ---------------------------------------

def test_localhost_page_accepted():
    accepted, inputs = try_type("http://localhost:%d" % PORT)
    assert accepted and inputs == ["whoami\r"]


def test_127_page_accepted():
    accepted, inputs = try_type("http://127.0.0.1:%d" % PORT,
                                base_url="http://127.0.0.1:%d" % PORT)
    assert accepted and inputs == ["whoami\r"]


def test_ipv6_page_accepted():
    # TestClient cannot parse "[::1]" hosts, so check the guard itself.
    from src.web.web_terminal_guard import check_ws
    ok, _ = check_ws({"origin": "http://[::1]:%d" % PORT, "host": "[::1]:%d" % PORT},
                     "localhost", PORT)
    assert ok


# --- take-over endpoint unchanged -------------------------------------------

def test_release_still_needs_token():
    server, _ = make_server()
    server._token = "secret"
    server.on_release = lambda: None
    client = TestClient(server._build_app(), base_url="http://localhost:%d" % PORT)
    assert client.post("/api/release").status_code == 403
    assert client.post("/api/release", headers={"X-PT-Token": "secret"}).status_code == 200


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
