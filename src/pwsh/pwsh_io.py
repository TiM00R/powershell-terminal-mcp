"""
pwsh_io.py
Low-level writes into the pty. Trivial wrappers kept separate so the session class
stays focused on orchestration.
"""

CTRL_C = "\x03"


def write_line(proc, text):
    """Send a line followed by Enter (CR only). PSReadLine treats CR as Enter; a
    trailing LF would cause a second empty accept (extra prompt / '>>')."""
    proc.write(text + "\r")


def write_raw(proc, text):
    """Send raw characters (no newline)."""
    proc.write(text)


def send_interrupt(proc):
    """Send Ctrl+C to the foreground command."""
    proc.write(CTRL_C)
