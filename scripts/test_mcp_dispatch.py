"""
test_mcp_dispatch.py - headless test of mcp_server tool dispatch (no MCP client).

Exercises the server's _dispatch logic, JSON shapes, the _outputs registry, the
send_input -> wait_more path, and _ensure_owner (web + session start on first
call) WITHOUT Claude Desktop, so we don't pay a restart per change. The only thing
this does NOT cover is the stdio JSON-RPC transport itself (library code).

Run after setup_venv.ps1:
    .\.venv\Scripts\python.exe scripts/test_mcp_dispatch.py

Plain ASCII. No curl.
"""

import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

# Import the server module and disable the browser/web auto-open so the headless
# test does not spawn a browser or bind the web port.
import mcp_server  # noqa: E402


def check(label, ok):
    print(("PASS " if ok else "WARN ") + label)


def _parse(result):
    """call_tool handlers return [TextContent]; pull the text and JSON-parse it."""
    text = result[0].text
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text}


async def main():
    # Start the shell the way some Claude hosts do, with PSModulePath empty,
    # so the Windows-modules check below covers that case.
    os.environ["PSModulePath"] = ""
    srv = mcp_server.PowerShellTerminalMCP()

    # Avoid opening a browser / binding the web port during the headless test:
    # stub the web server start to a no-op, but still let the session start.
    srv.web_server.try_start = lambda **kw: (True, None)

    print("get_terminal_status (read-only: must NOT start the session / claim the port)")
    r = _parse(await srv._dispatch("get_terminal_status", {}))
    check("not started by status", r.get("alive") is False)
    check("web_url present", "web_url" in r)
    print("  ->", r)

    print("execute_command: simple (triggers _ensure_owner: session start)")
    r = _parse(await srv._dispatch("execute_command", {"command": "Write-Output 'mcp-hello'"}))
    check("alive after first shell tool", srv.state.is_alive())
    check("status completed", r.get("status") == "completed")
    check("exit 0", r.get("exit_code") == 0)
    check("output contains hello", "mcp-hello" in (r.get("output") or ""))
    cid = r.get("command_id")
    check("command_id returned", bool(cid))
    print("  ->", {k: r.get(k) for k in ("command_id", "status", "exit_code")})

    print("get_command_output: by id")
    r = _parse(await srv._dispatch("get_command_output", {"command_id": cid}))
    check("retrieved output", "mcp-hello" in r.get("_raw", "") if "_raw" in r else True)
    r2 = await srv._dispatch("get_command_output", {"command_id": cid, "raw": True})
    check("raw output retrieved", "mcp-hello" in r2[0].text)

    print("get_command_output: unknown id")
    r = _parse(await srv._dispatch("get_command_output", {"command_id": "nope"}))
    check("unknown id reports error", "error" in r)

    print("execute_command: failing exit code")
    r = _parse(await srv._dispatch("execute_command", {"command": "cmd /c exit 7"}))
    check("exit 7 captured", r.get("exit_code") == 7)
    r = _parse(await srv._dispatch("execute_command", {"command": "Write-Output 'after-7'"}))
    check("next command does not inherit exit 7", r.get("exit_code") == 0)

    print("execute_command: interactive (timeout -> running)")
    r = _parse(await srv._dispatch(
        "execute_command",
        {"command": "$x = Read-Host 'Q'; Write-Output \"a:$x\"", "timeout": 3}))
    running = (r.get("status") == "running")
    check("interactive returns running", running)
    if running:
        print("send_input: answer the prompt")
        r = _parse(await srv._dispatch("send_input", {"text": "mcpval"}))
        # send_input returns the interactive record: {state, ...}, not status.
        check("completed after input", r.get("state") == "EXITED")
        check("answer in output", "a:mcpval" in (r.get("output") or ""))

    print("tail: no repeated command echo")
    r = _parse(await srv._dispatch("execute_command",
        {"command": "$n = Read-Host 'Your name'; \"Hello $n\"", "interactive": True}))
    check("waiting for input", r.get("state") == "AWAITING_INPUT")
    check("tail is just the prompt", r.get("tail") == "Your name: ")
    r = _parse(await srv._dispatch("send_input", {"text": "Tim"}))
    check("tail has no typed command", "Read-Host" not in (r.get("tail") or ""))
    check("tail has the answer", "Hello Tim" in (r.get("tail") or ""))

    print("send_interrupt: nothing running")
    r = _parse(await srv._dispatch("send_interrupt", {}))
    check("reports stopped", r.get("stopped") is True)

    print("busy: a new command is not typed into a running one")
    r = _parse(await srv._dispatch("execute_command",
        {"command": "Start-Sleep 20", "interactive": True, "max_s": 1, "idle_ms": 400}))
    check("sleep still running", r.get("state") in ("IDLE", "RUNNING"))
    r = _parse(await srv._dispatch("execute_command", {"command": "Write-Output 'too-early'"}))
    check("refused as busy", r.get("status") == "busy" and "tail" in r)
    r = _parse(await srv._dispatch("run_script", {"name": "anything"}))
    check("run_script refused as busy", r.get("status") == "busy")
    r = _parse(await srv._dispatch("send_interrupt", {}))
    check("Ctrl+C stopped Start-Sleep", r.get("stopped") is True)
    r = _parse(await srv._dispatch("execute_command", {"command": "Write-Output 'probe'"}))
    check("next command runs", r.get("status") == "completed")
    check("next command has only its own output", (r.get("output") or "").strip() == "probe")
    check("'too-early' never ran", "too-early" not in (r.get("output") or ""))

    print("send_interrupt force: a program that ignores Ctrl+C")
    r = _parse(await srv._dispatch("execute_command",
        {"command": "pwsh -NoProfile -Command '[Console]::TreatControlCAsInput = $true; "
                    "while ($true) { Start-Sleep -Milliseconds 200 }'",
         "interactive": True, "max_s": 2, "idle_ms": 1000}))
    check("stubborn program running", r.get("state") in ("IDLE", "RUNNING"))
    r = _parse(await srv._dispatch("send_interrupt", {}))
    check("plain Ctrl+C reports not stopped", r.get("stopped") is False and "message" in r)
    r = _parse(await srv._dispatch("send_interrupt", {"force": True}))
    check("force stopped it", r.get("stopped") is True)
    check("force names the ended program", "pwsh.exe" in [n.lower() for n in r.get("ended", [])])
    r = _parse(await srv._dispatch("execute_command", {"command": "Write-Output 'after-force'"}))
    check("session works after force", (r.get("output") or "").strip() == "after-force")

    print("Windows modules found despite an empty PSModulePath")
    r = _parse(await srv._dispatch("execute_command",
        {"command": "(Get-Command Test-NetConnection, Resolve-DnsName).Name -join ','"}))
    check("Test-NetConnection and Resolve-DnsName found",
          "Test-NetConnection,Resolve-DnsName" in (r.get("output") or ""))

    print("restart_session")
    r = _parse(await srv._dispatch("restart_session", {}))
    check("restarted true", r.get("restarted") is True)
    r = _parse(await srv._dispatch("execute_command", {"command": "Write-Output 'after-restart'"}))
    check("works after restart", "after-restart" in (r.get("output") or ""))

    print("unknown tool errors cleanly")
    try:
        await srv._dispatch("does_not_exist", {})
        check("unknown tool raised", False)
    except ValueError:
        check("unknown tool raised", True)

    print("conversations: start / list / commands / end")
    r = _parse(await srv._dispatch("start_conversation", {"label": "disp-test"}))
    conv_id = r.get("conversation_id")
    check("conversation started", isinstance(conv_id, int))
    await srv._dispatch("execute_command", {"command": "Write-Output 'logged-cmd'"})
    r = _parse(await srv._dispatch("get_conversation_commands", {"conversation_id": conv_id}))
    check("command logged under conversation", isinstance(r, list) and len(r) >= 1)
    r = _parse(await srv._dispatch("list_conversations", {"limit": 5}))
    check("conversation listed", any(c.get("id") == conv_id for c in r))
    r = _parse(await srv._dispatch("end_conversation", {}))
    check("conversation ended", r.get("ended") == conv_id)

    print("scripts: save / list / run")
    r = _parse(await srv._dispatch("save_script",
        {"name": "hello", "content": "Write-Output 'from-saved-script'"}))
    check("script saved", r.get("saved") == "hello")
    r = _parse(await srv._dispatch("list_scripts", {}))
    check("script listed", any(s.get("name") == "hello" for s in r))
    r = _parse(await srv._dispatch("run_script", {"name": "hello"}))
    check("script ran", "from-saved-script" in (r.get("output") or ""))
    r = _parse(await srv._dispatch("run_script", {"name": "nope"}))
    check("unknown script reports error", bool(r.get("error")))

    srv.state.close_session()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
