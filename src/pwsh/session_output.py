"""
session_output.py
Connect a PwshSession to the two output consumers:

  - OutputBuffer  : full scrollback for the human web terminal. Fed by on_output,
                    so it captures EVERYTHING (AI commands, manual typing, echo).
  - SmartOutputFilter : token reduction for the AI. Applied to the clean per-command
                    output that PwshSession already extracts (echo + prompt stripped),
                    so we do NOT need FilteredBuffer's SSH-era line/echo tracking.

This keeps the human stream complete and the AI stream lean, with one clear seam.
"""

import logging

from pwsh.pwsh_session import PwshSession
from completion_token import strip_ansi
from output.output_buffer import OutputBuffer
from output.output_filter import SmartOutputFilter

logger = logging.getLogger(__name__)


class SessionOutput:
    """Owns a PwshSession plus its buffer and filter."""

    def __init__(self, shell=None, max_lines=10000, filter_config=None,
                 on_broadcast=None):
        self.buffer = OutputBuffer(max_lines=max_lines)
        self.filter = SmartOutputFilter(**(filter_config or {}))
        self.on_broadcast = on_broadcast  # callback(raw_chunk) for the web terminal
        self.session = PwshSession(shell=shell, on_output=self._on_output)

    # --- lifecycle ----------------------------------------------------------

    def start(self):
        return self.session.start()

    def restart(self):
        # Fresh shell state; keep buffer history (human scrollback) intact.
        return self.session.restart()

    def close(self):
        self.session.close()

    def is_alive(self):
        return self.session.is_alive()

    # --- output sink --------------------------------------------------------

    def _on_output(self, chunk):
        """Every pty chunk: into the full buffer, and out to the web terminal.

        ANSI is stripped for the stored buffer (clean scrollback text); the raw
        chunk (escape codes intact) goes to the web terminal so xterm.js renders
        colors. The completion token tail is left for the web layer to strip.
        """
        self.buffer.add(strip_ansi(chunk))
        if self.on_broadcast:
            try:
                self.on_broadcast(chunk)
            except Exception:
                logger.exception("on_broadcast failed")

    # --- AI command path ----------------------------------------------------

    def run_command(self, command, timeout=60.0):
        """Run an AI command; return both raw and AI-filtered output.

        result keys: status, success, exit_code, output (raw clean),
                     filtered (token-reduced), should_send.
        """
        result = self.session.run_command(command, timeout=timeout)
        raw = result.get("output", "") or ""
        result["filtered"] = self.filter.filter_output(command, raw)
        result["should_send"] = self.filter.should_send(command, raw)
        return result

    def wait_more(self, command, timeout=60.0):
        """Continue an in-flight command (post send_input); same enrichment."""
        result = self.session.wait_more(timeout=timeout)
        raw = result.get("output", "") or ""
        result["filtered"] = self.filter.filter_output(command, raw)
        result["should_send"] = self.filter.should_send(command, raw)
        return result

    def send_input(self, text):
        self.session.send_input(text)

    def send_interrupt(self):
        self.session.send_interrupt()

    # --- human / passthrough ------------------------------------------------

    def send_manual(self, data):
        """Human-typed keystrokes from the web terminal: raw passthrough, NOT
        tracked for AI completion."""
        self.session.send_manual_raw(data)

    def resize(self, cols, rows):
        self.session.resize(cols, rows)

    # --- buffer access ------------------------------------------------------

    def get_recent(self, n=100):
        return self.buffer.get_text(start=-n)

    def get_stats(self):
        return self.buffer.get_stats()
