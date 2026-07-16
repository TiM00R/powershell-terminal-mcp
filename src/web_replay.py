"""
web_replay.py
-------------
On-connect screen-replay tail builder for the web terminal, factored out of
shared_state.py (v0.2.0). When a web client connects (a reopened tab, a refresh,
open_terminal after a disconnect) it must be sent the CURRENT screen so it is not
blank -- see SPEC.md section 5A and README "Terminal Reconnect / History Replay".

This module owns the two self-contained concerns of that feature:
  1. Stripping terminal QUERY/RESPONSE escape sequences from the replayed bytes
     (_REPLAY_STRIP), so the reconnecting xterm does not reply into the shell's
     stdin and corrupt the next command.
  2. Turning the raw ConPTY buffer into the reset-prefixed tail to send, honoring
     the replay_lines mode (build_replay_tail).

Kept as a pure function: shared_state reads config + the raw buffer and calls in
here, so the hub stays free of the escape-sequence detail.
"""

import re

# Terminal query/response sequences to strip from the RAW replay tail. Replaying a
# QUERY (DA `ESC[c`, DSR `ESC[6n`, DECRQM `ESC[?..$p`, XTVERSION `ESC[>q`, OSC color
# `ESC]..;?`) makes the reconnecting xterm emit a REPLY into the shell's stdin, which
# gets glued onto the next command (seen as `ESC[?1;2c` in front of a command ->
# ParserError). We remove these but KEEP SGR (colors) and cursor-movement/CR redraws,
# so the replay is still colored and the per-keystroke redraws still overwrite.
_REPLAY_STRIP = re.compile(
    "\x1b\\[[0-9;?>=]*[cnR]"                          # DA / DSR / CPR
    "|\x1b\\[\\?[0-9;]*\\$[py]"                       # DECRQM / DECRPM
    "|\x1b\\[>[0-9;]*q"                               # XTVERSION
    "|\x1b\\][0-9]*;\\?[^\x07\x1b]*(?:\x07|\x1b\\\\)"  # OSC color/query
)


def build_replay_tail(raw, max_lines, max_bytes):
    """Build the reset-prefixed replay tail from the raw ConPTY buffer.

    The result is RAW (ANSI intact) so colors AND the carriage-return line redraws
    render exactly as they did live; the caller (web layer) prepends a screen reset
    before writing it to the new socket, and this adds a leading SGR reset (ESC[0m)
    to clear any dangling color state.

    max_lines mode (server.replay_lines):
      < 0  -> FULL buffer from session start. Faithful and cursor-safe (it is the
              exact byte stream xterm already processed); ignores max_bytes.
      == 0 -> current prompt line only: no history, but still a usable prompt.
      > 0  -> last N physical lines, byte-capped by max_bytes. Fast/small, but a cut
              landing inside stateful output (TUI, alt-screen) can render imperfectly.

    Returns '' when there is nothing to show.
    """
    if not raw:
        return ""
    raw = raw.replace("\u200b", "")            # drop the invisible batch sentinel
    if max_lines < 0:
        text = raw                             # full buffer (faithful, cursor-safe)
    elif max_lines == 0:
        text = raw.split("\n")[-1]             # current prompt line only
    else:
        text = "\n".join(raw.split("\n")[-max_lines:])  # \n never inside an escape
        if max_bytes > 0 and len(text) > max_bytes:
            text = text[-max_bytes:]
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1:]           # re-align to a line boundary
    text = _REPLAY_STRIP.sub("", text)          # drop query/response sequences
    return "\x1b[0m" + text
