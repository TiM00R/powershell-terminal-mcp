"""
test_session_output.py - smoke test for SessionOutput (buffer + filter wiring).
Run after setup_venv.ps1:

    .\.venv\Scripts\python.exe test_session_output.py

Plain ASCII. No curl.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from pwsh.session_output import SessionOutput  # noqa: E402


def check(label, ok):
    print(("PASS " if ok else "WARN ") + label)


def main():
    broadcast = {"chunks": 0}

    def on_broadcast(_chunk):
        broadcast["chunks"] += 1

    so = SessionOutput(on_broadcast=on_broadcast, max_lines=10000)

    print("start + settle")
    check("settled", so.start())

    print("simple command: raw + filtered")
    r = so.run_command("Write-Output 'hello world'")
    check("completed exit 0", r["status"] == "completed" and r["exit_code"] == 0)
    check("raw output correct", r["output"].strip() == "hello world")
    check("filtered present", bool(r["filtered"]))
    print("  filtered:", repr(r["filtered"][:80]))

    print("verbose command: filter should reduce")
    r = so.run_command("1..200 | ForEach-Object { \"line $_\" }", timeout=20.0)
    raw_lines = r["output"].count("\n")
    filt_lines = r["filtered"].count("\n")
    check("raw is large", raw_lines > 150)
    check("filtered is smaller than raw", filt_lines < raw_lines)
    print("  raw lines ~", raw_lines, " filtered lines ~", filt_lines)

    print("command type detection (file_listing)")
    r = so.run_command("Get-ChildItem")
    check("gci ran", r["status"] == "completed")

    print("buffer captured full stream")
    stats = so.get_stats()
    check("buffer has many lines", stats["total_lines"] > 50)
    print("  buffer stats:", {k: stats[k] for k in ("total_lines", "total_lines_added")})
    check("broadcast received chunks", broadcast["chunks"] > 0)

    so.close()
    print("Done.")


if __name__ == "__main__":
    main()
