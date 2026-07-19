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
from output import OutputBuffer
from output.output_filter import SmartOutputFilter

logger = logging.getLogger(__name__)


class SessionOutput:
    """Owns a PwshSession plus its buffer and filter.

    This is the seam where one pty stream is split for two very different
    audiences, and it is the only layer that knows both exist.
    """

    def __init__(self, shell=None, max_lines=10000, filter_config=None,
                 on_broadcast=None):
        """Wire the trio together. on_broadcast is the web terminal's tap on the
        live stream, injected rather than imported so this layer stays unaware
        of the web server."""
        self.buffer = OutputBuffer(max_lines=max_lines)
        self.filter = SmartOutputFilter(**(filter_config or {}))
        self.on_broadcast = on_broadcast  # callback(raw_chunk) for the web terminal
        self.session = PwshSession(shell=shell, on_output=self._on_output)

    # --- lifecycle ----------------------------------------------------------

    def start(self):
        """Pass-through to the session; kept so callers never reach past this
        facade to the PwshSession underneath."""
        return self.session.start()

    def restart(self):
        """Replace the shell process while keeping this object (and the web
        clients bound to it) alive."""
        # Fresh shell state; keep buffer history (human scrollback) intact.
        return self.session.restart()

    def close(self):
        """Tear down the shell on shutdown."""
        self.session.close()

    def is_alive(self):
        """Whether the shell process is still usable."""
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
        """Answer a prompt hit during a batch command. No filtering happens here;
        the reply is picked up by the following wait_more."""
        self.session.send_input(text)

    # --- interactive-input path ---------------------------------------------
    # Interactive output is returned RAW (contract: output = buffer slice). The
    # SmartOutputFilter is a batch token-reducer and would risk hiding the very
    # prompt the AI must read, so it is NOT applied here.

    def run_command_interactive(self, command, idle_ms=None, max_s=None, expect=None):
        """Start a command expected to prompt, returning as soon as it settles
        into a state the caller can act on."""
        kwargs = self._interactive_kwargs(idle_ms, max_s)
        return self.session.run_command(command, interactive=True, expect=expect,
                                        **kwargs)

    def send_input_interactive(self, text, idle_ms=None, max_s=None, expect=None):
        """Answer the current prompt and report the next state."""
        kwargs = self._interactive_kwargs(idle_ms, max_s)
        return self.session.send_input_interactive(text, expect=expect, **kwargs)

    def wait_interactive(self, idle_ms=None, max_s=None, expect=None):
        """Look again without sending anything, for a step that was still running."""
        kwargs = self._interactive_kwargs(idle_ms, max_s)
        return self.session.poll_interactive(expect=expect, **kwargs)

    @staticmethod
    def _interactive_kwargs(idle_ms, max_s):
        """Forward only the timing knobs the caller actually set, so unset ones
        fall through to the session's configured defaults instead of None."""
        kwargs = {}
        if idle_ms is not None:
            kwargs["idle_ms"] = idle_ms
        if max_s is not None:
            kwargs["max_s"] = max_s
        return kwargs

    def send_interrupt(self):
        """Ctrl+C into the pty -- works for both the batch and interactive paths."""
        self.session.send_interrupt()

    # --- human / passthrough ------------------------------------------------

    def send_manual(self, data):
        """Human-typed keystrokes from the web terminal: raw passthrough, NOT
        tracked for AI completion."""
        self.session.send_manual_raw(data)

    def resize(self, cols, rows):
        """Match the pty to the browser window so wrapping and redraws line up."""
        self.session.resize(cols, rows)

    # --- buffer access ------------------------------------------------------

    def get_raw_buffer(self):
        """Full raw ConPTY buffer (ANSI intact) from the session. Used by the web
        layer to replay the current screen tail (incl. the in-progress prompt line)
        to a newly connected client."""
        return self.session.get_raw_buffer()

    def get_stats(self):
        """Buffer usage, surfaced through the MCP status tool."""
        return self.buffer.get_stats()
