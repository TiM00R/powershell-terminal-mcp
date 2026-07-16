"""
pwsh_interactive.py
--------------------
The INTERACTIVE-INPUT primitive for PwshSession, factored out of pwsh_session.py
(v0.2.0) as a mixin so the core session file stays focused on lifecycle + batch
token completion.

WHY A MIXIN (not a helper object): every method here operates on PwshSession's
own live state - the raw ConPTY buffer (self._buf), its lock (self._lock), the
completion token (self.token), the process handle (self.proc), and the scan/slice
pointers (self._scan_pos, self._interactive_start). Making it a mixin lets these
methods read/advance that state directly, exactly as if they still lived on the
class, with zero plumbing. PwshSession declares `class PwshSession(InteractiveMixin)`.

CONTRACT (see docs/INTERACTIVE-INPUT.md and SPEC.md section 4A):
  wait_interactive(...) -> {state, output, tail, exit_code}
    EXITED         completion token fired (program returned to the PS prompt);
                   exit_code authoritative. Terminal.
    AWAITING_INPUT idle >= idle_ms AND (an expect-pattern matched OR the tail is
                   prompt-shaped). The program is blocked on stdin -> send_input.
    IDLE           idle >= idle_ms but the tail is NOT prompt-shaped: ambiguous
                   (mid-work pause or a partial prompt). Caller should poll() to
                   accumulate more before deciding.
    RUNNING        hit max_s while output was still growing. Partial output; poll().

  IDLE is a LATENCY signal (when to hand the turn back), never an input trigger.
  Completeness is best-effort: the prompt-shaped-tail gate (server) plus a
  re-pollable loop (AI) mean a wrong guess costs a round-trip, not a bad answer.

Requires from PwshSession (the mixed-in class):
  attributes: self._buf, self._lock, self._scan_pos, self._interactive_start,
              self.token, self.proc
  methods:    self._output_end(raw, start, match), self.is_alive()
"""

import re
import time

from pwsh import pwsh_io
from pwsh.output_clean import extract_output
from completion_token import strip_ansi

# --- interactive-input primitive tuning ---------------------------------------
INTERACTIVE_POLL = 0.03        # buffer poll cadence (s); ~ config poll_ms default
INTERACTIVE_IDLE_MS = 600      # default idle window (ms) before tail classification
INTERACTIVE_MAX_S = 30         # default safety cap (s) before returning RUNNING
# Prompt-shaped tail: ends with prompt punctuation (optionally trailing spaces).
# The no-trailing-newline requirement is checked separately in _is_prompt_shaped,
# because "\s*$" alone would also match a tail that ends in a newline.
PROMPT_TAIL_RE = re.compile(r"[:?>#$)\]]\s*$")


class InteractiveMixin:
    """Interactive-input methods for PwshSession. Not usable standalone - it relies
    on PwshSession's buffer/token/pointer state (see module docstring)."""

    # --- public entry points ------------------------------------------------

    def send_input_interactive(self, text, idle_ms=INTERACTIVE_IDLE_MS,
                               max_s=INTERACTIVE_MAX_S, expect=None):
        """Write one line to the ConPTY stdin (CR accept -> goes to the running
        program, not a new wrapped command), then wait on the interactive primitive.
        Returns {state, output, tail, exit_code}."""
        pwsh_io.write_line(self.proc, text)
        return self.wait_interactive(idle_ms=idle_ms, max_s=max_s, expect=expect)

    def poll_interactive(self, idle_ms=INTERACTIVE_IDLE_MS,
                         max_s=INTERACTIVE_MAX_S, expect=None):
        """Accumulate-more: wait on the primitive with NO input written. This is the
        re-poll that makes an early IDLE/RUNNING return safe - the caller can gather
        the rest of the output before answering."""
        return self.wait_interactive(idle_ms=idle_ms, max_s=max_s, expect=expect)

    def wait_interactive(self, idle_ms=INTERACTIVE_IDLE_MS,
                         max_s=INTERACTIVE_MAX_S, expect=None):
        """Wait for the next interactive turn on the shared ConPTY buffer and
        classify it into one of the four states (see module docstring).

        output is the buffer slice accumulated since the last return; tail is the
        last ~120 chars of the ANSI-stripped buffer used for prompt inspection.
        """
        idle_s = max(0.0, idle_ms / 1000.0)
        deadline = time.time() + max_s
        expect_re = re.compile(expect) if expect else None
        with self._lock:
            last_len = len(self._buf)
        last_change = time.time()

        while True:
            time.sleep(INTERACTIVE_POLL)
            with self._lock:
                raw = self._buf
            cur_len = len(raw)

            # EXITED: the completion token rode in on the redrawn PS prompt. Scan
            # from _scan_pos (the token lives inside an OSC, so do NOT ANSI-strip
            # before matching). Slice the command's output up to the prompt line.
            match = self.token.search(raw, self._scan_pos)
            if match:
                self._scan_pos = match.end()
                end = self._output_end(raw, self._interactive_start, match)
                output = extract_output(raw[self._interactive_start:end],
                                        self.token.start_marker)
                self._interactive_start = len(raw)
                _, code = self.token.parse(match)
                return {"state": "EXITED", "output": output,
                        "tail": self._interactive_tail(raw), "exit_code": code}

            # EOF guard: if the shell process itself is gone and the stream is
            # quiescent, return terminal instead of spinning to the max_s cap. A
            # child exe exiting keeps pwsh alive, so this only fires on a dead
            # session (this is the fix for the prototype's 0-length-read spin bug).
            if cur_len == last_len and not self.is_alive():
                return self._interactive_result("EXITED", raw,
                                                exit_code=self._proc_exit_code())

            # Growth resets the idle timer; a full idle window triggers tail
            # classification: expect-match or prompt-shaped -> AWAITING_INPUT, else
            # IDLE (ambiguous, caller re-polls).
            if cur_len != last_len:
                last_len = cur_len
                last_change = time.time()
            elif (time.time() - last_change) >= idle_s:
                tail = self._interactive_tail(raw)
                if (expect_re and expect_re.search(tail)) or self._is_prompt_shaped(tail):
                    return self._interactive_result("AWAITING_INPUT", raw)
                return self._interactive_result("IDLE", raw)

            # Safety cap: still growing at max_s -> RUNNING with partial output.
            if time.time() > deadline:
                return self._interactive_result("RUNNING", raw)

    # --- internal helpers ---------------------------------------------------

    def _interactive_result(self, state, raw, exit_code=None):
        """Build a non-EXITED interactive record and advance the slice pointer so the
        NEXT return only reports output produced after this one."""
        end = len(raw)
        output = self._clean_interactive(raw[self._interactive_start:end])
        self._interactive_start = end
        return {"state": state, "output": output,
                "tail": self._interactive_tail(raw), "exit_code": exit_code}

    def _clean_interactive(self, raw_slice):
        """Clean an interactive buffer slice. If this command's start marker is in
        the slice (the first return after launch), anchor on it to drop the command
        echo; otherwise just strip ANSI and carriage returns (keeps REPL prompt
        lines like '>>>' intact for the AI to read)."""
        if self.token.start_marker in raw_slice:
            return extract_output(raw_slice, self.token.start_marker)
        return strip_ansi(raw_slice).replace("\r", "")

    def _interactive_tail(self, raw, n=120):
        """Last ~n chars of the ANSI-stripped buffer. Stripping removes the invisible
        OSC token too, so real prompt punctuation is exposed for classification."""
        return strip_ansi(raw).replace("\r", "")[-n:]

    def _is_prompt_shaped(self, tail):
        """True when the tail ends with prompt punctuation and has NO trailing
        newline (a trailing newline means output is still flowing, not a prompt)."""
        if not tail or tail.endswith("\n"):
            return False
        return bool(PROMPT_TAIL_RE.search(tail))

    def _proc_exit_code(self):
        """Best-effort process exit code for the EOF-guard terminal case (the token
        path provides the authoritative code; this only covers a dead session)."""
        try:
            return getattr(self.proc, "exitstatus", None)
        except Exception:
            return None
