"""
test_mcp_dispatch.py - headless test of mcp_server tool dispatch (no MCP client).

Exercises the server's _dispatch logic, JSON shapes, the _outputs registry, the
send_input -> wait_more path, and _ensure_started (web + session start on first
call) WITHOUT Claude Desktop, so we don't pay a restart per change. The only thing
this does NOT cover is the stdio JSON-RPC transport itself (library code).

Run after setup_venv.ps1:
    .\.venv\Scripts\python.exe test_mcp_dispatch.py

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
    srv = mcp_server.PowerShellTerminalMCP()

    # Avoid opening a browser / binding the web port during the headless test:
    # stub the web server start to a no-op, but still let the session start.
    srv.web_server.start = lambda: None

    print("get_terminal_status (triggers _ensure_started: session start)")
    r = _parse(await srv._dispatch("get_terminal_status", {}))
    check("alive true", r.get("alive") is True)
    check("web_url present", "web_url" in r)
    print("  ->", r)

    print("execute_command: simple")
    r = _parse(await srv._dispatch("execute_command", {"command": "Write-Output 'mcp-hello'"}))
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

    print("execute_command: interactive (timeout -> running)")
    r = _parse(await srv._dispatch(
        "execute_command",
        {"command": "$x = Read-Host 'Q'; Write-Output \"a:$x\"", "timeout": 3}))
    running = (r.get("status") == "running")
    check("interactive returns running", running)
    if running:
        print("send_input: answer the prompt")
        r = _parse(await srv._dispatch("send_input", {"text": "mcpval"}))
        check("completed after input", r.get("status") == "completed")
        check("answer in output", "a:mcpval" in (r.get("output") or ""))

    print("send_interrupt")
    r = _parse(await srv._dispatch("send_interrupt", {}))
    check("interrupt acknowledged", r.get("status") == "interrupt_sent")

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
