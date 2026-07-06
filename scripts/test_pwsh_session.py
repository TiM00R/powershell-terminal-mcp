"""
test_pwsh_session.py - standalone smoke test for the PwshSession classes.
Mirrors the spike probes but against the production modules. NOT a unit test;
run it by hand after setup_venv.ps1:

    .\.venv\Scripts\python.exe test_pwsh_session.py

Plain ASCII. No curl.
"""

import os
import sys
import time

# Make src importable (internal imports are top-level: pwsh.*, completion_token).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from pwsh.pwsh_session import PwshSession  # noqa: E402


def check(label, ok):
    print(("PASS " if ok else "WARN ") + label)


def main():
    captured = {"chunks": 0}

    def on_output(_chunk):
        captured["chunks"] += 1

    s = PwshSession(on_output=on_output)

    print("PROBE 1/2: start + settle (spawn, encoding, prompt token)")
    settled = s.start()
    check("session settled", settled)
    if not settled:
        print("Cannot continue; session did not settle.")
        s.close()
        return

    print("PROBE: simple command")
    r = s.run_command("Write-Output 'hello from session'")
    check("hello completed exit 0", r["status"] == "completed" and r["exit_code"] == 0)
    print("  output:", repr(r["output"].strip()[:80]))

    print("PROBE 3: failing command exit code")
    r = s.run_command("cmd /c exit 5")
    check("exit code 5 captured", r["exit_code"] == 5)

    print("PROBE 4: interactive Read-Host")
    r = s.run_command("$n = Read-Host 'Name'; Write-Output \"got:$n\"", timeout=3.0)
    waiting = (r["status"] == "running")
    check("token withheld while waiting for input", waiting)
    if waiting:
        s.send_input("session-user")
        r2 = s.wait_more(timeout=8.0)
        check("input delivered and completed", "got:session-user" in r2["output"])

    print("PROBE 5: Ctrl+C then restart")
    s.run_command("Start-Sleep -Seconds 30", timeout=1.0)  # returns 'running'
    s.send_interrupt()
    back = s.wait_more(timeout=6.0)
    check("Ctrl+C returned control", back["status"] == "completed")

    ok = s.restart()
    check("respawned cleanly", ok)
    r = s.run_command("Write-Output 'respawn-ok'")
    check("post-restart command works", r["exit_code"] == 0)

    print("on_output chunks captured:", captured["chunks"])
    s.close()
    print("Done.")


if __name__ == "__main__":
    main()
