"""
pwsh_io.py
Low-level writes into the pty. Trivial wrappers kept separate so the session class
stays focused on orchestration.
"""

CTRL_C = "\x03"

# Invisible AI-batch sentinel: a zero-width space (U+200B). Prepended to AI batch
# commands; the Enter handler (see completion_token.prompt_function_snippet)
# detects it via [char]0x200B, sets $global:__mcp_ai_batch, and strips it before
# the command runs. Renders as nothing, so there is no visible marker.
BATCH_SENTINEL = "\u200b"

# Multi-line handling. A batch command that spans lines cannot be sent to the ConPTY
# with its raw LF (\n) separators: interior LF desyncs PSReadLine's multi-line redraw
# (cursor jumps up, lines reverse, the session wedges at a '>>' continuation prompt).
# A human paste does NOT hit this because xterm.js sends line breaks as CR (\r), which
# goes through PSReadLine's normal continuation cleanly. So we replicate the paste:
# convert interior newlines to CR. On top of that, completion detection waits for ONE
# token from ONE accept, so the block must submit as a single unit -- otherwise CR
# separators would accept each top-level statement on its own (multiple tokens, and
# the AI would return after the first). Wrapping the whole block in 'if ($true) { ... }'
# keeps it incomplete (open brace) until the closing '}', so every interior CR is a
# continuation and only the final CR accepts: one submission, one token. An if-block
# runs in the CURRENT scope, so variables/functions defined inside persist in the
# session; and unlike the call operators '. { }' / '& { }' it does NOT reset $?, so
# the completion token's success bool stays accurate when the block's last statement
# fails. The 'if ($true) {' header and '}' footer are the only visible additions; the
# command's own lines render as-is in the readable '>>' block.


def _has_newline(text):
    return ("\n" in text) or ("\r" in text)


def _wrap_multiline(text):
    """Wrap a multi-line command in 'if ($true) { ... }' and use CR separators, so it
    submits as one unit and renders through PSReadLine's clean continuation path
    instead of the LF scramble. The if-block runs in the current scope (variables
    persist) and does not reset $? (success stays accurate), unlike '. { }'. Returns
    the pty-ready payload (no sentinel, no trailing accept CR -- write_accept_batch
    adds those)."""
    body = text.replace("\r\n", "\n").replace("\r", "\n")  # normalize to LF first
    wrapped = "if ($true) {\n" + body + "\n}"
    return wrapped.replace("\n", "\r")  # CR separators, like an xterm paste


def write_line(proc, text):
    """Send a line followed by Enter (CR only). PSReadLine treats CR as Enter; a
    trailing LF would cause a second empty accept (extra prompt / '>>').

    CR takes the default accept path -> the exe is BYPASSED (attached to the ConPTY,
    stdin open). Used by humans (raw passthrough), the AI interactive launch, and
    send_input (input to a running program)."""
    proc.write(text + "\r")


def write_accept_batch(proc, text):
    """Submit an AI *batch* command: prepend the invisible BATCH_SENTINEL and end
    with CR (like Enter). The Enter handler sees the sentinel, sets
    $global:__mcp_ai_batch, and strips it -- so batch commands take the stdin-closed
    capture wrapper while humans (no sentinel) stay on the default bypass path. The
    sentinel is a zero-width space, so nothing visible is added.

    Multi-line commands are wrapped in 'if ($true) { ... }' with CR separators (see the
    module note) so they submit as a single unit and render as a readable '>>' block
    instead of scrambling. Single-line commands take the original path unchanged."""
    payload = _wrap_multiline(text) if _has_newline(text) else text
    proc.write(BATCH_SENTINEL + payload + "\r")


def write_raw(proc, text):
    """Send raw characters (no newline)."""
    proc.write(text)


def send_interrupt(proc):
    """Send Ctrl+C to the foreground command."""
    proc.write(CTRL_C)
