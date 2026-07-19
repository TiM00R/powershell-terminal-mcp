"""
tool_schemas.py
---------------
MCP tool-schema registry for the PowerShell Terminal server, factored out of
mcp_server.py (v0.2.0) so that file holds the server wiring + dispatch logic and
this file holds the (long, declarative) tool definitions.

build_tool_list() returns the list of mcp.types.Tool objects advertised to the MCP
client via the server's list_tools handler. These are DECLARATIONS ONLY - the actual
behavior for each tool lives in PowerShellTerminalMCP._dispatch (mcp_server.py),
matched by the tool `name`. Keep the names here in sync with the `if name == ...`
branches there.

Only depends on mcp.types (no session / nicegui / stdout side effects), so it is safe
to import at the top of mcp_server.py alongside the other mcp imports.
"""

from mcp import types


def build_tool_list():
    """Return every tool the server exposes, grouped: terminal/execution,
    interactive-input, conversations (history), and script store."""
    return [
        # --- terminal / execution -------------------------------------------
        types.Tool(
            name="execute_command",
            description="Run a PowerShell command in the shared local session. "
                        "Default (batch) returns filtered output plus exit code; "
                        "on timeout returns status 'running' with partial output. "
                        "Set interactive=true to drive a REPL / interactive prompt "
                        "(e.g. python -i, an installer): the native-exe wrapper is "
                        "skipped for that invocation and the call returns a state "
                        "record {state, output, exit_code, tail} where state is "
                        "EXITED | AWAITING_INPUT | IDLE | RUNNING. Follow up with "
                        "send_input (to answer) or poll (to accumulate more).",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "PowerShell command"},
                    "timeout": {"type": "number", "description": "Seconds (default 60); batch mode only"},
                    "interactive": {"type": "boolean", "description": "Opt in to the interactive-input path (default false)"},
                    "idle_ms": {"type": "number", "description": "Interactive: buffer-idle window before classifying the tail"},
                    "max_s": {"type": "number", "description": "Interactive: safety cap before returning RUNNING"},
                    "expect": {"type": "string", "description": "Interactive: optional regex; a tail match forces AWAITING_INPUT"},
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
        # --- interactive-input (states EXITED/AWAITING_INPUT/IDLE/RUNNING) ---
        types.Tool(
            name="send_input",
            description="Send a line of input to a running/interactive command "
                        "(e.g. answer a prompt), then wait on the interactive "
                        "primitive. Returns {state, output, exit_code, tail}.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "idle_ms": {"type": "number"},
                    "max_s": {"type": "number"},
                    "expect": {"type": "string"},
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="poll",
            description="Accumulate more output from a running/interactive command "
                        "WITHOUT sending input. Returns {state, output, exit_code, "
                        "tail}. Use after an IDLE or RUNNING result, or to gather "
                        "more before answering an AWAITING_INPUT prompt that looks "
                        "truncated.",
            inputSchema={
                "type": "object",
                "properties": {
                    "idle_ms": {"type": "number"},
                    "max_s": {"type": "number"},
                    "expect": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="send_interrupt",
            description="Send Ctrl+C to the foreground command.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_terminal_status",
            description="Session alive? plus the web terminal URL.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="restart_session",
            description="Kill and respawn the PowerShell session (clears state).",
            inputSchema={"type": "object", "properties": {}},
        ),
        # --- conversations (history, DB-backed) -----------------------------
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
            name="get_command_history",
            description="Get commands executed within a date/time range, across all "
                        "conversations. from_date/to_date accept 'YYYY-MM-DD' (whole "
                        "day, inclusive) or 'YYYY-MM-DD HH:MM:SS'. Returns command "
                        "rows ordered by time. Use for 'what did we run between X "
                        "and Y' forensics.",
            inputSchema={"type": "object", "properties": {
                "from_date": {"type": "string"},
                "to_date": {"type": "string"}},
                "required": ["from_date", "to_date"]},
        ),
        # --- script store ---------------------------------------------------
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
