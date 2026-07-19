"""
Shared terminal state - singleton hub between the MCP server and the Web UI.

Local PowerShell rewrite: owns ONE SessionOutput (PwshSession + OutputBuffer +
SmartOutputFilter). The web layer talks to this via the same interface it always
has (web_server_running, get_output()), plus send_manual_input()
and resize(). AI tools call run_command()/send_input()/etc.

DB-backed command logging, conversation tools, history/prune, and the script store
live in StateHistoryMixin (state_history.py); this class owns the session, the
output plumbing, and the AI execution path. The database is created in initialize().
"""

import threading
import logging
from typing import Optional

from config.config_loader import Config
from pwsh.session_output import SessionOutput
from db import Database
from web_replay import build_replay_tail
from state_history import StateHistoryMixin

logger = logging.getLogger(__name__)


class SharedTerminalState(StateHistoryMixin):
    """Singleton shared state. One local PowerShell session, seen by AI and humans."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton gate. The MCP server and the web server are separate entry
        points in the same process; both must land on the SAME session, so
        construction is funnelled through one instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Declare the slots only. Real construction is deferred to initialize()
        because config is not available until the MCP server has loaded it."""
        if self._initialized:
            return

        self.config: Optional[Config] = None
        self.session_output: Optional[SessionOutput] = None
        self.database = None  # wired in the tools increment

        self.web_server_running = False
        self.output_queue = []
        self.output_lock = threading.Lock()

        self._initialized = True

    # --- initialization -----------------------------------------------------

    def initialize(self, config: Config):
        """Assemble the object graph once config exists: database, then the
        session/buffer/filter stack. Split from __init__ so the singleton can be
        imported freely before the server has read config.yaml.

        Deliberately does NOT start the pty -- the shell comes up on first use
        (see start_session), so importing the module never spawns a process.
        """
        if self.session_output is not None:
            return

        self.config = config

        db_path = None
        try:
            db_path = self.config.database.path
        except Exception:
            db_path = None
        self.database = Database(path=db_path)
        self._active_conversation_id = None

        filter_config = {
            "thresholds": config.claude.thresholds,
            "truncation": config.claude.truncation,
            "error_patterns": config.claude.error_patterns,
            "auto_send_errors": config.claude.auto_send_errors,
        }

        self.session_output = SessionOutput(
            shell=getattr(config, "shell_path", None),
            max_lines=config.buffer.max_lines,
            filter_config=filter_config,
            on_broadcast=self._enqueue_output,
        )

    # --- session lifecycle --------------------------------------------------

    def start_session(self) -> bool:
        """Bring the PowerShell session up and attach it to a conversation.

        Also the point where history housekeeping happens: an existing active
        conversation is adopted (so a server restart continues the same thread
        instead of fragmenting history), and retention pruning runs once here
        rather than on a timer. Both are best-effort -- a history problem must
        never stop the terminal from starting.
        """
        if not self.session_output:
            return False
        # The init script clears the screen (wiping the dot-source echo) and prints
        # the banner through ConPTY, so the web terminal opens clean with the first
        # prompt correctly positioned below the banner. No queue surgery needed.
        ok = self.session_output.start()
        if ok and self.database and self._active_conversation_id is None:
            try:
                existing = self.database.get_active_conversation()
                if existing:
                    self._active_conversation_id = existing["id"]
                else:
                    self._active_conversation_id = self.database.create_conversation(
                        label="session")
            except Exception:
                logger.exception("could not resolve active conversation")
            try:
                self.prune_history()
            except Exception:
                logger.exception("prune failed")
        return ok

    def restart_session(self) -> bool:
        """Recycle the underlying pty after a hang or a wedged shell, keeping this
        singleton (and the web clients bound to it) in place."""
        if not self.session_output:
            return False
        return self.session_output.restart()

    def close_session(self):
        """Shutdown path, called when the MCP server goes away."""
        if self.session_output:
            self.session_output.close()

    def is_alive(self) -> bool:
        """Liveness probe used by tools and the web layer before acting on a
        session that may have exited underneath them."""
        return bool(self.session_output and self.session_output.is_alive())

    # --- output plumbing (web UI) -------------------------------------------

    def _enqueue_output(self, chunk: str):
        """on_broadcast sink: queue raw chunks for the web broadcast loop."""
        with self.output_lock:
            self.output_queue.append(chunk)

    def get_output(self) -> str:
        """Drain queued output for the web UI (polled by the broadcast loop).
        Passed through raw: the completion token and start marker ride inside OSC
        sequences that xterm.js swallows, so no text stripping is needed and the
        screen-addressed ConPTY render stays intact.
        """
        with self.output_lock:
            if self.output_queue:
                output = "".join(self.output_queue)
                self.output_queue.clear()
                return output
            return ""

    def get_replay_tail(self) -> str:
        """Screen content replayed to a newly connected web client (e.g. a reopened
        tab) so it is not blank. Kept RAW (ANSI intact) so colors AND the
        carriage-return line redraws render exactly as they did live; query/response
        sequences are stripped so the reconnecting xterm doesn't reply into stdin.

        Mode is server.replay_lines:
          < 0  -> FULL buffer from session start. Faithful and cursor-safe (it is the
                  exact byte stream xterm already processed); ignores the byte cap.
          == 0 -> current prompt line only: no history, but still a usable prompt (no
                  manual Enter needed).
          > 0  -> last N physical lines, byte-capped by server.replay_max_bytes. Fast
                  and small, but a cut landing inside stateful output (TUI, alt-screen,
                  scroll region) can render imperfectly -- fine for plain history.
        Returns '' only when there is nothing to show.
        """
        if not self.session_output:
            return ""
        max_lines, max_bytes = 40, 8192
        try:
            sc = self.config.server
            max_lines = int(getattr(sc, "replay_lines", max_lines))
            max_bytes = int(getattr(sc, "replay_max_bytes", max_bytes))
        except Exception:
            pass
        try:
            raw = self.session_output.get_raw_buffer() or ""
        except Exception:
            return ""
        if not raw:
            return ""
        # The raw-tail construction (mode handling + query/response stripping) lives
        # in web_replay.build_replay_tail; this method just supplies config + buffer.
        return build_replay_tail(raw, max_lines, max_bytes)

    # --- web terminal input -------------------------------------------------

    def send_manual_input(self, data: str):
        """Raw keystrokes typed in a web terminal (not tracked for AI completion)."""
        if self.session_output:
            self.session_output.send_manual(data)

    def resize(self, cols: int, rows: int):
        """Propagate the browser terminal's geometry to the pty so PowerShell wraps
        and redraws to the window the user is actually looking at."""
        if self.session_output:
            self.session_output.resize(cols, rows)

    # --- AI command path ----------------------------------------------------

    def run_command(self, command: str, timeout: float = 60.0):
        """The main AI entry point: run a command to completion and record it.

        Execution is delegated to SessionOutput; the value added here is that
        every AI-issued command lands in the DB, which is what makes history and
        after-the-fact forensics possible.
        """
        result = self.session_output.run_command(command, timeout=timeout)
        self._log_command(command, result, is_script=False)
        return result

    def wait_more(self, command: str, timeout: float = 60.0):
        """Continue waiting on a command that already returned as still-running,
        so a slow job can be polled instead of blocking one long tool call.
        Deliberately not logged again -- the command was recorded by run_command.
        """
        return self.session_output.wait_more(command, timeout=timeout)

    def send_input(self, text: str):
        """Feed a line to a command that stopped for input during the normal
        (non-interactive) path, e.g. an unexpected confirmation prompt."""
        self.session_output.send_input(text)

    # --- AI interactive-input path ------------------------------------------

    def run_command_interactive(self, command: str, idle_ms=None, max_s=None,
                                expect=None):
        """Opt-in path for programs that prompt (REPLs, installers, wizards).

        Unlike run_command, this returns as soon as the program is judged to be
        waiting, so the caller can answer it; the state machine lives in
        SessionOutput. Logged so interactive work appears in history too.
        """
        result = self.session_output.run_command_interactive(
            command, idle_ms=idle_ms, max_s=max_s, expect=expect)
        self._log_interactive(command, result)
        return result

    def send_input_interactive(self, text: str, idle_ms=None, max_s=None,
                               expect=None):
        """Answer an interactive prompt and wait for the next state (another
        prompt, or exit). The turn-by-turn half of run_command_interactive."""
        return self.session_output.send_input_interactive(
            text, idle_ms=idle_ms, max_s=max_s, expect=expect)

    def wait_interactive(self, idle_ms=None, max_s=None, expect=None):
        """Re-observe an interactive session without sending anything -- used when
        a step returned RUNNING and the caller just needs to look again."""
        return self.session_output.wait_interactive(
            idle_ms=idle_ms, max_s=max_s, expect=expect)

    def send_interrupt(self):
        """Ctrl+C. The escape hatch for a runaway or wedged command, letting the
        session be reclaimed without tearing down the pty."""
        self.session_output.send_interrupt()


# Global shared state
_shared_state = SharedTerminalState()


def get_shared_state() -> SharedTerminalState:
    """Accessor every module uses to reach the one live session. Importing this
    rather than constructing SharedTerminalState keeps the singleton honest."""
    return _shared_state
