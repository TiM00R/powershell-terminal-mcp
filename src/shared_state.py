"""
Shared terminal state - singleton hub between the MCP server and the Web UI.

Local PowerShell rewrite: owns ONE SessionOutput (PwshSession + OutputBuffer +
SmartOutputFilter). The web layer talks to this via the same interface it always
has (web_server_running, get_output(), _handle_output()), plus send_manual_input()
and resize(). AI tools call run_command()/send_input()/etc.

Removed vs the SSH version: ssh_manager, prompt_detector, machine_id, sudo preauth,
SFTP transfer state, the background monitor_command (token completion replaces it),
the SSH CommandRegistry monitoring, and the old ConversationState (the conversation
tools are now DB-backed directly). The database is created in initialize().
"""

import threading
import logging
from typing import Optional

from config.config_loader import Config
from pwsh.session_output import SessionOutput
from db import Database, should_persist_output
from utils.utils import is_error_output, extract_error_context, count_lines

logger = logging.getLogger(__name__)


class SharedTerminalState:
    """Singleton shared state. One local PowerShell session, seen by AI and humans."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
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
        """Build the session + buffer + filter. Does not start the pty yet."""
        if self.session_output is not None:
            return

        self.config = config

        self.database = Database()
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
        if not self.session_output:
            return False
        # The init script clears the screen (wiping the dot-source echo) and prints
        # the banner through ConPTY, so the web terminal opens clean with the first
        # prompt correctly positioned below the banner. No queue surgery needed.
        ok = self.session_output.start()
        if ok and self.database and self._active_conversation_id is None:
            try:
                self._active_conversation_id = self.database.create_conversation(
                    label="session")
            except Exception:
                logger.exception("could not create conversation")
        return ok

    def restart_session(self) -> bool:
        if not self.session_output:
            return False
        return self.session_output.restart()

    def close_session(self):
        if self.session_output:
            self.session_output.close()

    def is_alive(self) -> bool:
        return bool(self.session_output and self.session_output.is_alive())

    # --- output plumbing (web UI) -------------------------------------------

    def _enqueue_output(self, chunk: str):
        """on_broadcast sink: queue raw chunks for the web broadcast loop."""
        with self.output_lock:
            self.output_queue.append(chunk)

    def _handle_output(self, text: str):
        """Inject text directly into the web stream (used by broadcast_raw_output)."""
        with self.output_lock:
            self.output_queue.append(text)

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

    # --- web terminal input -------------------------------------------------

    def send_manual_input(self, data: str):
        """Raw keystrokes typed in a web terminal (not tracked for AI completion)."""
        if self.session_output:
            self.session_output.send_manual(data)

    def resize(self, cols: int, rows: int):
        if self.session_output:
            self.session_output.resize(cols, rows)

    # --- AI command path ----------------------------------------------------

    def run_command(self, command: str, timeout: float = 60.0):
        result = self.session_output.run_command(command, timeout=timeout)
        self._log_command(command, result, is_script=False)
        return result

    def _log_command(self, command, result, is_script=False, flagged=False):
        """Log a command with selective full-output persistence (spec section 6)."""
        if not self.database:
            return
        try:
            output = result.get("output", "") or ""
            success = bool(result.get("success"))
            status = result.get("status", "executed")
            try:
                patterns = self.config.claude.error_patterns or []
            except Exception:
                patterns = []
            has_err = (not success) or is_error_output(output, patterns)
            err_ctx = extract_error_context(output) if has_err else None
            reason = should_persist_output(success, is_script=is_script, flagged=flagged)
            self.database.log_command(
                conversation_id=self._active_conversation_id,
                command_text=command,
                exit_code=result.get("exit_code"),
                success=success,
                status=status,
                has_errors=has_err,
                error_context=err_ctx,
                line_count=count_lines(output),
                output_text=(output if reason else None),
                output_persisted=reason,
            )
        except Exception:
            logger.exception("command logging failed")

    def wait_more(self, command: str, timeout: float = 60.0):
        return self.session_output.wait_more(command, timeout=timeout)

    def send_input(self, text: str):
        self.session_output.send_input(text)

    def send_interrupt(self):
        self.session_output.send_interrupt()

    # --- conversation tools (DB-backed) -------------------------------------

    def start_conversation(self, label=None):
        if not self.database:
            return None
        cid = self.database.create_conversation(label)
        self._active_conversation_id = cid
        return cid

    def end_conversation(self, conversation_id=None, status="completed"):
        cid = conversation_id or self._active_conversation_id
        if self.database and cid:
            self.database.end_conversation(cid, status)
        return cid

    def list_conversations(self, limit=20):
        return self.database.list_conversations(limit) if self.database else []

    def get_conversation_commands(self, conversation_id):
        return (self.database.get_conversation_commands(conversation_id)
                if self.database else [])

    # --- script store -------------------------------------------------------

    def save_script(self, name, content):
        if self.database:
            self.database.save_script(name, content)
        return name

    def list_scripts(self):
        return self.database.list_scripts() if self.database else []

    def run_script(self, name, timeout=120.0):
        import os
        import tempfile
        sc = self.database.get_script(name) if self.database else None
        if not sc:
            return {"status": "error", "error": "unknown script: " + name}
        fd, path = tempfile.mkstemp(suffix=".ps1", prefix="pwsh_script_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sc["content"])
        try:
            result = self.session_output.run_command("& '" + path + "'", timeout=timeout)
            self._log_command("run_script:" + name, result, is_script=True)
            if self.database:
                self.database.touch_script(name)
            return result
        finally:
            try:
                os.remove(path)
            except Exception:
                pass


# Global shared state
_shared_state = SharedTerminalState()


def get_shared_state() -> SharedTerminalState:
    """Get the global shared state instance."""
    return _shared_state
