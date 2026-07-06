<!-- mcp-name: io.github.TiM00R/powershell-terminal-mcp -->
# PowerShell Terminal

**Shared AI + user PowerShell session on Windows — execute commands, run scripts, automate your PC**

PowerShell Terminal lets Claude (the AI assistant) run commands in a persistent, interactive PowerShell 7 session on your Windows machine through a real pseudo-terminal (ConPTY). Watch every command stream into your browser in real time while Claude receives smart-filtered output optimized for token efficiency.

---

## 🎯 What Is This?

Imagine telling Claude:

```
"Check what Python version I have and install requests if it's missing"
"Run my build script and tell me if anything failed"
"Find all .log files modified today and show me any errors"
"Save this cleanup script and run it every time I ask"
```

And Claude does it — executing commands in your real PowerShell session, analyzing output, saving reusable scripts, and taking action on your behalf.

**That's PowerShell Terminal.**

---

## ✨ Key Features

### Core Capabilities

- **🖥️ Real PowerShell Session** — Persistent `pwsh` (PS7) or PS5.1 session via ConPTY; state carries across commands (working directory, variables, activated venv)
- **🌐 Shared Human + AI Terminal** — NiceGUI + xterm.js web terminal at `http://localhost:8090`; type your own commands alongside Claude's
- **🔄 Multi-Terminal Sync** — Open multiple browser tabs, all perfectly synchronized
- **🪟 No Popup Windows** — Native console executables (`git`, `python`, `ipconfig`, full paths) run inside ConPTY without spawning new windows
- **✂️ Dual-Stream Output** — You see full output in the browser; Claude receives a token-reduced summary
- **✅ Reliable Completion Detection** — Exit codes and command completion detected via invisible prompt token in OSC escape sequences — no fragile regex matching
- **⌨️ Interactive Commands** — Commands that prompt for input (`Read-Host`) work; Claude can send input and interrupt with Ctrl+C
- **📚 Script Library** — Save and reuse named `.ps1` scripts; full output persisted on script runs
- **🗄️ Command History** — Commands grouped into conversations and logged to SQLite with selective output persistence

### The Interactive Web Terminal

PowerShell Terminal provides a **fully interactive terminal window** in your browser at `http://localhost:8090` — it looks and feels just like a native PowerShell window:

**You can:**
- Type commands directly (just like any terminal)
- Right-click to Copy/Paste, or use Ctrl+Shift+C / Ctrl+Shift+V
- Scroll through the full session scrollback
- Watch every command Claude runs appear in real time

**Claude can:**
- Execute commands that stream into your terminal
- See results instantly
- Continue working while you watch

**The key advantage:** Complete visibility and control. Every command Claude runs appears in your terminal in real time. You're never in the dark — it's like sitting side-by-side with an assistant who types commands while you watch the screen.

**Multi-Terminal Support:** Open multiple browser windows at `http://localhost:8090` — they all stay perfectly synchronized via WebSocket broadcast. Type in one terminal, see it in all terminals instantly.

### The Dual-Stream Architecture

```
        PowerShell Session Output (ConPTY)
                      |
                 [Raw Output]
                      |
             ---------+---------
             |                 |
          [FULL]           [FILTERED]
             |                 |
             v                 v
       Web Terminal          Claude
    (You see all)      (Smart summary)
```

- **You:** Full output, colors, and scrollback in the browser terminal
- **Claude:** Token-efficient filtered summary
- **Both:** Same live PowerShell session, synchronized state

### Native Exe — No Popup Windows

A key problem with running an AI-controlled terminal on Windows: native console executables like `python`, `git`, `ipconfig`, or any `.exe` would spawn a separate `conhost.exe` popup window, breaking the in-terminal experience.

PowerShell Terminal solves this completely:

- A **PostCommandLookupAction hook** intercepts every native CUI executable before it runs
- A **PE header check** distinguishes console apps (CUI) from GUI apps — GUI apps like `notepad` and `code` open normally without blocking
- Execution is handled via `System.Diagnostics.Process` with `CreateNoWindow=true` and redirected I/O, so output flows through ConPTY instead of a new window
- Works for **short names** (`git`, `python`), **full paths** (`D:\tools\ffmpeg.exe`), and **any exe not known in advance**

---

## 🚀 Quick Start

### Requirements

- Windows 10 1809+ or Windows 11 (ConPTY required)
- PowerShell 7 (`pwsh`) recommended; falls back to Windows PowerShell 5.1
- Python 3.10+

### Option A — Install from PyPI

**Step 1: Create a virtual environment**

```powershell
mkdir D:\powershell_terminal
cd D:\powershell_terminal
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Step 2: Install the package**

```powershell
pip install powershell-terminal-mcp
```

**Step 3: Register with Claude Desktop**

```powershell
notepad $env:APPDATA\Claude\claude_desktop_config.json
```

Add:

```json
{
  "mcpServers": {
    "powershell-terminal": {
      "command": "D:\\powershell_terminal\\.venv\\Scripts\\powershell-terminal-mcp.exe"
    }
  }
}
```

---

### Option B — Install from Source (dev)

**Step 1: Clone the repo**

```powershell
git clone https://github.com/TiM00R/powershell-terminal-mcp D:\powershell_terminal
cd D:\powershell_terminal
```

**Step 2: Create the virtual environment and install dependencies**

```powershell
.\setup_venv.ps1
```

This creates `.venv`, installs all dependencies (including `pywinpty`), and installs the project in editable mode.

**Step 3: Register with Claude Desktop**

```powershell
notepad $env:APPDATA\Claude\claude_desktop_config.json
```

Add:

```json
{
  "mcpServers": {
    "powershell-terminal": {
      "command": "D:\\powershell_terminal\\.venv\\Scripts\\python.exe",
      "args": ["D:\\powershell_terminal\\src\\mcp_server.py"]
    }
  }
}
```

---

Fully quit and relaunch Claude Desktop (system tray → Exit), then open a new conversation. On the first tool call the server starts the PowerShell session and opens the web terminal at `http://localhost:8090`.

---

## 💡 Usage Examples

### Running Commands

```
"What Python version do I have?"
"Run git status in D:\myproject"
"List all files modified today in my Downloads folder"
"Check if port 8080 is in use"
```

### Scripts & Automation

```
"Save this build script and run it"
"Run my cleanup script"
"List my saved scripts"
```

### Diagnostics & System Info

```
"Check disk space on all drives"
"What processes are using the most memory?"
"Show me the last 20 lines of the application event log"
"Is the Docker daemon running?"
```

### Development Workflows

```
"Activate the venv and run the tests"
"Install requests and check if it imports correctly"
"Run git log --oneline -10 and summarize what changed"
```

---

## 🎨 Example Session

**You:**
> Run my test suite and tell me what failed

**Claude:**
> I'll run the tests now...

*Web terminal opens automatically at http://localhost:8090*

**Web Terminal shows (real-time):**
```
PS D:\myproject> python -m pytest tests/ -v
============================= test session starts ==============================
collected 42 items

tests/test_api.py::test_login PASSED                                     [  2%]
tests/test_api.py::test_auth_required PASSED                             [  4%]
tests/test_db.py::test_connection FAILED                                 [ 26%]
...
========================= 3 failed, 39 passed in 8.42s =========================
```

**Claude receives (filtered summary):**
```
pytest: 42 tests, 3 failed, 39 passed (8.4s)
Failed: test_connection, test_insert_duplicate, test_rollback
```

**Claude responds:**
> 3 tests failed, all in `test_db.py` — looks like a database connection issue. Want me to look at the error details and fix them?

---

## 🏗️ Architecture

### How It Works

```
Claude  <--stdio JSON-RPC-->  mcp_server.py
                                    |
                           SharedTerminalState
                           /         |         \
                  PwshSession   OutputBuffer   SmartOutputFilter   SQLite (db.py)
                  (ConPTY via    (full human    (AI token-          (conversations,
                   pywinpty)      scrollback)     reduced view)       commands, scripts)
                                    |
                           NiceGUI + xterm.js web terminal (shared, multi-tab)
```

- A single `pwsh` process runs inside a ConPTY (`pywinpty`). A background thread reads its output into a circular buffer.
- The session's prompt is overridden (in the MCP session only) to emit a unique completion token + exit status inside invisible OSC escape sequences. The browser swallows them; the server detects them in the raw stream.
- An output start-marker (emitted via a PSReadLine Enter handler, also invisible) lets the server separate a command's real output from the terminal's echo of the typed command.
- Human keystrokes from the browser are passed through raw and are not tracked by the AI's completion detection, so the two streams never collide.

### Project Structure

```
powershell_terminal/
├── config/
│   └── config.yaml                 # Web port, filter thresholds, error patterns
├── data/
│   └── commands.db                 # SQLite: conversations, commands, scripts
├── scripts/                        # Headless test harnesses
│   ├── test_pwsh_session.py        # Session: completion, exit codes, interactive, Ctrl+C
│   ├── test_session_output.py      # Buffer + dual-stream filter
│   └── test_mcp_dispatch.py        # All MCP tools via direct dispatch
├── src/
│   ├── config/                     # Configuration loading
│   ├── output/                     # Output filtering and buffering
│   ├── pwsh/                       # PowerShell session (ConPTY)
│   │   ├── pwsh_launch.py          # Shell spawn, init script, native exe hook
│   │   ├── pwsh_session.py         # Session lifecycle, run_command, send_input
│   │   ├── session_output.py       # Dual-stream wrapper (raw + filtered)
│   │   └── completion_token.py     # Prompt token and OSC escape injection
│   ├── web/
│   │   └── web_terminal.py         # NiceGUI + xterm.js web terminal
│   ├── db.py                       # SQLite database layer
│   ├── mcp_server.py               # MCP server entry point (all tools)
│   └── shared_state.py             # Global session hub
├── setup_venv.ps1                  # One-command environment setup
└── run_web.py                      # Launch web terminal standalone (no Claude)
```

### Technology Stack

- **Python 3.10+** — Core language
- **MCP Protocol** — Claude integration (stdio JSON-RPC)
- **pywinpty** — ConPTY pseudo-terminal on Windows
- **NiceGUI + WebSockets** — Web terminal with multi-tab sync
- **SQLite** — Command history and script storage
- **xterm.js** — Browser terminal renderer

---

## 🔧 MCP Tools Reference

### Terminal / Execution

| Tool | Description |
|------|-------------|
| `execute_command(command, timeout?)` | Run a command; returns filtered output + exit code. Returns `status: "running"` on timeout. |
| `get_command_output(command_id, raw?)` | Fetch a prior command's output by id. |
| `send_input(text)` | Answer a running interactive command, then wait for completion. |
| `send_interrupt()` | Send Ctrl+C to the running command. |
| `get_terminal_status()` | Session alive? Web terminal URL. |
| `restart_session()` | Kill and respawn the PowerShell session (clears all state). |
| `open_terminal()` | Open (or re-open) the web terminal in the browser. |

### Scripts

| Tool | Description |
|------|-------------|
| `save_script(name, content)` | Save (or overwrite) a named `.ps1` script. |
| `list_scripts()` | List all saved scripts. |
| `run_script(name, timeout?)` | Run a saved script; full output is always persisted. |

### Conversations (History)

| Tool | Description |
|------|-------------|
| `start_conversation(label?)` | Group subsequent commands; returns `conversation_id`. |
| `end_conversation(conversation_id?, status?)` | End the active (or specified) conversation. |
| `list_conversations(limit?)` | List recent conversations. |
| `get_conversation_commands(conversation_id)` | Commands logged under a conversation. |

---

## 🔧 Configuration

`config.yaml` (project root) controls:
- `server.host` / `server.port` — Web terminal address (default `localhost:8090`)
- Output filter thresholds and error patterns
- SQLite database lives at `data\commands.db` in the project root

---

## 🛡️ Security Considerations

- Web terminal bound to `localhost` only — not exposed to the network
- Full command audit trail in SQLite
- The init script runs in the MCP's own session only — **your `$PROFILE`, normal PowerShell, and prompt are never modified**
- Claude runs commands in your local user context with your normal permissions

---

## 🐛 Known Issues & Limitations

1. **Windows only** — ConPTY is a Windows API; Linux/Mac not supported
2. **Interactive TUI apps not supported** — Commands that take over the terminal (e.g. `vim`, `htop`) will hang; use `-NonInteractive` alternatives
3. **Single session** — One shared PowerShell session; no per-command isolation

---

## 🔍 Development

Run the headless test harnesses without Claude Desktop:

```powershell
.\.venv\Scripts\python.exe scripts\test_pwsh_session.py     # session: completion, exit codes, interactive, Ctrl+C, restart
.\.venv\Scripts\python.exe scripts\test_session_output.py   # buffer + dual-stream filter
.\.venv\Scripts\python.exe scripts\test_mcp_dispatch.py     # all MCP tools via direct dispatch
python run_web.py                                            # launch web terminal standalone
```

---

## 📜 Version History

### v0.1.0 (July 2026) — Initial public release

- ✅ ConPTY-based persistent PowerShell 7 session via `pywinpty`
- ✅ Shared human + AI terminal: NiceGUI + xterm.js web terminal, multi-tab WebSocket sync
- ✅ Native exe fix: all CUI executables run inside ConPTY without popup windows (`System.Diagnostics.Process` + `CreateNoWindow=true` + redirected I/O)
- ✅ PostCommandLookupAction hook covers short names, full paths, and any unknown exe
- ✅ PE header subsystem check: GUI apps (notepad, code) bypass the hook and open normally
- ✅ PATHEXT fix: session always gets a correct PATHEXT so bare command names resolve reliably
- ✅ Dual-stream output: full output in browser, token-reduced view for Claude
- ✅ Reliable command completion via prompt token in invisible OSC escape sequences
- ✅ Interactive commands: `Read-Host`, `Ctrl+C`, `send_input`
- ✅ SQLite history: conversations, commands (selective persistence), saved scripts
- ✅ Full MCP tool set: execute, send_input, interrupt, restart, scripts, conversations

---

## 🤝 Contributing

This is Tim's personal project. If you'd like to contribute:

1. Test on your setup and document any issues found
2. Suggest improvements or missing features
3. Share useful scripts you create

---

## 📄 License

MIT

---

**Ready to let Claude run PowerShell for you? Register the MCP server in Claude Desktop and open a new conversation to get started.**

---

**Version:** 0.1.0
**Last Updated:** July 2026
**Maintainer:** Tim
