"""
Web Terminal Server - plain Starlette + uvicorn serving an xterm.js page, with
WebSocket broadcast so every open tab shows the same session.

The server can be stopped and started again inside one process. That is what lets
a copy of this MCP server hand the port over to another copy (see ownership.py)
and take it back later.
"""

import asyncio
import logging
import os
import threading
import time
import webbrowser
from typing import Callable, Optional, Tuple

from .web_terminal_websocket import WebSocketManager
from .web_terminal_ui import render_index_html
from . import ownership
from .eventlog import log_event

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'static')


class WebTerminalServer:
    """
    Web-based terminal interface with WebSocket broadcast.
    Multiple browser windows stay synchronized.
    """

    def __init__(self, shared_state, config, hosts_manager=None):
        """
        Args:
            shared_state: SharedTerminalState instance
            config: Config instance
            hosts_manager: ignored (kept for signature compatibility)
        """
        self.shared_state = shared_state
        self.config = config
        self.thread: Optional[threading.Thread] = None
        self._server = None          # uvicorn.Server while running
        self._loop = None            # the server thread's event loop
        self._token: Optional[str] = None
        self._ws_manager = WebSocketManager(shared_state)
        # Called (in a background thread) when another process asks for the
        # port. Set by the MCP server; without it we just stop serving.
        self.on_release: Optional[Callable[[], None]] = None
        # Who owns the terminal, shown in the tab title ("Claude Code · repo").
        self.owner_label: str = ""

    @property
    def url(self) -> str:
        return "http://%s:%s" % (self.config.server.host, self.config.server.port)

    def has_connected_clients(self) -> bool:
        """True if at least one browser tab holds an open WebSocket to this server."""
        return self.is_running() and bool(self._ws_manager.active_websockets)

    def has_visible_clients(self) -> bool:
        """True if a connected tab says it is on screen (not a background tab,
        minimized window, or hidden browser pane)."""
        m = self._ws_manager
        return self.is_running() and bool(m.visible_websockets & m.active_websockets)

    def client_counts(self) -> Tuple[int, int]:
        """(connected tabs, visible tabs)."""
        m = self._ws_manager
        return len(m.active_websockets), len(m.visible_websockets & m.active_websockets)

    def is_running(self) -> bool:
        """True while this process is serving the port."""
        return self._server is not None and self.shared_state.web_server_running

    # --- start / stop -------------------------------------------------------

    def start(self) -> bool:
        """Start serving; returns False (and logs why) if the port is busy."""
        ok, err = self.try_start()
        if not ok:
            logger.error("Web terminal could not start on %s: %s", self.url, err)
        return ok

    def try_start(self, wait_s: float = 10.0) -> Tuple[bool, Optional[str]]:
        """Bind the port and serve in a background thread.

        Returns (True, None) once the socket is bound, or (False, reason) if the
        bind failed (typically: another process owns the port)."""
        if self.is_running():
            return True, None

        import uvicorn

        # Fresh manager per run: the old one's broadcast task belonged to the old
        # (now closed) event loop.
        self._ws_manager = WebSocketManager(self.shared_state)
        server = uvicorn.Server(uvicorn.Config(
            self._build_app(),
            host=self.config.server.host,
            port=self.config.server.port,
            log_config=None,      # never let uvicorn write to stdout (JSON-RPC)
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=1,
        ))
        error = {}

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(server.serve())
            except BaseException as e:      # uvicorn calls sys.exit(1) on bind failure
                error["reason"] = "port %s is in use" % self.config.server.port \
                    if isinstance(e, SystemExit) else repr(e)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        # Set before the thread can accept connections so the broadcast loop
        # does not exit immediately on the first connect.
        self.shared_state.web_server_running = True
        thread = threading.Thread(target=run, daemon=True, name="web-terminal")
        thread.start()

        deadline = time.time() + wait_s
        while time.time() < deadline and thread.is_alive() and not server.started:
            time.sleep(0.05)

        if not server.started:
            server.should_exit = True
            thread.join(2)
            self.shared_state.web_server_running = False
            self._loop = None
            reason = error.get("reason") or "server did not start"
            log_event("bind_failed", port=self.config.server.port, reason=reason)
            return False, reason

        self._server = server
        self.thread = thread
        self._token = ownership.new_token()
        ownership.write_owner(self.config.server.port, self._token)
        logger.info("Web terminal serving %s (owner pid %s)", self.url, os.getpid())
        log_event("bind_ok", port=self.config.server.port)
        return True, None

    def stop(self, timeout: float = 5.0):
        """Stop serving and free the port. Safe to call when not running."""
        server, thread = self._server, self.thread
        if server is None:
            return
        logger.info("Stopping web terminal server...")
        self.shared_state.web_server_running = False   # ends the broadcast loop
        server.should_exit = True
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        self._server = None
        self.thread = None
        self._loop = None
        if self._token:
            ownership.remove_owner(self.config.server.port, self._token)
            self._token = None
        log_event("stopped")
        logger.info("Web terminal server stopped")

    def notify_handover(self, timeout: float = 2.0):
        """Tell every open tab that another session is taking over (tabs then
        reconnect to the new owner). Callable from any thread."""
        self._run_on_server_loop(self._ws_manager.broadcast_handover(), timeout)

    def notify_shutdown(self, timeout: float = 1.0):
        """Tell every open tab that this session is ending, so the tab closes
        itself instead of waiting to reconnect. Callable from any thread; kept
        short because the client may kill the process soon after."""
        self._run_on_server_loop(self._ws_manager.broadcast_shutdown(), timeout)

    async def notice(self, text: str):
        """Show a one-line notice in every open tab (awaited on the caller's
        loop, delivered on the server's)."""
        loop = self._loop
        if not self.is_running() or loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._ws_manager.broadcast_notice(text), loop)
            await asyncio.wrap_future(fut)
        except Exception as e:
            logger.debug("notice broadcast failed: %s", e)

    def _run_on_server_loop(self, coro, timeout: float):
        """Run a broadcast coroutine on the web server's event loop and wait."""
        loop = self._loop
        if not self.is_running() or loop is None:
            coro.close()
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)
        except Exception as e:
            logger.debug("tab broadcast failed: %s", e)

    # --- tabs ---------------------------------------------------------------

    async def wait_for_client(self, timeout: float) -> bool:
        """Wait until a tab connects (e.g. an old tab reconnecting after a
        take-over). Returns True if one did."""
        start = time.time()
        deadline = start + timeout
        found = False
        while time.time() < deadline:
            if self.has_connected_clients():
                found = True
                break
            await asyncio.sleep(0.1)
        found = found or self.has_connected_clients()
        log_event("wait_for_client", found=found, waited=time.time() - start,
                  limit=float(timeout))
        return found

    async def wait_for_visible(self, timeout: float) -> bool:
        """Give connected tabs a moment to report whether they are visible
        (they do so right after connecting). True if one is."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.has_visible_clients():
                return True
            await asyncio.sleep(0.05)
        return self.has_visible_clients()

    async def open_terminal(self, reason: str = ""):
        """Ask existing tabs to step aside, then open exactly one fresh tab.
        (The MCP layer only calls this when no tab is connected, so the
        step-aside message is a safety net.)"""
        if not self.is_running():
            if not self.start():
                return
        loop = self._loop
        if loop is not None and self.has_connected_clients():
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._ws_manager.broadcast_session_superseded(), loop)
                await asyncio.wrap_future(fut)
                log_event("superseded_sent")
            except Exception as e:
                logger.debug("superseded broadcast failed: %s", e)
            await asyncio.sleep(0.5)
        log_event("open_tab", reason=reason or "unspecified",
                  tabs=len(self._ws_manager.active_websockets))
        webbrowser.open(self.url)
        logger.info("Opened terminal tab: %s", self.url)
        # Give the tab time to load and connect its WebSocket.
        await self.wait_for_client(3.0)

    # --- app ----------------------------------------------------------------

    def _build_app(self):
        """Routes: page, static files, terminal WebSocket, release endpoint."""
        from starlette.applications import Starlette
        from starlette.responses import HTMLResponse, JSONResponse
        from starlette.routing import Mount, Route, WebSocketRoute
        from starlette.staticfiles import StaticFiles

        async def index(request):
            return HTMLResponse(render_index_html(self.owner_label))

        async def websocket_endpoint(websocket):
            await websocket.accept()
            # Tell the tab who owns the terminal now (a reconnecting tab may
            # have been showing another session).
            try:
                await websocket.send_json({'type': 'owner', 'label': self.owner_label,
                                           'pid': os.getpid()})
            except Exception:
                pass
            log_event("tab_connected", tabs=len(self._ws_manager.active_websockets) + 1)
            try:
                await self._ws_manager.handle_websocket(websocket)
            finally:
                log_event("tab_disconnected", tabs=len(self._ws_manager.active_websockets))

        async def release(request):
            # Token must match the owner file. A browser page cannot send this
            # custom header cross-origin (no CORS preflight answer), so only a
            # local process that can read the owner file gets here.
            if not self._token or request.headers.get(ownership.TOKEN_HEADER) != self._token:
                log_event("release_forbidden")
                return JSONResponse({"error": "forbidden"}, status_code=403)
            log_event("release_received", tabs=len(self._ws_manager.active_websockets))
            logger.info("Release requested by another session; handing over the web terminal")
            threading.Thread(target=self._do_release, daemon=True,
                             name="web-terminal-release").start()
            return JSONResponse({"releasing": True})

        routes = [
            Route('/', index),
            Route('/api/release', release, methods=['POST']),
            WebSocketRoute('/ws/terminal', websocket_endpoint),
        ]
        if os.path.isdir(STATIC_DIR):
            routes.append(Mount('/static', StaticFiles(directory=STATIC_DIR), name='static'))
        else:
            logger.warning("Static directory not found: %s", STATIC_DIR)
        return Starlette(routes=routes)

    def _do_release(self):
        """Runs off the server thread so stop() can join it."""
        time.sleep(0.1)   # let the HTTP reply go out first
        try:
            if self.on_release:
                self.on_release()
            else:
                self.notify_handover()
                self.stop()
        except Exception:
            logger.exception("release failed")
            self.stop()
