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
- **🌐 Shared Human + AI Terminal** — xterm.js web terminal at `http://localhost:8090`; type your own commands alongside Claude's
- **🔄 Multi-Terminal Sync** — Open multiple browser tabs, all perfectly synchronized
- **🔀 Multiple Claude Sessions** — Desktop chats and Claude Code sessions each keep their own shell; the web terminal shows whichever one used it last, and switching back restores that shell exactly as it was (screen, folder, variables)
- **📁 Opens in Your Project** — Claude Code sessions start the shell in their project folder; the tab title shows who owns the terminal (e.g. `Claude Code (Desktop app) · myproject`)
- **🪟 No Popup Windows** — Native console executables (`git`, `python`, `ipconfig`, full paths) run inside ConPTY without spawning new windows
- **✂️ Dual-Stream Output** — You see full output in the browser; Claude receives a token-reduced summary
- **✅ Reliable Completion Detection** — Exit codes and command completion detected via invisible prompt token in OSC escape sequences — no fragile regex matching
- **📝 Multi-Line Commands** — Send readable multi-line blocks (functions, loops, here-strings, piped chains); they execute as a single unit and render as a proper `>>` continuation block in the terminal, with variables persisting across the session
- **⌨️ Interactive Programs** — REPLs, installers, and prompts (`python -i`, `node`, `ftp`, `Read-Host`) work end-to-end: Claude drives them turn-by-turn via a state machine (`AWAITING_INPUT` / `IDLE` / `RUNNING` / `EXITED`) with `send_input`, `poll`, and Ctrl+C. Interactive programs you type yourself in the web terminal work too.
- **🚀 Auto-Open Terminal** — The web terminal opens automatically on any command when no browser tab is connected; minimized tabs stay connected and are left alone, and open tabs are reused when another session takes over
- **🔁 Reconnect Replay** — Reopen or refresh the web terminal and the current screen is restored **in color**, instead of a blank tab
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

### Terminal Reconnect / History Replay

When you reopen the web terminal (a reopened tab, a refresh, or `open_terminal()` after a disconnect), the new tab is sent the **current screen contents** so it isn't blank — the prompt, recent commands, and their output are restored **in color**, with the cursor correctly placed. This includes commands *you* typed, not just Claude's.

Why it's needed: the browser only receives *new* output from the moment it connects, so before this a reopened tab was blank until you pressed Enter. On connect the server now replays the session's screen buffer (terminal query/response escape sequences are stripped, so the reconnecting terminal can't inject a stray reply into your next command).

Configured under `server:` in `config.yaml`:

| Setting | Behavior |
|---------|----------|
| `replay_lines: -1` | **Full buffer** from session start — faithful and cursor-safe (default). |
| `replay_lines: 0` | Current prompt line only — no history, but still a usable prompt. |
| `replay_lines: N` | Last **N** lines (byte-capped by `replay_max_bytes`). |
| `replay_max_bytes` | Byte cap for the `N > 0` mode (ignored when `-1`). |

Full replay is faithful because it re-feeds the exact byte stream the terminal already processed. A very long/noisy session makes reconnect parse more (seconds at most); switch to a bounded `N` if that ever matters.

### Multiple Claude Sessions (Take-Over)

Every Claude client runs its own copy of this server: the Desktop app has one shared by all its chats, and each Claude Code session (CLI or the Desktop app's Code tab) has its own. Each copy has its **own PowerShell**, but there is only one web terminal port, so the rule is **latest user wins**:

- When a session runs a command and another copy owns the port, it asks that copy to hand over, then takes the port. Open tabs show *"Terminal taken over by another Claude session, reconnecting…"* for a moment and switch to the new session's shell — no new tab opens.
- The previous owner **keeps its shell running in the background**. A command it was running finishes normally, and when you use that chat again it takes the terminal back with its screen, folder, and variables intact.
- Only tools that use the shell (`execute_command`, `send_input`, `poll`, `send_interrupt`, `restart_session`, `run_script`, `open_terminal`) take the terminal over. Read-only tools (status, history, script list) never pull it away from the chat you are watching.
- The tab title and header show the current owner: `Claude Desktop`, `Claude Code · <project>` (CLI), or `Claude Code (Desktop app) · <project>`.
- One tab is reused throughout: asking to open the terminal again does not add tabs, as long as that tab is visible. If it is hidden (a background tab, minimized window, or a browser pane you are not looking at), a new tab opens and the hidden one is told to close. When a Claude session ends the tab closes itself; if the server is killed, it closes after 5 seconds without a reconnect. (If your browser refuses to let a page close its tab, the tab says the session ended instead.)

**Start folder.** Claude Code sessions report their project folder (MCP *roots*), and a new shell opens there. The Desktop chat has no project and opens in `POWERSHELL_TERMINAL_HOME`, or your home folder if that is unset. A shell that is already running keeps its current folder.

**How the hand-over is protected.** The owner writes `%LOCALAPPDATA%\powershell-terminal\owner-<port>.json` (process id + random token). A hand-over request must carry that token in a custom header, so only a local process that can read the file can trigger it — a web page in your browser cannot. If the port is held by an unrelated program, commands still run, and tool results include a `web_warning` explaining why the web terminal is unavailable.

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
- The program starts in the shell's **current folder**, so `cd D:\repo; git status` works however the server itself was launched

### Multi-Line Commands

Claude can send **multi-line command blocks** — functions, `foreach` / `if` blocks, here-strings, or long piped chains — and they run as a single unit while rendering as a readable continuation block in your terminal:

```
PS D:\> if ($true) {
>> $sum = 0
>> 1..10 | ForEach-Object { $sum += $_ }
>> $sum
>> }
55
```

Under the hood, a multi-line command is wrapped in an `if ($true) { ... }` block and submitted with carriage-return line separators — the same way a human paste enters the shell. This gives:

- **One submission, one result** — the whole block completes as a single command with one exit code, so output capture stays clean (no partial/early return)
- **Variables persist** — the block runs in the current scope, so any variables or functions defined inside remain available to later commands
- **Accurate success** — an `if` block (unlike the `. { }` / `& { }` call operators) does not reset `$?`, so a failure in the block's last statement is reported correctly instead of masked
- **Readable on screen** — the block shows as a normal `>>` continuation block instead of scrambling, reordering, or wedging the terminal

Why it is needed: sending raw line-feed (`\n`) separators to the ConPTY desynchronizes PowerShell's multi-line redraw (lines reverse, the cursor jumps, the session sticks at `>>`). Matching what a paste does — carriage returns plus a single-submission wrapper — avoids that entirely.

Single-line commands are unchanged. If you prefer one line, semicolons (`;`) still work. The `if ($true) {` header and `}` footer are the only additions the wrapper makes to the on-screen echo.

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
      "command": "D:\\powershell_terminal\\.venv\\Scripts\\powershell-terminal-mcp.exe",
      "env": {
        "POWERSHELL_TERMINAL_HOME": "D:\\powershell_terminal"
      }
    }
  }
}
```

`POWERSHELL_TERMINAL_HOME` (optional) sets the working root: your editable `config.yaml` lives there (copied from the packaged default on first run; upgrades never overwrite it), and sessions without a project folder start there. If unset, the config is placed in `%USERPROFILE%\.powershell-terminal\` and those sessions start in `%USERPROFILE%`. Claude Code sessions start in their own project folder instead (see [Multiple Claude Sessions](#multiple-claude-sessions-take-over)).

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
      "args": ["D:\\powershell_terminal\\src\\mcp_server.py"],
      "env": {
        "POWERSHELL_TERMINAL_HOME": "D:\\powershell_terminal"
      }
    }
  }
}
```

When running from a source checkout, `config.yaml` at the repo root is used directly; `POWERSHELL_TERMINAL_HOME` still sets the start folder for sessions without a project folder.

The same entry can be registered for Claude Code (user scope, so every Code session can use it) with `claude mcp add --scope user powershell-terminal -- D:\powershell_terminal\.venv\Scripts\python.exe D:\powershell_terminal\src\mcp_server.py`.

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
                    Starlette/uvicorn + xterm.js web terminal (shared, multi-tab)
```

- A single `pwsh` process runs inside a ConPTY (`pywinpty`). A background thread reads its output into a circular buffer.
- The session's prompt is overridden (in the MCP session only) to emit a unique completion token + exit status inside invisible OSC escape sequences. The browser swallows them; the server detects them in the raw stream.
- An output start-marker (emitted via a PSReadLine Enter handler, also invisible) lets the server separate a command's real output from the terminal's echo of the typed command.
- Human keystrokes from the browser are passed through raw and are not tracked by the AI's completion detection, so the two streams never collide.
- Each Claude client runs its own copy of the server with its own shell; only the copy that owns the port serves the web terminal, and ownership moves to whichever copy used the shell last (`web/ownership.py`).

### Project Structure

```
powershell_terminal/
├── config.yaml                     # Web port, filter thresholds, error patterns, DB retention
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
│   │   ├── pwsh_interactive.py     # Interactive-input state machine (mixin)
│   │   └── session_output.py       # Dual-stream wrapper (raw + filtered)
│   ├── static/                     # terminal.js, terminal.css, favicon, xterm.js vendor files
│   ├── web/
│   │   ├── web_terminal.py         # Web server (Starlette/uvicorn), start/stop, release endpoint
│   │   ├── web_terminal_ui.py      # Terminal page HTML
│   │   ├── web_terminal_websocket.py  # Tab connections, broadcast, hand-over notice
│   │   ├── ownership.py            # Which copy owns the port; hand-over requests
│   │   └── eventlog.py             # Optional diagnostic event log (server.event_log)
│   ├── completion_token.py         # Prompt token and OSC escape injection
│   ├── db.py                       # SQLite database layer
│   ├── mcp_server.py               # MCP server entry point (all tools)
│   ├── shared_state.py             # Global session hub (session + execution path)
│   └── state_history.py            # DB facade: logging, conversations, history, scripts
├── db_admin.py / db-admin.ps1      # DB maintenance CLI (list/show/prune/vacuum/delete)
├── setup_venv.ps1                  # One-command environment setup
└── run_web.py                      # Launch web terminal standalone (no Claude)
```

### Technology Stack

- **Python 3.10+** — Core language
- **MCP Protocol** — Claude integration (stdio JSON-RPC)
- **pywinpty** — ConPTY pseudo-terminal on Windows
- **Starlette + uvicorn + WebSockets** — Web terminal with multi-tab sync (restartable in-process, which the session take-over needs)
- **SQLite** — Command history and script storage
- **xterm.js** — Browser terminal renderer

---

## 🔧 MCP Tools Reference

### Terminal / Execution

| Tool | Description |
|------|-------------|
| `execute_command(command, timeout?, interactive?, idle_ms?, max_s?, expect?)` | Run a command. Batch (default): filtered output + exit code, `status: "running"` on timeout. `interactive: true`: drive a REPL/prompt — returns `{state, output, exit_code, tail}` where state is `EXITED` / `AWAITING_INPUT` / `IDLE` / `RUNNING`. A multi-line `command` runs as a single block (shown as a `>>` continuation block; variables persist). While Claude's previous command is still running, nothing is sent: it returns `status: "busy"` with the last screen lines (`tail`), so a new command is never typed into a running program. |
| `get_command_output(command_id, raw?)` | Fetch a prior command's output by id. |
| `send_input(text, idle_ms?, max_s?, expect?)` | Send a line to a running/interactive program, then wait; returns `{state, output, exit_code, tail}`. |
| `poll(idle_ms?, max_s?, expect?)` | Accumulate more output from a running/interactive command **without** sending input. |
| `send_interrupt(force?)` | Send Ctrl+C and report whether the program stopped (`{stopped, tail}`). `force: true` ends the programs the shell started, for one that ignores Ctrl+C (e.g. `ftp` stuck connecting); the PowerShell session itself stays, but `Start-Job` jobs end too. |
| `get_terminal_status()` | Session alive? Web terminal URL, whether this session owns it (`web_owner`), and `web_warning` if it is unavailable. Read-only: never takes the terminal over. |
| `restart_session()` | Kill and respawn the PowerShell session (clears all state; reopens in the project folder if known). |
| `open_terminal(reopen?)` | Show the web terminal. Reuses an open tab you can see (its header briefly shows "Terminal requested by …"); otherwise opens a new tab. A tab that is hidden (background tab, minimized window, hidden browser pane) is not reused: it is told to close and a new one opens. `reopen: true` (only when you ask, e.g. "reopen the terminal" or "I can't find the terminal tab") closes the open terminal tab(s) and opens one fresh tab in front. Also happens automatically on any command when no tab is connected. |

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
| `get_command_history(from_date, to_date)` | Commands across all conversations in a date/time range (`YYYY-MM-DD` whole day, or `YYYY-MM-DD HH:MM:SS`). |

---

## 🔧 Configuration

**Config file location** (first match wins):
1. Source checkout: `config.yaml` at the repo root (dev mode)
2. `%POWERSHELL_TERMINAL_HOME%\config.yaml` — your editable copy, created from the packaged default on the first run of a pip install
3. If `POWERSHELL_TERMINAL_HOME` is unset: `%USERPROFILE%\.powershell-terminal\config.yaml`

Edit your copy freely — package upgrades never overwrite it. Restart Claude Desktop after changes.

`config.yaml` controls:
- `server.host` / `server.port` — Web terminal address (default `localhost:8090`)
- `server.replay_lines` — On-connect screen replay: `-1` full buffer (default), `0` prompt only, `N` last N lines
- `server.replay_max_bytes` — Byte cap for the `N > 0` replay mode
- `server.event_log` — Diagnostic log of take-overs and tab events to `%LOCALAPPDATA%\powershell-terminal\web.log` (~2 MB cap); off by default, turn on to troubleshoot extra or missing tabs
- `interactive.idle_ms` / `interactive.max_s` / `interactive.poll_ms` — Interactive-command tuning (defaults `600` / `30` / `30`)
- Output filter thresholds (keyed by command type: `install`, `system_info`, `network`, `file_listing`, `file_viewing`, `log_search`, `generic`) and PowerShell error patterns
- `database.path` — Override the SQLite location (default `data\commands.db`, relative to the install); set an absolute path to pin it across installs / working directories
- `database.retention_days` — Startup auto-prune: drop conversations whose last activity is older than N days (default `30`, `0` disables); the active conversation is never pruned

---

## 🛡️ Security Considerations

- Web terminal bound to `localhost` only — not exposed to the network
- Session hand-over requires a per-owner secret token sent in a custom header; browser pages cannot trigger it
- Full command audit trail in SQLite
- The init script runs in the MCP's own session only — **your `$PROFILE`, normal PowerShell, and prompt are never modified**
- Claude runs commands in your local user context with your normal permissions

---

## 🐛 Known Issues & Limitations

1. **Windows only** — ConPTY is a Windows API; Linux/Mac not supported
2. **Interactive TUI apps not supported** — Commands that take over the terminal (e.g. `vim`, `htop`) will hang; use `-NonInteractive` alternatives
3. **`ssh` password auth via Claude** — Windows OpenSSH reads its password from the console (CONIN$), not stdin, so `ssh` doesn't prompt through the interactive path (silent exit 255). Use key auth / `BatchMode`, a scripted client (`ftp -s:`), or type the password yourself in the web terminal. stdin-based tools (`python -i`, `node`, `ftp`, `Read-Host`) work.
4. **One shell per Claude client** — All chats in the Desktop app share one shell; each Claude Code session has its own. Each client that has used the terminal keeps an idle `pwsh` (roughly 60–100 MB) until that client closes. Two sessions used at the same time keep taking the web terminal from each other (their shells are unaffected)
5. **Incomplete multi-line commands hang** — A multi-line command with unbalanced braces, parentheses, or quotes stays at the `>>` continuation prompt until it times out (returned as `status: "running"`) and must be cleared with `send_interrupt()`. This is inherent to PowerShell's continuation model, not specific to this tool — send syntactically complete blocks.

---

## 🔍 Development

Run the headless test harnesses without Claude Desktop:

```powershell
.\.venv\Scripts\python.exe scripts\test_pwsh_session.py     # session: completion, exit codes, interactive, Ctrl+C, restart
.\.venv\Scripts\python.exe scripts\test_session_output.py   # buffer + dual-stream filter
.\.venv\Scripts\python.exe scripts\test_mcp_dispatch.py     # all MCP tools via direct dispatch
.\.venv\Scripts\python.exe run_web.py 8095                  # web terminal standalone on a spare port (open the URL yourself)
```

---

## 📜 Version History

### v0.4.0 (November 2026) — Multiple Claude sessions, project start folder, web server without NiceGUI

- ✅ **Multiple Claude sessions share the web terminal** — previously a second Claude client (another Claude Code session, or Desktop + Code) ran commands in its own shell while the web page kept showing the first client's shell, and it opened a new browser tab on almost every command. Now the session that used the shell last owns the web terminal: the previous owner hands over on request, open tabs show a short notice and reconnect, and no extra tabs open.
- ✅ **Switching back restores the session** — the previous owner keeps its shell running in the background, so screen history, folder, variables, and any running command survive a take-over.
- ✅ **Read-only tools don't take over** — status, history, and script listing never pull the web terminal away from another chat.
- ✅ **Project start folder** — Claude Code sessions (CLI and the Desktop app's Code tab) open the shell in their project folder, read via MCP roots; Desktop chats are unchanged.
- ✅ **Owner in the tab title** — e.g. `PowerShell Terminal — Claude Code (Desktop app) · myproject`, updated live on take-over; new `>_` tab icon.
- ✅ **Fix: native programs ignored `cd`** — `git`, `python`, `npm`, etc. run by Claude started in the server's launch folder (e.g. `C:\Windows\System32`) instead of the shell's current folder; they now follow `cd`.
- ✅ **Fix: web server bind failure went unnoticed** — when the port was taken, the server thread died silently while the app still believed it was running.
- ✅ **Web server rewritten on Starlette + uvicorn** — NiceGUI is no longer a dependency (smaller install, one connection per tab, server can stop and restart in-process). Same page, same look.
- ✅ **One terminal tab, reused** — `open_terminal` always reuses the open tab and opens one only when none is connected; previously, switching between two sessions stacked up tabs. Its reply now clearly says the existing tab was reused, so Claude no longer opens the URL itself and creates a duplicate tab. Only a visible tab is reused; `reopen: true` replaces a tab you lost among others.
- ✅ **Optional diagnostic event log** — `server.event_log` (off by default) records take-overs and tab events to `web.log`, to troubleshoot extra or missing tabs.
- ✅ **Fix: commands typed into a running program** — a new command sent while an interactive program (e.g. `ftp`) was still running became that program's input, and the program's late output was reported as the new command's output. Such a command is now refused with `status: "busy"`.
- ✅ **Ctrl+C reports whether it worked** — `send_interrupt` returns `stopped`; `force: true` ends a program that ignores Ctrl+C without restarting the session.
- ✅ **Fix: Windows modules missing** — `Test-NetConnection`, `Resolve-DnsName` and other built-in Windows modules were not found when Claude started the server with an empty `PSModulePath`; Windows' own module folder is now always on the module path.
- ✅ **Fix: repeated command in `tail`** — the `tail` of interactive results showed the typed command several times over (`ftp speedtest.teleftp speedtest.tele2…`), because each redraw of the line being typed was kept. `tail` now starts after the submitted command, so it shows only what is on screen since then (e.g. `Your name: `).
- ✅ **Fix: stale exit codes** — a PowerShell command run after a failed program reported that program's exit code (e.g. `"probe"` → `exit_code: 1`). The exit code is now reset before each Claude command.
- ✅ **No progress bars in Claude's output** — progress bars (e.g. `Test-NetConnection`, `Invoke-WebRequest`) were redrawn into the text Claude reads and could bury the real output. They are now hidden for Claude's commands only; commands you type in the browser still show them, and your own `$ProgressPreference` is restored after each Claude command.
- ✅ **Tab closes when the session ends** — on a normal Claude quit the tab closes itself right away; if the server is killed it retries for 5 s, then closes. Scrollbar is now always visible in Edge/Chrome.

### v0.3.1 (July 2026) -- Config file now ships with pip installs

- `config.yaml` is now included in the wheel (packaged inside `src/`).
- On first run, the default config is copied to `%POWERSHELL_TERMINAL_HOME%\config.yaml`
  (or `%USERPROFILE%\.powershell-terminal\` if unset). Edit that copy; upgrades never overwrite it.
- Previously, pip installs shipped no config file and silently ran on built-in defaults.
- Web terminal now auto-opens on any command when no browser tab is connected (no more manual "open terminal" after Claude restart). Minimized tabs stay connected and do not retrigger.

### v0.3.0 (July 2026) — Windows-native config, command-history range queries, internal cleanup

- ✅ **Windows/PowerShell output filter** — `error_patterns` rewritten for PowerShell / .NET / Windows console error text (e.g. `is not recognized`, `CategoryInfo`, `CommandNotFoundException`). Fixed a threshold-key bug where `network` and `log_search` commands silently fell back to the `generic` threshold (`network_info` → `network`, added `log_search`).
- ✅ **New tool: `get_command_history(from_date, to_date)`** — retrieve commands across all conversations within a date/time range (`YYYY-MM-DD` for a whole day, or `YYYY-MM-DD HH:MM:SS`).
- ✅ **Database config + retention** — `database.path` pins the SQLite file across installs / working directories; `database.retention_days` auto-prunes old conversations on startup (default `30`, `0` disables). The active conversation is never pruned.
- ✅ **Persistent active conversation** — a server restart now reuses the newest active conversation instead of starting a fresh one each time; a new conversation is created only on first run or when you explicitly start one.
- ✅ **DB maintenance CLI** — `db-admin.ps1` / `db_admin.py` for occasional upkeep: `list`, `show`, `clean-stale`, `delete-ids`, `delete-commands`, `delete-script`, `prune`, `vacuum`, `integrity`. Destructive commands are dry-run by default (require `--yes`).
- ✅ **Single-source config + correct defaults** — the app now uses one `config.yaml` at the repo root; a stale duplicate `config/config.yaml` (which the build was shipping instead of the real one) was removed. The web-terminal port default is `8090` everywhere, replacing an `8080→8090` override that also silently ignored a user's explicit port. Packaging fixed: `pyproject.toml` / `MANIFEST.in` no longer reference the removed `config/` package or nonexistent prototype files.
- ✅ **Remote-terminal leftover sweep** — corrected artifacts carried over from the prototype fork: the shipped `claude_desktop_config_example.json` (was registering `remote-terminal` with the wrong executable and `REMOTE_TERMINAL_ROOT` env var), a stale "SSH output" docstring in the web layer, and the SSH/paramiko fork-migration wording in `setup_venv.ps1`.
- ✅ **Internal cleanup** — removed vestigial remote-terminal configuration (9 unused config sections + the dead `output_modes` block); split `shared_state.py`'s DB layer into a `state_history.py` mixin; consolidated the `utils` / `output` package facades; removed dead modules, unused imports, and a stale package `__init__` carried over from the prototype.

### v0.2.0 (July 2026) — Interactive operation, multi-line commands, reconnect replay

- ✅ **Interactive command operation** — Claude can drive interactive programs (REPLs, installers, prompting tools like `python -i`, `node`, `ftp`, `Read-Host`) turn-by-turn. A state machine returns `{state, output, exit_code, tail}` with states `EXITED` / `AWAITING_INPUT` / `IDLE` / `RUNNING`; `execute_command` gains `interactive` / `idle_ms` / `max_s` / `expect`, `send_input` now waits on the same primitive, and a new `poll` tool accumulates more output. Per-step latency dropped from the old 60 s token timeout to sub-second (tool-side).
- ✅ **Human interactive programs fixed** — The native-exe wrapper now bypasses (keeps stdin open) **by default**, so interactive programs you type yourself in the web terminal (`ftp`, `python -i`, …) prompt correctly instead of dying on EOF. AI *batch* commands still run wrapped (stdin closed, output captured) — marked invisibly by a zero-width sentinel, so nothing shows in the shared terminal and batch behavior is unchanged.
- ✅ **Multi-line commands** — Multi-line command blocks now execute reliably and display as a readable `>>` continuation block instead of scrambling/reordering the terminal or wedging at `>>`. Each block is wrapped in an `if ($true) { }` and submitted with carriage-return separators (matching how a paste enters the shell), so it runs as one command — one exit code, clean output capture, variables persist in the session, and `$?`/`success` stays accurate (an `if` block doesn't reset `$?` the way `. { }` / `& { }` do). The Enter handler now only strips the batch sentinel / emits the output marker on a real submit, leaving interior continuation lines untouched (the prior scramble cause). Single-line commands are unchanged; `;` still works if you prefer one line.
- ✅ **Terminal reconnect / history replay** — Reopening the web terminal restores the current screen in color (prompt + recent history, including your own typed commands) instead of a blank tab. Configurable via `server.replay_lines` (`-1` full / `0` prompt / `N` lines) and `server.replay_max_bytes`; terminal query/response escapes are stripped so a reconnect can't corrupt the next command.

> **Breaking:** `send_input` now returns `{state, output, exit_code}` instead of the previous token-shaped result. The native-exe wrapper default flipped from wrap to bypass (AI batch opts in via the sentinel).

### v0.1.2 (July 2026) — Web terminal fix

- ✅ Duplicate tab fix: opening the terminal on a fresh session no longer spawns two browser tabs. `start()` now launches the server only; `open_terminal()` is the single place that opens a tab, and it always closes any existing tabs before opening exactly one. The output broadcast loop was hardened alongside this rework so a freshly opened tab reliably shows output.

### v0.1.1 (July 2026) — Bug fixes

- ✅ Native exe output routing: native console output (`git`, `python`, `netstat`, `ipconfig`, etc.) now flows through the PowerShell success stream instead of `Write-Host`, restoring pipelines (`native | Select-String`), variable capture (`$x = native`), and file redirection (`native > file`). The hidden-window `ProcessStartInfo` behavior is unchanged, so native exes still run without popup windows.
- ✅ Output extraction: quoted-argument and empty-result commands now return clean or empty output instead of echoing the typed input; the captured region is bounded to the current command's start marker.

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

**Version:** 0.4.0
**Last Updated:** November 2026
**Maintainer:** Tim
