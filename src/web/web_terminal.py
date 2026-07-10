"""
Web Terminal Server - NiceGUI-based xterm.js interface
WITH WebSocket broadcast for multi-terminal synchronization
FIXED: Proper WebSocket message handling that keeps connection alive
"""

import asyncio
import sys
import os
import threading
import time
import logging
import webbrowser
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Import from split modules
from .web_terminal_websocket import WebSocketManager
from .web_terminal_ui import create_terminal_page


class WebTerminalServer:
    """
    Web-based terminal interface with WebSocket broadcast
    Multiple browser windows stay perfectly synchronized
    """

    def __init__(self, shared_state, config, hosts_manager=None):
        """
        Initialize web terminal server

        Args:
            shared_state: SharedTerminalState instance
            config: Config instance
            hosts_manager: ignored (kept for signature compatibility)
        """
        self.shared_state = shared_state
        self.config = config
        self.thread: Optional[threading.Thread] = None
        self._running = False

        # Create WebSocket manager
        self._ws_manager = WebSocketManager(shared_state)

    def is_running(self) -> bool:
        """Check if web server is running"""
        return self.shared_state.web_server_running

    def get_connection_display(self) -> str:
        """Get current connection info for display"""
        return "PowerShell 7 (local)"

    def start(self):
        """Start the web terminal server in a background thread"""
        if self.is_running():
            logger.info("Web terminal already running")
            return

        logger.info(f"Starting web terminal on http://{self.config.server.host}:{self.config.server.port}")

        # Mark running BEFORE the thread can accept connections. NiceGUI can
        # start serving during the sleep() below; if a tab connects before this
        # flag is True, the broadcast loop sees web_server_running == False and
        # exits immediately, leaving every later tab blank/unresponsive.
        self.shared_state.web_server_running = True

        self.thread = threading.Thread(target=self._run_web_server, daemon=True)
        self.thread.start()

        time.sleep(2)

        # NOTE: do NOT open a browser here. start() launches the server only.
        # open_terminal() is the single place that opens a tab, so we never
        # get a duplicate tab from start()+open_terminal() firing together.
        logger.info("Web terminal server started (browser opened by open_terminal)")

    def stop(self):
        """Stop the web terminal server and close all WebSocket connections"""
        if not self.is_running():
            logger.info("Web terminal not running")
            return

        logger.info("Stopping web terminal server...")

        # Close all WebSocket connections
        self._ws_manager.close_all()

        # Stop the server
        self.shared_state.web_server_running = False
        logger.info("Web terminal server stopped")

    async def open_terminal(self):
        """
        Close all existing terminal tabs, then open exactly one fresh tab.
        Always runs supersede-then-open so we never stack a second tab.
        """
        if not self.is_running():
            self.start()  # starts server only, does NOT open a browser
        # Close any existing tabs first. Connected tabs run window.close()
        # on the session_superseded message (same path used on WS drop).
        await self._ws_manager.broadcast_session_superseded()
        # Give existing tabs a moment to actually close before opening a new one.
        await asyncio.sleep(0.5)
        # Open exactly one fresh tab.
        url = f"http://{self.config.server.host}:{self.config.server.port}"
        webbrowser.open(url)
        logger.info(f"Opened fresh terminal tab: {url}")
        # Delay to allow browser tab to open and WebSocket to connect
        await asyncio.sleep(2.0)

    def _run_web_server(self):
        """Run NiceGUI web server (runs in separate thread)"""
        try:
            old_stdout = sys.stdout
            sys.stdout = sys.stderr

            from nicegui import ui, app
            from starlette.responses import JSONResponse
            from starlette.websockets import WebSocket

            # Configure static files
            # static_dir is in src/static, go up one level from web/ directory
            static_dir = os.path.join(os.path.dirname(__file__), '..', 'static')

            if os.path.exists(static_dir):
                app.add_static_files('/static', static_dir)
                logger.info(f"Serving static files from: {static_dir}")
            else:
                logger.warning(f"Static directory not found: {static_dir}")

            # WebSocket endpoint for terminal synchronization
            @app.websocket('/ws/terminal')
            async def websocket_endpoint(websocket: WebSocket):
                """WebSocket endpoint for bidirectional terminal communication"""
                await websocket.accept()
                await self._ws_manager.handle_websocket(websocket)

            # API endpoint for connection info (still used by UI)
            @app.get('/api/connection_info')
            def handle_connection_info():
                """Get current connection info"""
                connection_info = self.get_connection_display()
                return JSONResponse({'connection': connection_info})

            # Create terminal UI page
            create_terminal_page(ui, self)

            # Run server with socket reuse enabled for immediate restart
            import socket
            ui.run(
                host=self.config.server.host,
                port=self.config.server.port,
                title='PowerShell Terminal',
                show=False,
                reload=False,
                timeout_graceful_shutdown=1
            )

            sys.stdout = old_stdout

        except Exception as e:
            logger.error(f"Web server error: {e}", exc_info=True)
            self.shared_state.web_server_running = False
