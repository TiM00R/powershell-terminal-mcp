"""
mcp_server.py - PowerShell Terminal MCP server (lean v1).

Exposes the local pwsh session to an MCP client. Wires the core tools directly to
the SharedTerminalState hub (PwshSession + buffer + filter + web terminal).
Conversation/script/DB tools come in a later increment.
"""

import asyncio
import sys
import json
import uuid
import logging
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# Redirect stdout to stderr BEFORE importing nicegui (stdout is the JSON-RPC channel).
_original_stdout = sys.stdout
sys.stdout = sys.stderr

logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from config.config_loader import Config           # noqa: E402
from shared_state import get_shared_state          # noqa: E402
from web.web_terminal import WebTerminalServer      # noqa: E402

sys.stdout = _original_stdout


def _txt(s):
    return [types.TextContent(type="text", text=s)]


class PowerShellTerminalMCP:
    def __init__(self):
        self.state = get_shared_state()
        config_file = SCRIPT_DIR.parent / "config.yaml"
        self.config = Config(str(config_file))
        if self.config.server.port == 8080:
            self.config.server.port = 8090
        self.state.initialize(self.config)
        self.web_server = WebTerminalServer(self.state, self.config)
        self.server = Server("powershell-terminal")
        self._outputs = {}   # command_id -> last result dict
        self._started = False
        self._setup()

    def _ensure_started(self):
        if not self._started:
            self.state.start_session()
            self.web_server.start()  # opens the shared web terminal
            self._started = True

    def _setup(self):
        @self.server.list_tools()
        async def list_tools():
            return [
                types.Tool(
                    name="execute_command",
                    description="Run a PowerShell command in the shared local session. "
                                "Returns filtered (token-reduced) output plus exit code. "
                                "On timeout returns status 'running' with partial output.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "PowerShell command"},
                            "timeout": {"type": "number", "description": "Seconds (default 60)"},
                        },
                        "required": ["command"],
                    },
                ),
                types.Tool(
                    name="get_command_output",
                    description="Get output for a previous command_id (raw=true for unfiltered).",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "command_id": {"type": "string"},
                            "raw": {"type": "boolean"},
                        },
                        "required": ["command_id"],
                    },
                ),
                types.Tool(
                    name="send_input",
                    description="Send a line of input to a running/interactive command "
                                "(e.g. answer a prompt), then wait for completion.",
                    inputSchema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),
                types.Tool(
                    name="send_interrupt",
                    description="Send Ctrl+C to the foreground command.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                types.Tool(
                    name="get_terminal_status",
                    description="Session alive?, PowerShell version, web terminal URL.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                types.Tool(
                    name="restart_session",
                    description="Kill and respawn the PowerShell session (clears state).",
                    inputSchema={"type": "object", "properties": {}},
                ),
                types.Tool(
                    name="start_conversation",
                    description="Start a new conversation (groups subsequent commands). "
                                "Returns conversation_id.",
                    inputSchema={"type": "object", "properties": {
                        "label": {"type": "string"}}},
                ),
                types.Tool(
                    name="end_conversation",
                    description="End a conversation (defaults to the active one).",
                    inputSchema={"type": "object", "properties": {
                        "conversation_id": {"type": "integer"},
                        "status": {"type": "string"}}},
                ),
                types.Tool(
                    name="list_conversations",
                    description="List recent conversations (history).",
                    inputSchema={"type": "object", "properties": {
                        "limit": {"type": "integer"}}},
                ),
                types.Tool(
                    name="get_conversation_commands",
                    description="List commands logged under a conversation_id.",
                    inputSchema={"type": "object", "properties": {
                        "conversation_id": {"type": "integer"}},
                        "required": ["conversation_id"]},
                ),
                types.Tool(
                    name="save_script",
                    description="Save (or overwrite) a named PowerShell script (.ps1 content).",
                    inputSchema={"type": "object", "properties": {
                        "name": {"type": "string"}, "content": {"type": "string"}},
                        "required": ["name", "content"]},
                ),
                types.Tool(
                    name="list_scripts",
                    description="List saved scripts.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                types.Tool(
                    name="run_script",
                    description="Run a saved script by name in the session. Full output is "
                                "persisted.",
                    inputSchema={"type": "object", "properties": {
                        "name": {"type": "string"}, "timeout": {"type": "number"}},
                        "required": ["name"]},
                ),
                types.Tool(
                    name="open_terminal",
                    description="Open (or re-open) the web terminal in the browser. "
                                "Starts the web server if not running, then opens a fresh "
                                "browser tab. Use this whenever the user asks to see or open "
                                "the terminal.",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name, arguments):
            try:
                return await self._dispatch(name, arguments or {})
            except Exception as e:
                logger.error("tool %s failed: %s", name, e, exc_info=True)
                return _txt(json.dumps({"error": str(e)}))

    async def _dispatch(self, name, args):
        self._ensure_started()
        loop = asyncio.get_event_loop()

        if name == "execute_command":
            cmd = args["command"]
            timeout = float(args.get("timeout", 60))
            result = await loop.run_in_executor(None, lambda: self.state.run_command(cmd, timeout))
            cid = uuid.uuid4().hex[:12]
            self._outputs[cid] = result
            return _txt(json.dumps({
                "command_id": cid,
                "status": result["status"],
                "exit_code": result["exit_code"],
                "success": result["success"],
                "output": result.get("filtered") or result.get("output", ""),
            }, ensure_ascii=False))

        if name == "get_command_output":
            cid = args["command_id"]
            raw = bool(args.get("raw", False))
            r = self._outputs.get(cid)
            if not r:
                return _txt(json.dumps({"error": "unknown command_id"}))
            key = "output" if raw else ("filtered" if r.get("filtered") else "output")
            return _txt(r.get(key, ""))

        if name == "send_input":
            text = args["text"]
            result = await loop.run_in_executor(None, lambda: self._send_and_wait(text))
            return _txt(json.dumps({
                "status": result["status"],
                "exit_code": result["exit_code"],
                "output": result.get("filtered") or result.get("output", ""),
            }, ensure_ascii=False))

        if name == "send_interrupt":
            self.state.send_interrupt()
            return _txt(json.dumps({"status": "interrupt_sent"}))

        if name == "get_terminal_status":
            url = "http://%s:%s" % (self.config.server.host, self.config.server.port)
            return _txt(json.dumps({
                "alive": self.state.is_alive(),
                "web_url": url,
            }))

        if name == "restart_session":
            ok = await loop.run_in_executor(None, self.state.restart_session)
            return _txt(json.dumps({"restarted": bool(ok)}))

        if name == "start_conversation":
            cid = self.state.start_conversation(args.get("label"))
            return _txt(json.dumps({"conversation_id": cid}))

        if name == "end_conversation":
            cid = self.state.end_conversation(args.get("conversation_id"),
                                              args.get("status", "completed"))
            return _txt(json.dumps({"ended": cid}))

        if name == "list_conversations":
            rows = self.state.list_conversations(int(args.get("limit", 20)))
            return _txt(json.dumps(rows, ensure_ascii=False))

        if name == "get_conversation_commands":
            rows = self.state.get_conversation_commands(int(args["conversation_id"]))
            return _txt(json.dumps(rows, ensure_ascii=False))

        if name == "save_script":
            nm = self.state.save_script(args["name"], args["content"])
            return _txt(json.dumps({"saved": nm}))

        if name == "list_scripts":
            return _txt(json.dumps(self.state.list_scripts(), ensure_ascii=False))

        if name == "run_script":
            timeout = float(args.get("timeout", 120))
            result = await loop.run_in_executor(
                None, lambda: self.state.run_script(args["name"], timeout))
            return _txt(json.dumps({
                "status": result.get("status"),
                "exit_code": result.get("exit_code"),
                "output": result.get("filtered") or result.get("output", ""),
                "error": result.get("error"),
            }, ensure_ascii=False))

        if name == "open_terminal":
            await self.web_server.open_terminal()
            url = "http://%s:%s" % (self.config.server.host, self.config.server.port)
            return _txt(json.dumps({"opened": True, "url": url}))

        raise ValueError("Unknown tool: %s" % name)

    def _send_and_wait(self, text):
        self.state.send_input(text)
        return self.state.wait_more("", timeout=60)

    async def run(self):
        logger.info("PowerShell Terminal MCP ready.")
        try:
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(read_stream, write_stream,
                                      self.server.create_initialization_options())
        finally:
            try:
                self.web_server.stop()
            except Exception:
                pass
            self.state.close_session()


async def main():
    await PowerShellTerminalMCP().run()


if __name__ == "__main__":
    asyncio.run(main())
