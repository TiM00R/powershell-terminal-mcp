"""
pwsh_session.py
PwshSession: one persistent PowerShell 7 session over a ConPTY. Owns the process,
the background reader, and token-based completion detection.

This is the bottom of the stack -- the only module that touches the live shell.
Everything above it (SessionOutput, SharedTerminalState, the MCP tools, the web
terminal) is a consumer of the two things produced here: per-command results for
the AI, and a raw byte stream for the human terminal.

Consumers:
  - AI path: run_command() -> token completion -> {success, exit_code, output}.
  - Human path: raw output is forwarded via on_output(raw_chunk), the single hook
    the buffer / web broadcast layer attaches to.

The hard problem solved here is knowing when a command has finished in a stream
that has no framing: a shell echoes, redraws, and prompts, all as plain bytes. The
answer is a unique token emitted by the prompt (see completion_token.py), which is
why so much of this file is careful buffer-position bookkeeping.
"""

import os
import time
import threading
import logging

from pwsh import pwsh_launch, pwsh_io
from pwsh.pwsh_reader import ReaderThread
from pwsh.output_clean import extract_output
from pwsh.pwsh_interactive import (InteractiveMixin, INTERACTIVE_IDLE_MS,
                                   INTERACTIVE_MAX_S)
from completion_token import CompletionToken

logger = logging.getLogger(__name__)

SETTLE_TIMEOUT = 15.0
DEFAULT_COMMAND_TIMEOUT = 60.0

# The interactive-input primitive (wait_interactive + helpers; states EXITED /
# AWAITING_INPUT / IDLE / RUNNING) lives in pwsh_interactive.InteractiveMixin, which
# PwshSession mixes in below. INTERACTIVE_IDLE_MS / INTERACTIVE_MAX_S are imported
# from there so run_command's interactive defaults match the primitive's.


class PwshSession(InteractiveMixin):
    """The live PowerShell process and everything needed to read it reliably.

    Holds no policy about what to run or how to present output; it only
    guarantees that a command's bytes can be told apart from the shell's own
    noise and from anything a human typed in the meantime.
    """

    def __init__(self, shell=None, on_output=None, dimensions=None):
        """Set up state only -- no process is spawned until start().

        The two position markers matter: _scan_pos is how far the completion
        scanner has consumed, and _interactive_start marks where the current
        interactive step began. They are what keep one command's output from
        bleeding into the next.
        """
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
        self._interactive_start = 0     # slice pointer for the interactive primitive
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
        self._interactive_start = 0
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
        """Stop the reader, kill the shell, and remove the temp init script so a
        crashed or restarted server leaves nothing behind."""
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
        """Reader-thread sink: append to the raw buffer, then fan out to the web
        terminal. The buffer append is locked because completion scanning reads it
        from the caller's thread while this runs.

        Everything the shell produces passes through here -- AI output, human
        typing, echo -- which is precisely why the human view is complete and the
        AI view has to be carved out of it by position.
        """
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

    def run_command(self, command, timeout=DEFAULT_COMMAND_TIMEOUT,
                    interactive=False, idle_ms=INTERACTIVE_IDLE_MS,
                    max_s=INTERACTIVE_MAX_S, expect=None):
        """Run an AI command; scan only output produced AFTER this point so any
        prior manual output is ignored (manual commands invisible to AI detection).

        Default (interactive=False): unchanged token-completion path. Returns dict
        status (completed|running), success, exit_code, output. On timeout status is
        'running' with partial output; caller may send_input / send_interrupt / wait.

        interactive=True: launch on the default accept path (CR) so the exe is NOT
        wrapped -- it attaches to the ConPTY with stdin open -- and wait on the
        interactive primitive instead of the token-only wait. Returns the
        interactive record {state, output, tail, exit_code}. See wait_interactive.
        """
        with self._lock:
            self._scan_pos = len(self._buf)
            self._interactive_start = self._scan_pos
        start = self._scan_pos

        if interactive:
            # Default accept (CR): exe bypasses the wrapper, attaches to the ConPTY
            # with stdin open. Same path humans get in the web terminal.
            pwsh_io.write_line(self.proc, self.token.wrap_command(command))
            return self.wait_interactive(idle_ms=idle_ms, max_s=max_s, expect=expect)

        # Batch accept: write_accept_batch prepends the U+200B sentinel; the Enter
        # handler detects it on submit, sets $global:__mcp_ai_batch, and strips it, so
        # this command's exe(s) take the stdin-closed capture wrapper. The flag is
        # reset by the prompt after the command completes.
        pwsh_io.write_accept_batch(self.proc, self.token.wrap_command(command))
        found, raw, match = self.wait_token(timeout)
        end = self._output_end(raw, start, match) if found else len(raw)
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
        end = self._output_end(raw, start, match) if found else len(raw)
        output = extract_output(raw[start:end], self.token.start_marker)
        if not found:
            return {"status": "running", "success": None,
                    "exit_code": None, "output": output}
        success, code = self.token.parse(match)
        return {"status": "completed", "success": success,
                "exit_code": code, "output": output}

    def _output_end(self, raw, start, match):
        """End of real output = start of the prompt LINE carrying the token, i.e.
        the last newline before the token's OSC (drops the next prompt's base).

        FLOORED at the end of this command's start marker: with EMPTY output
        there is no newline between the marker and the prompt, so an unfloored
        rfind lands on the newline BEFORE the marker, slicing the marker off and
        sending extract_output down the fallback path -- which returned the
        PSReadLine per-keystroke redraw echo as 'output' (the empty-result echo
        bug). With the floor, empty output yields end == marker end, so
        extract_output returns ''. The marker search is bounded to
        [start, token) so a previous command's marker can never match."""
        floor = 0
        mk = raw.rfind(self.token.start_marker, start, match.start())
        if mk != -1:
            floor = mk + len(self.token.start_marker)
        nl = raw.rfind("\n", floor, match.start())
        if nl != -1:
            return nl
        return floor if floor > start else match.start()

    def send_input(self, text):
        """Feed a line to a running/interactive command (e.g. answer Read-Host)."""
        pwsh_io.write_line(self.proc, text)

    # The interactive-input primitive (send_input_interactive, poll_interactive,
    # wait_interactive, and the _interactive_* / _is_prompt_shaped / _proc_exit_code
    # helpers) is provided by InteractiveMixin in pwsh_interactive.py. run_command's
    # interactive branch above calls self.wait_interactive from there.

    def get_raw_buffer(self):
        """Snapshot of the full raw ConPTY buffer (ANSI included). Used by the web
        layer to replay the current screen tail to a newly connected client."""
        with self._lock:
            return self._buf

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
        """Whether the shell process is still running. Defensive by design: a dead
        or half-torn-down pty should report False, not raise."""
        try:
            return self.proc is not None and self.proc.isalive()
        except Exception:
            return False

    # --- internals ----------------------------------------------------------

    def _cleanup_init_script(self):
        """Delete the temp init script written at startup. Best-effort: failing to
        remove a temp file must never break shutdown."""
        if self._init_script and os.path.exists(self._init_script):
            try:
                os.remove(self._init_script)
            except Exception:
                pass
        self._init_script = None
