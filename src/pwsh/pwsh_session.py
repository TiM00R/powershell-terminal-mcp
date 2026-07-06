"""
pwsh_session.py
PwshSession: one persistent PowerShell 7 session over a ConPTY. Owns the process,
the background reader, and token-based completion detection. This is the production
form of the proven spike.

Consumers:
  - AI path: run_command() -> token completion -> {success, exit_code, output}.
  - Human path (later): raw output is forwarded via on_output for the web terminal.

Not yet wired to the output buffer / web broadcast / DB; that is the next increment.
Designed for it: on_output(raw_chunk) is the single hook to forward output.
"""

import os
import time
import threading
import logging

from pwsh import pwsh_launch, pwsh_io
from pwsh.pwsh_reader import ReaderThread
from pwsh.output_clean import extract_output
from completion_token import CompletionToken

logger = logging.getLogger(__name__)

SETTLE_TIMEOUT = 15.0
DEFAULT_COMMAND_TIMEOUT = 60.0


class PwshSession:
    def __init__(self, shell=None, on_output=None, dimensions=None):
        self._shell_pref = shell
        self.on_output = on_output            # callback(raw_chunk) for buffer/web
        self.dimensions = dimensions or pwsh_launch.DEFAULT_DIMENSIONS
        self.token = None
        self.proc = None
        self.reader = None
        self.shell_cmd = None
        self._buf = ""
        self._lock = threading.Lock()
        self._scan_pos = 0
        self._init_script = None

    # --- lifecycle ----------------------------------------------------------

    def start(self, settle_timeout=SETTLE_TIMEOUT):
        """Spawn the session, set encoding + prompt override, wait for first token.

        Returns True if the session settled (prompt token detected).
        """
        self.token = CompletionToken()
        shell = pwsh_launch.find_shell(self._shell_pref)
        self.shell_cmd = pwsh_launch.spawn_command(shell)
        self.proc = pwsh_launch.spawn(self.shell_cmd, self.dimensions)

        self._buf = ""
        self._scan_pos = 0
        self.reader = ReaderThread(self.proc, self._on_data, pwsh_launch.READ_CHUNK)
        self.reader.start()

        self._init_script = pwsh_launch.write_init_script(self.token)
        time.sleep(0.6)  # let the shell start before we inject
        pwsh_io.write_line(self.proc, ". '" + self._init_script + "'")

        found, _, _ = self.wait_token(settle_timeout)
        if not found:
            logger.warning("session did not settle within %ss", settle_timeout)
        return found

    def restart(self, settle_timeout=SETTLE_TIMEOUT):
        """Kill and respawn a fresh session (deliberately clears live state)."""
        self.close()
        return self.start(settle_timeout)

    def close(self):
        if self.reader:
            self.reader.stop()
        if self.proc:
            try:
                self.proc.terminate(force=True)
            except Exception:
                pass
        self._cleanup_init_script()
        self.proc = None
        self.reader = None

    # --- output plumbing ----------------------------------------------------

    def _on_data(self, data):
        with self._lock:
            self._buf += data
        if self.on_output:
            try:
                self.on_output(data)
            except Exception:
                logger.exception("on_output failed")

    def wait_token(self, budget=DEFAULT_COMMAND_TIMEOUT, poll=0.05):
        """Scan the RAW buffer from _scan_pos for the next token (the token rides
        inside an OSC sequence, so we must NOT ANSI-strip before matching).
        Returns (found, raw, match).
        """
        deadline = time.time() + budget
        while time.time() < deadline:
            with self._lock:
                raw = self._buf
            match = self.token.search(raw, self._scan_pos)
            if match:
                self._scan_pos = match.end()
                return True, raw, match
            time.sleep(poll)
        with self._lock:
            raw = self._buf
        return False, raw, None

    # --- commands -----------------------------------------------------------

    def run_command(self, command, timeout=DEFAULT_COMMAND_TIMEOUT):
        """Run an AI command; scan only output produced AFTER this point so any
        prior manual output is ignored (manual commands invisible to AI detection).

        Returns dict: status (completed|running), success, exit_code, output.
        On timeout (interactive prompt or long job) status='running' with partial
        output; caller may send_input / send_interrupt / wait again.
        """
        with self._lock:
            self._scan_pos = len(self._buf)
        start = self._scan_pos

        pwsh_io.write_line(self.proc, self.token.wrap_command(command))
        found, raw, match = self.wait_token(timeout)
        end = self._output_end(raw, match) if found else len(raw)
        output = extract_output(raw[start:end], self.token.start_marker)

        if not found:
            return {"status": "running", "success": None,
                    "exit_code": None, "output": output}
        success, code = self.token.parse(match)
        return {"status": "completed", "success": success,
                "exit_code": code, "output": output}

    def wait_more(self, timeout=DEFAULT_COMMAND_TIMEOUT):
        """Continue waiting for the token of an in-flight command (post send_input).

        Returns the same dict shape as run_command.
        """
        start = self._scan_pos
        found, raw, match = self.wait_token(timeout)
        end = self._output_end(raw, match) if found else len(raw)
        output = extract_output(raw[start:end], self.token.start_marker)
        if not found:
            return {"status": "running", "success": None,
                    "exit_code": None, "output": output}
        success, code = self.token.parse(match)
        return {"status": "completed", "success": success,
                "exit_code": code, "output": output}

    @staticmethod
    def _output_end(raw, match):
        """End of real output = start of the prompt LINE carrying the token, i.e.
        the last newline before the token's OSC (drops the next prompt's base)."""
        nl = raw.rfind("\n", 0, match.start())
        return nl if nl != -1 else match.start()

    def send_input(self, text):
        """Feed a line to a running/interactive command (e.g. answer Read-Host)."""
        pwsh_io.write_line(self.proc, text)

    def send_manual_raw(self, data):
        """Human web-terminal keystrokes: raw passthrough (no newline added, no AI
        completion tracking). xterm.js sends raw keys including Enter as \r."""
        pwsh_io.write_raw(self.proc, data)

    def resize(self, cols, rows):
        """Resize the ConPTY (xterm.js terminal_resize)."""
        try:
            self.proc.setwinsize(rows, cols)
        except Exception:
            logger.debug("resize failed", exc_info=True)

    def send_interrupt(self):
        """Send Ctrl+C to the foreground command."""
        pwsh_io.send_interrupt(self.proc)

    def is_alive(self):
        try:
            return self.proc is not None and self.proc.isalive()
        except Exception:
            return False

    # --- internals ----------------------------------------------------------

    def _cleanup_init_script(self):
        if self._init_script and os.path.exists(self._init_script):
            try:
                os.remove(self._init_script)
            except Exception:
                pass
        self._init_script = None
