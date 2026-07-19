"""
output_clean.py
Separate an AI command's real output from ConPTY's echo of the typed command.

The start marker is emitted INSIDE an invisible OSC sequence
(ESC ] 7001 ; MCP_OUT_START:<uuid> BEL) right before the user command. Every echo
occurrence of the marker (during typing) precedes the OSC's runtime occurrence, so
the marker's last occurrence anchors the start of real output. We operate on the
RAW slice (so the OSC survives), skip the OSC terminator, then ANSI-strip the
remainder for clean AI/DB text.
"""

from completion_token import strip_ansi


def extract_output(raw_slice, start_marker):
    """Return real command output from a RAW (un-stripped) buffer slice."""
    idx = raw_slice.rfind(start_marker)
    if idx != -1:
        after = raw_slice[idx + len(start_marker):]
        # Skip the OSC terminator right after the marker (BEL or ST).
        after = after.lstrip("\x07")
        if after.startswith("\x1b\\"):
            after = after[2:]
        return _normalize(strip_ansi(after).lstrip("\r\n"))
    # Marker not present yet (rare). Best-effort: strip ANSI + drop echo lines.
    return _normalize(strip_ansi(_strip_echo_fallback(raw_slice)))


def _normalize(text):
    """Drop carriage returns so the AI/DB get clean \\n-only text."""
    return text.replace("\r", "")


def _strip_echo_fallback(text):
    """Last-resort cleanup when the marker is missing: drop PSReadLine's '>>'
    continuation lines, which are the most obvious echo artifact. Cruder than the
    marker path and only reached in rare cases."""
    lines = text.split("\n")
    kept = [ln for ln in lines if not ln.lstrip().startswith(">>")]
    return "\n".join(kept).strip("\r\n")
