"""
mcp_server.py - PowerShell Terminal MCP server.

The outermost layer: it speaks MCP to the client (Claude Desktop) over stdio and
translates each tool call into a method on SharedTerminalState. It owns no terminal
logic of its own -- everything it does is argument handling, JSON shaping, and
keeping the blocking session work off the event loop.

Two details here are load-bearing:
  - stdout IS the JSON-RPC channel, so it is redirected to stderr around the
    imports; a stray print from a dependency would otherwise corrupt the protocol.
  - Session startup is lazy (_ensure_owner), so the shell and web terminal only
    appear when a tool is actually used, not when the client loads the server.
"""

import asyncio
import sys
import os
import json
import shutil
import threading
import time
import uuid
import logging
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# Redirect stdout to stderr BEFORE importing the web stack (stdout is the JSON-RPC channel).
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
from web import ownership                           # noqa: E402
from web import eventlog                            # noqa: E402
from web.eventlog import log_event                  # noqa: E402
from tool_schemas import build_tool_list            # noqa: E402  (tool declarations)

sys.stdout = _original_stdout


def _resolve_config_path():
    """Locate config.yaml: source checkout first, then the user copy under
    POWERSHELL_TERMINAL_HOME (created from the packaged default on first run)."""
    dev_cfg = SCRIPT_DIR.parent / "config.yaml"
    if dev_cfg.exists():
        return dev_cfg
    home = os.environ.get("POWERSHELL_TERMINAL_HOME")
    root = Path(home) if home else Path.home() / ".powershell-terminal"
    root.mkdir(parents=True, exist_ok=True)
    user_cfg = root / "config.yaml"
    if not user_cfg.exists():
        packaged = SCRIPT_DIR / "config.yaml"
        if packaged.exists():
            shutil.copy(packaged, user_cfg)
            logger.info("First run: copied default config to %s", user_cfg)
    return user_cfg


# Tools that need this process's shell. Calling one takes the web terminal over
# from another copy of this server if needed (latest user wins).
SHELL_TOOLS = {
    "execute_command", "send_input", "poll", "send_interrupt",
    "restart_session", "run_script", "open_terminal",
}


# MCP clientInfo.name -> readable name for the web terminal title.
CLIENT_NAMES = {
    "claude-ai": "Claude Desktop",                       # Desktop chat tab
    "claude-code": "Claude Code",                        # CLI in a terminal
    "local-agent-mode": "Claude Code (Desktop app)",     # Desktop Code tab
}


def _file_uri_to_path(uri):
    """file:///D:/proj -> D:\\proj (also file://server/share -> \\\\server\\share).
    None for anything that is not a file URI."""
    from urllib.parse import urlparse, unquote
    from urllib.request import url2pathname
    p = urlparse(uri)
    if p.scheme != "file":
        return None
    path = url2pathname(unquote(p.path))
    if p.netloc and p.netloc.lower() != "localhost":
        path = "\\\\" + p.netloc + path
    return path or None


def _txt(s):
    """Wrap a string in the content shape MCP expects, so every tool branch below
    can end in one short line."""
    return [types.TextContent(type="text", text=s)]


class PowerShellTerminalMCP:
    """Binds the MCP protocol surface to the shared terminal session.

    Holds the tool dispatch table and the per-command result cache that makes
    get_command_output possible after a filtered reply.
    """

    def __init__(self):
        """Assemble the server: load config, build the session hub and web server,
        and register the MCP handlers. Nothing is started yet -- see
        _ensure_owner.

        _outputs caches each command's full result by id, which is what lets the AI
        ask for raw output later after receiving a token-reduced version.
        """
        self.state = get_shared_state()
        config_file = _resolve_config_path()
        logger.info("Loading config from: %s", config_file)
        self.config = Config(str(config_file))
        self.state.initialize(self.config)
        eventlog.configure(self.config.server.event_log)
        self.web_server = WebTerminalServer(self.state, self.config)
        self.web_server.on_release = self._release
        self.server = Server("powershell-terminal")
        self._outputs = {}   # command_id -> last result dict
        self._started = False
        self._own_lock = threading.RLock()
        self._web_warning = None   # why the web terminal is unavailable, if it is
        self._project_dir = None   # client's project folder (MCP roots), if any
        self._project_checked = False
        self._setup()

    def _ensure_owner(self) -> bool:
        """Make sure this process owns the web terminal and has a live shell.

        Runs before every tool call. Startup stays lazy (merely loading the server
        never spawns PowerShell or a browser tab). When another copy of this
        server holds the port -- another Claude chat or Claude Code session -- we
        ask it to release (latest user wins), then bind.

        Returns True when we just took the port over from another copy.
        """
        with self._own_lock:
            # Shell first, port second: tabs that reconnect after a take-over
            # then find a ready shell and switch once, instead of seeing a blank
            # screen followed by the new shell's own clear + banner.
            if not self._started:
                self.state.start_session(start_dir=self._project_dir)
                self._started = True
            took_over = False
            if not self.web_server.is_running():
                took_over = self._acquire_port()
            return took_over

    def _acquire_port(self) -> bool:
        """Bind the web port, asking its current owner to release if needed."""
        host, port = self.config.server.host, self.config.server.port
        ok, err = self.web_server.try_start()
        if ok:
            self._web_warning = None
            return False
        accepted, reason = ownership.request_release(host, port)
        log_event("release_sent", accepted=accepted, reason=reason)
        if accepted:
            start = time.time()
            deadline = start + 10
            tries = 0
            while time.time() < deadline:
                time.sleep(0.3)
                tries += 1
                ok, err = self.web_server.try_start()
                if ok:
                    self._web_warning = None
                    logger.info("Took over the web terminal on port %s", port)
                    log_event("took_over", tries=tries, elapsed=time.time() - start)
                    return True
            reason = "previous owner did not free the port in time (%s)" % err
        self._web_warning = ("Web terminal unavailable: %s. Commands still run in "
                             "this session's own shell." % reason)
        logger.warning(self._web_warning)
        return False

    def _release(self):
        """Another copy asked for the port: hand over the web terminal only.

        The shell keeps running in the background (a running command finishes
        normally, and folder, variables and screen history are kept), so the
        next tool call here takes the port back and the tab shows this shell
        exactly as it was.
        """
        with self._own_lock:
            self.web_server.notify_handover()
            self.web_server.stop()
            self.state.clear_output_queue()
            log_event("handed_over")
            logger.info("Handed the web terminal over to another session "
                        "(shell kept running)")

    def _setup(self):
        """Register the two MCP handlers. Declarations and behavior are kept apart:
        schemas live in tool_schemas, behavior in _dispatch."""
        # list_tools advertises the tool SCHEMAS (declarations live in
        # tool_schemas.build_tool_list); call_tool routes each invocation to
        # _dispatch, which holds the behavior. Keep tool names in sync across both.
        @self.server.list_tools()
        async def list_tools():
            """Advertise the available tools to the client."""
            return build_tool_list()

        @self.server.call_tool()
        async def call_tool(name, arguments):
            """Route one invocation to _dispatch, converting any exception into an
            error payload so a failed tool never breaks the protocol session."""
            try:
                if not self._project_checked:
                    await self._fetch_project_dir()
                if not self.web_server.owner_label:
                    self.web_server.owner_label = self._client_label()
                    eventlog.set_client(self.web_server.owner_label)
                return await self._dispatch(name, arguments or {})
            except Exception as e:
                logger.error("tool %s failed: %s", name, e, exc_info=True)
                return _txt(json.dumps({"error": str(e)}))

    async def _dispatch(self, name, args):
        """Map a tool name to session work and shape the JSON reply.

        Every blocking call is pushed through run_in_executor: the session methods
        wait on a real process, and running them inline would stall the event loop
        and the whole MCP connection with it.

        Note the two response shapes -- batch tools return status/exit_code/success,
        interactive ones return the state machine's {state, tail, ...}. Command
        output is returned filtered by default, with the full text retrievable by
        command_id.
        """
        loop = asyncio.get_event_loop()

        # Only tools that use the shell claim the terminal. Read-only tools
        # (status, history, script list) must not pull the web terminal away
        # from the chat the user is watching.
        took_over = False
        if name in SHELL_TOOLS:
            took_over = await loop.run_in_executor(None, self._ensure_owner)
            log_event("tool", name=name, took_over=took_over,
                      owner=self.web_server.is_running(),
                      tabs=self.web_server.has_connected_clients())

        # Auto-open the web terminal if no browser tab is watching. After a
        # take-over, first give the previous owner's tabs a moment to reconnect
        # to us, so an existing tab is reused instead of a new one opening.
        if (name in SHELL_TOOLS and name != "open_terminal"
                and self.web_server.is_running()
                and not self.web_server.has_connected_clients()):
            if not took_over:
                await self.web_server.open_terminal(
                    reason="auto-open, not a take-over, no tab connected")
            elif not await self.web_server.wait_for_client(6.0):
                await self.web_server.open_terminal(
                    reason="auto-open, no tab reconnected after take-over")

        if name in ("execute_command", "run_script"):
            # Never type a new command into a program that is still running:
            # it would become that program's input, and the program's late
            # output would be credited to the new command.
            busy = await loop.run_in_executor(None, self.state.busy_tail)
            if busy is not None:
                return _txt(json.dumps({
                    "status": "busy",
                    "error": "The previous command is still running, so nothing "
                             "was sent. Answer it with send_input, wait with poll, "
                             "or stop it with send_interrupt (force=true if Ctrl+C "
                             "does not stop it).",
                    "tail": busy,
                }, ensure_ascii=False))

        if name == "execute_command":
            cmd = args["command"]
            if bool(args.get("interactive", False)):
                ic = self.config.interactive
                idle_ms = args.get("idle_ms", ic.idle_ms)
                max_s = args.get("max_s", ic.max_s)
                expect = args.get("expect")
                result = await loop.run_in_executor(
                    None, lambda: self.state.run_command_interactive(
                        cmd, idle_ms=idle_ms, max_s=max_s, expect=expect))
                cid = uuid.uuid4().hex[:12]
                self._outputs[cid] = result
                return _txt(json.dumps({
                    "command_id": cid,
                    "state": result.get("state"),
                    "exit_code": result.get("exit_code"),
                    "output": result.get("output", ""),
                    "tail": result.get("tail", ""),
                    **self._warning_field(),
                }, ensure_ascii=False))
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
                **self._warning_field(),
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
            ic = self.config.interactive
            idle_ms = args.get("idle_ms", ic.idle_ms)
            max_s = args.get("max_s", ic.max_s)
            expect = args.get("expect")
            result = await loop.run_in_executor(
                None, lambda: self.state.send_input_interactive(
                    text, idle_ms=idle_ms, max_s=max_s, expect=expect))
            return _txt(json.dumps({
                "state": result.get("state"),
                "exit_code": result.get("exit_code"),
                "output": result.get("output", ""),
                "tail": result.get("tail", ""),
            }, ensure_ascii=False))

        if name == "poll":
            ic = self.config.interactive
            idle_ms = args.get("idle_ms", ic.idle_ms)
            max_s = args.get("max_s", ic.max_s)
            expect = args.get("expect")
            result = await loop.run_in_executor(
                None, lambda: self.state.wait_interactive(
                    idle_ms=idle_ms, max_s=max_s, expect=expect))
            return _txt(json.dumps({
                "state": result.get("state"),
                "exit_code": result.get("exit_code"),
                "output": result.get("output", ""),
                "tail": result.get("tail", ""),
            }, ensure_ascii=False))

        if name == "send_interrupt":
            force = bool(args.get("force", False))
            r = await loop.run_in_executor(None, lambda: self.state.interrupt(force))
            reply = {"stopped": r["stopped"], "tail": r["tail"]}
            if force:
                reply["ended"] = r["ended"]
            if not r["stopped"]:
                reply["message"] = ("The program did not stop. " +
                                    ("It may still be closing; poll to check."
                                     if force else
                                     "If it is stuck, call send_interrupt with "
                                     "force=true to end it."))
            return _txt(json.dumps(reply, ensure_ascii=False))

        if name == "get_terminal_status":
            return _txt(json.dumps({
                "alive": self.state.is_alive(),
                "web_url": self.web_server.url,
                "web_owner": self.web_server.is_running(),
                **self._warning_field(),
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

        if name == "get_command_history":
            rows = self.state.get_command_history(args["from_date"], args["to_date"])
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
            if not self.web_server.is_running():
                return _txt(json.dumps({
                    "status": "unavailable", "url": self.web_server.url,
                    "message": "The web terminal could not be shown (see web_warning). "
                               "Commands still run. Do not open the URL yourself.",
                    **self._warning_field()}))
            # Reuse an open tab, but only one the user can see: a hidden tab
            # (background tab, minimized window, a browser pane nobody looks
            # at) would leave the user with no terminal. Right after a
            # take-over the previous owner's tab is still reconnecting (it
            # pauses ~1.5 s to show its notice), so give it a moment first.
            if bool(args.get("reopen", False)):
                # User lost the tab among others: close every open terminal
                # tab (open_terminal tells them to step aside) and open one
                # fresh tab, which the browser brings to the front.
                connected, _ = self.web_server.client_counts()
                await self.web_server.open_terminal(reason="reopen requested")
                return _txt(json.dumps({
                    "status": "shown", "tab": "reopened", "url": self.web_server.url,
                    "message": "Closed %d open terminal tab(s) and opened a fresh one "
                               "in front. Done." % connected}))
            if took_over and not self.web_server.has_connected_clients():
                await self.web_server.wait_for_client(6.0)
            if self.web_server.has_connected_clients():
                await self.web_server.wait_for_visible(1.0)
            connected, visible = self.web_server.client_counts()
            log_event("open_terminal_check", connected=connected, visible=visible)
            if visible:
                who = self.web_server.owner_label or "Claude"
                await self.web_server.notice("Terminal requested by %s" % who)
                # Worded as a plain success: a reply like "opened: false" made
                # Claude think the tool failed and open the URL itself (extra tab).
                return _txt(json.dumps({
                    "status": "shown", "tab": "existing", "url": self.web_server.url,
                    "message": "The web terminal is already open in the user's browser; "
                               "that tab was reused and its header flashed. Done. "
                               "Do not open the URL yourself."}))
            if connected:
                reason = "open_terminal, %d tab(s) connected but none visible" % connected
                message = ("Opened the web terminal in a new browser tab (the "
                           "existing tab was not visible). Done.")
            else:
                reason = "open_terminal, no tab connected"
                message = "Opened the web terminal in a new browser tab. Done."
            await self.web_server.open_terminal(reason=reason)
            return _txt(json.dumps({
                "status": "shown", "tab": "new", "url": self.web_server.url,
                "message": message}))

        raise ValueError("Unknown tool: %s" % name)

    async def _fetch_project_dir(self):
        """Ask the client for its project folder (MCP 'roots'), once.

        Used to open the shell in the project and to name it in the tab title.
        Skipped for the Desktop chat (no project; one shared copy for all chats)
        and for clients without roots support. Never fails a tool call: any
        problem or a slow answer (> 2 s) just leaves the default behaviour."""
        self._project_checked = True
        try:
            session = self.server.request_context.session
            params = session.client_params
            name = ((params.clientInfo.name if params else "") or "").lower()
            if name.startswith("claude-ai"):
                return
            if not (params and params.capabilities and params.capabilities.roots):
                logger.info("client %r does not offer roots; using default start folder", name)
                return
            result = await asyncio.wait_for(session.list_roots(), timeout=2.0)
            for root in result.roots:
                path = _file_uri_to_path(str(root.uri))
                if path and os.path.isdir(path):
                    self._project_dir = path
                    logger.info("project folder from client roots: %s", path)
                    return
            logger.info("client roots had no usable folder: %s",
                        [str(r.uri) for r in result.roots])
        except Exception as e:
            logger.info("could not get client roots (%s); using default start folder", e)

    def _client_label(self):
        """Who this copy serves, for the web terminal's tab title: the MCP
        client's name (sent at connect), plus the project folder for Claude Code
        so two Code sessions can be told apart."""
        try:
            info = self.server.request_context.session.client_params.clientInfo
            raw = (info.name or "").strip()
        except Exception:
            return ""
        key = raw.lower()
        if key.endswith("-powershell-terminal"):
            key = key[:-len("-powershell-terminal")]
            raw = raw[:-len("-powershell-terminal")]
        pretty = CLIENT_NAMES.get(key, raw)
        # Only the CLI starts in the project folder; the Desktop app starts its
        # servers in C:\Windows\System32, so a folder there would be meaningless.
        if key in ("claude-code", "local-agent-mode"):
            if self._project_dir:
                folder = os.path.basename(self._project_dir.rstrip("\\/"))
            elif key == "claude-code":
                folder = os.path.basename(os.getcwd().rstrip("\\/"))
            else:
                folder = ""
            if folder:
                pretty = "%s · %s" % (pretty, folder)
        logger.info("MCP client: %r -> label %r", raw, pretty)
        return pretty

    def _warning_field(self):
        """{'web_warning': ...} when the web terminal is unavailable, else {}."""
        return {"web_warning": self._web_warning} if self._web_warning else {}

    async def run(self):
        """Serve MCP over stdio until the client disconnects, then shut the terminal
        down. The finally block matters: without it a closed client would leave an
        orphaned PowerShell process and web server behind.
        """
        logger.info("PowerShell Terminal MCP ready.")
        try:
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(read_stream, write_stream,
                                      self.server.create_initialization_options())
        finally:
            log_event("shutdown")
            try:
                # Tabs close themselves on this notice instead of waiting to
                # reconnect to a server that is gone.
                self.web_server.notify_shutdown()
                self.web_server.stop()
            except Exception:
                pass
            self.state.close_session()


async def main():
    """Entry point used by the console script and by __main__."""
    await PowerShellTerminalMCP().run()


if __name__ == "__main__":
    asyncio.run(main())
