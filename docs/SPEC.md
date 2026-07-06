# powershell-terminal-mcp - SPECIFICATION

Status: DRAFT (pre-implementation). No application code written yet.
Project folder: D:\powershell_terminal
Copied from: remote-terminal-mcp (SSH) at D:\RodsProj\remote_terminal
Pristine original (recovery source): D:\RodsProj\remote_terminal

================================================================================
## 1. PURPOSE

An MCP server that drives a single persistent, fully interactive local PowerShell 7
session via a Windows pseudo-terminal (ConPTY). Both the AI and the human operate
the SAME live shell. All output is captured to an in-memory circular buffer; the
human sees the full stream in a browser xterm.js terminal and can type into it; the
AI receives a filtered, token-reduced stream so long, noisy output never floods
context.

Local-execution sibling of remote-terminal-mcp. Reuses that project's high-value
parts (web terminal, output buffer, output filter, MCP scaffold, DB layer) and
replaces SSH-specific machinery with local ConPTY execution.

================================================================================
## 2. KEY DECISIONS (all settled)

- PowerShell 7 (pwsh) shell. Launch: pwsh -NoLogo, PROFILE LOADED. Fallback to
  powershell.exe (5.1) only if pwsh missing. Shell path is a config value.
- Single persistent ConPTY session via pywinpty. Session state (cwd, variables,
  modules, venv/conda) persists because it is one long-lived shell.
- Fully interactive human web terminal (handles interactive prompts), not
  fire-and-forget.
- Completion detection: IN-SESSION PROMPT OVERRIDE + appended stable token. No
  per-command injected command. Section 4.
- Manual (human-typed) commands: RAW PASSTHROUGH, invisible to AI completion
  detection. AI scanning starts at the line where the AI injected its command.
- One web terminal only. Standalone UI (8081) DROPPED.
- Recipes DROPPED. Replaced by a script store (save/list/run .ps1).
- SQLite self-contained (stores output text, not buffer pointers). Selective
  full-output persistence. Section 6.
- No secret/password scrubbing. EXPLICIT NON-GOAL (single user, own machine). This
  was the biggest time-sink in the SSH project; removed entirely.
- Full resource + identity isolation from remote-terminal-mcp. Sections 9, 10.

================================================================================
## 3. EXECUTION MODEL

One persistent pwsh process inside a ConPTY (pywinpty). A background reader thread
continuously drains ConPTY output into the circular buffer and broadcasts to web
clients.

(a) HUMAN input (typed in any web terminal):
    - Raw passthrough: keystrokes -> ConPTY stdin; output -> buffer -> broadcast.
    - No completion detection. Not tracked by AI completion logic.

(b) AI input (execute_command / run_script):
    - Command written to ConPTY stdin.
    - Completion detected via prompt token (section 4).
    - Output between "AI command sent" and the next prompt token is THIS command's
      output, sliced from the buffer via in-memory line tracking.
    - Exit status read from the token.

Interactive AI commands (command prompts for input): token will not appear until it
returns, so the tool reports graceful timeout with partial output. AI then inspects
and uses send_input / send_interrupt.

================================================================================
## 4. COMPLETION DETECTION (prompt override + token)

Why not port SSH prompt detection directly: the local prompt is dynamic
(Shorten-Path changes with path length; venv/conda activation prepends a colored
"(env) " prefix). The SSH prompt had a stable anchor ("user@host:") surviving path
and venv changes; the local prompt has only a weak trailing ">". SSH prompt
detection already broke once on venv activation and needed a regex patch. Locally it
would be worse (dynamic path + ANSI color in the prompt).

Solution: the MCP spawns its OWN pwsh session, so override the prompt INSIDE that
session only (the user's real $PROFILE / prompt untouched elsewhere). Session prompt
= the user's existing prompt (Shorten-Path + venv prefix, unchanged, fully visible)
WITH an appended stable token carrying exit status.

Conceptual session prompt:

    <user's normal prompt>  PWSH_MCP_READY:<sessionUuid>:$($?):$LASTEXITCODE:>

Properties:
- Still prompt detection (preferred, clean model). Completion = prompt reappears.
- Nothing injected into command history (a prompt is not a history entry).
- Path shown (Shorten-Path kept). venv/conda prefix shown: activation scripts WRAP
  the existing prompt, so the prefix appears automatically AND the token survives
  inside the wrapped prompt.
- Token = strong unique ASCII anchor real output never contains. This is the anchor
  SSH had for free; detection keys on the token, so path changes, color codes, env
  prefixes cannot break it.
- Exit status rides along: $? (cmdlet success bool) and $LASTEXITCODE (native exe
  code). Better fidelity than SSH ever had.
- sessionUuid generated at session start; prevents confusing a token with stale
  output.

Web display: strip the " PWSH_MCP_READY:...:" token tail before broadcasting. Human
sees normal "(env) ...\proj >"; token invisible. Detector sees token; human does not.

Detection:
- AI command sent -> record current buffer line as scan-start for this command.
- Scan ONLY from scan-start forward (manual output before it ignored).
- Completion = line containing "PWSH_MCP_READY:<sessionUuid>:" after scan-start;
  parse $? and $LASTEXITCODE.
- Timeout without token -> partial output + "still running"; AI decides
  wait / interrupt / inspect.

src/prompt/ (prompt_detector*) DELETED. Pager handling dropped.

SPIKE RESULT (spike_pywinpty.py, all 5 probes PASS on pwsh 7): ConPTY spawn +
background reader + UTF-8, prompt-override token detection, exit-code capture,
interactive Read-Host (token withheld until input), Ctrl+C interrupt, and kill +
respawn ALL confirmed working. Foundation de-risked.

LESSON 1 (quoting): inject the prompt override robustly, NOT as a long typed
interactive line. Typed injection hit two PowerShell pitfalls: (a) "$o:" in a
double-quoted string is parsed as a drive-qualified variable and errors - use
${o} to delimit; (b) long lines can get reparsed across ">>" continuation prompts.
Production pwsh_session.py should define the prompt once at session start via a
temp snippet dot-sourced (or a here-string), not a single typed quoted line.

LESSON 2 ($? vs $LASTEXITCODE): they differ. $? = whether the last PowerShell
operation ran without a terminating error; $LASTEXITCODE = the native exe exit
code. A native tool can exit 5 while $? is still True. "Did this fail" should use:
$LASTEXITCODE for native commands, $? for pure-cmdlet operations, and the output
filter's error detection as a third signal. The prompt token carries BOTH.

================================================================================
## 5. COMPONENT MAP (grounded in the real copied tree)

REUSE (mostly as-is):
- src/web/ (web_terminal.py, web_terminal_ui.py, web_terminal_websocket.py) +
  src/static/ (xterm.js vendor, terminal.js, fragments). WebSocket broadcast
  multi-terminal sync. Strip SSH connection-display strings; remove transfer_panel
  (SFTP) bits.
- src/output/ (output_buffer*, output_filter*, output_formatter). Buffer reused;
  filter command-type table rewritten for PowerShell (section 7).
- src/command_state.py - in-memory per-command line tracking. Reuse.
- src/shared_state.py + src/state/ - reuse; remove SSH/transfer references.
- src/mcp_server.py - reuse scaffold; re-register trimmed tool set.
- src/database/ (database_manager, database_commands, database_conversations) -
  reuse, trimmed (section 6).
- src/config/ - reuse, simplified.
- src/utils/ (utils, utils_format, utils_output, utils_text) - reuse.
- src/error_check_helper.py - reuse for live error detection + selective persist.

DELETE (note: several are import-coupled - see caveat below; deletion happens
DURING retool with companion edits, not as a blind upfront script):
- src/ssh/ (ssh_manager, ssh_connection, ssh_io, ssh_commands) -> pwsh ConPTY.
- src/prompt/ (all 4) -> token detection.
- src/hosts_manager.py, hosts.yaml, config/hosts.yaml.example,
  src/tools/tools_hosts*.py -> single local machine.
- src/tools/tools_recipes*.py (7), src/database/database_recipes.py, recipes/,
  export_recipe.py -> script store.
- src/tools/sftp_*.py + src/tools/tools_sftp*.py (all), transfer_panel fragment,
  transfer-panel.js, src/state/shared_state_transfer.py -> no file transfer.
- standalone/ (whole dir), start_standalone.ps1 -> no standalone UI.
- src/utils/utils_machine_id.py, src/database/database_servers.py -> single machine
  (requires DB-layer edits: database_manager imports DatabaseServers).
- src/batch/ (batch_executor, batch_parser, batch_helpers), src/tools/tools_batch*.py,
  src/database/database_batch*.py, AND the batch_scripts / batch_executions DB tables
  -> DELETE COMPLETELY (never used). script_store is built fresh, no batch reuse.
- .mcpregistry_github_token, .mcpregistry_registry_token -> CREDENTIALS, delete now
  (do not commit; .gitignore must exclude).

NEW:
- src/pwsh_session.py - owns ConPTY via pywinpty; background reader -> buffer
  callback; send_input(), send_interrupt() (Ctrl+C); restart/respawn; UTF-8 setup;
  prompt-override injection at session start.
- src/completion_token.py - token generation + detection (replaces src/prompt/).
- src/script_store.py - save_script / list_scripts / run_script for .ps1.

IMPORT-COUPLING CAVEAT: ssh/, prompt/, tools_hosts*, tools_recipes*, tools_sftp*,
database_servers, database_recipes are imported by mcp_server.py / shared_state.py /
database_manager.py / tool registration. Deleting them breaks imports until the
retool edits land. Since the project is not run until retool, this is acceptable;
just do not expect it to import mid-deletion.

================================================================================
## 6. PERSISTENCE (SQLite) - CORRECTED MODEL

Confirmed by reading src/database/database_manager.py and
src/tools/tools_commands_database.py in the copied tree:
- The SSH commands table stores result_output (TEXT), has_errors, error_context,
  line_count, exit_code, status, plus conversation link + timestamps - for EVERY
  command (result_output=output passed in _save_to_database).
- The DB does NOT store buffer line pointers. buffer_start_line / buffer_end_line
  live only on in-memory CommandState. There is NO cross-restart dangling-pointer
  problem: the DB is self-contained.

Model for this project:

In-memory (transient, lost on restart - acceptable live working set):
- Circular buffer: all output for the running session.
- CommandState per command: buffer line range for live slicing this session.

SQLite (self-contained, survives restart):
- conversations(id, label, status, started_at, ended_at)
- commands(
    id, conversation_id, sequence_num,
    command_text,
    exit_code,        -- $LASTEXITCODE from token
    success,          -- $? from token
    status,           -- executed / cancelled / timeout
    has_errors,
    error_context,    -- extracted error lines (stored when errors)
    line_count,
    output_text,      -- NULLABLE; full output ONLY by policy below
    output_persisted, -- flag/reason: failed / script / flagged
    executed_at
  )
- scripts(name, content, content_hash, created_at, updated_at, times_used,
          last_used_at)

Selective full-output persistence (improvement over SSH which stored everything):
- ALWAYS store (small): command_text, exit_code, success, status, has_errors,
  error_context, line_count, timestamps.
- Store full output_text ONLY for: (1) failed commands (non-zero exit / $? false),
  (2) script runs, (3) commands explicitly flagged.
- Ordinary successful commands: metadata + error_context only, no full output.
- Retention: keep persisted output FOREVER (no pruning in v1).

LIMITATION (explicit): full raw output of an ordinary successful command lives only
in the in-memory buffer and is lost on MCP restart (not persisted by policy).
Failed / script / flagged output survives in the DB. Deliberate trade to avoid DB
bloat; revisit if a "persist all" mode is ever wanted.

DB-layer edits: remove machine_id / DatabaseServers coupling (single local machine);
drop recipes AND batch tables/handlers completely (batch never used).

================================================================================
## 7. POWERSHELL-SPECIFIC DESIGN

- Launch: pwsh -NoLogo (PROFILE LOADED). Fallback powershell.exe. After profile
  load, inject prompt override (section 4).
- Encoding: force UTF-8 at session start ([Console]::OutputEncoding and
  $OutputEncoding = UTF-8). Confirm xterm.js decodes UTF-8. Top Windows gotcha;
  PS7 UTF-8-first defaults are a main reason to prefer it over 5.1.
- Script execution: run_script writes a temp .ps1 and invokes
  -ExecutionPolicy Bypass -File <path>, wrapped by the same token completion.
  Multi-line goes to a file, not -Command.
- Output filter command-type table rewritten for PS verbs: Get-ChildItem (listing),
  Get-Content (file view), Select-String (log search), winget/choco/pip install,
  Get-Process, Get-Service, dotnet/msbuild builds. Error detection keys off the PS
  error stream + non-zero exit, not Linux stderr heuristics.
- ASCII only, no curl: generated PowerShell and any installer stay plain ASCII and
  use Invoke-WebRequest / Invoke-RestMethod (never curl). Standing rule.

================================================================================
## 8. MCP TOOL SURFACE (v1)

- execute_command(command, timeout=...) -> AI command via token completion;
  returns command_id + filtered output (or partial + still-running on timeout).
- get_command_output(command_id, raw=False) -> filtered or raw (raw from live
  buffer this session, or DB output_text if persisted).
- send_input(text) -> stdin to a running/interactive command.
- send_interrupt() -> Ctrl+C.
- get_terminal_status() -> session alive?, cwd, PS version, web URL.
- restart_session() -> kill + respawn pwsh (deliberately clears live state).
- save_script(name, content) / list_scripts() / run_script(name, timeout=...).

Conversation tools (HISTORY ONLY - rollback dropped):
- start_conversation(goal_summary) -> group commands by goal (no server scoping;
  single local machine).
- resume_conversation(conversation_id)
- end_conversation(conversation_id, status, user_notes)
- get_conversation_commands(conversation_id) -> command history (NO reverse_order,
  NO backup_file_path / undo fields).
- list_conversations(status, limit) -> (no server_identifier filter).

DROPPED with rollback: update_command_status tool; backup-on-write machinery;
reverse_order undo sequencing; backup_file_path / backup_size_bytes columns.

SSH project's overlapping output_mode (preview/auto/full) collapsed to:
filtered-by-default + raw=True escape hatch.

================================================================================
## 9. RESOURCE ISOLATION FROM remote-terminal-mcp

Both may run simultaneously; separate every shared resource:
- MCP server id (Claude Desktop config): powershell-terminal
- Web terminal port: 8090 (remote-terminal uses 8080)
- SQLite DB: %LOCALAPPDATA%\powershell-terminal\commands.db
- Log dir: %LOCALAPPDATA%\powershell-terminal\logs
- Config: own config.yaml in D:\powershell_terminal
- Root env var: POWERSHELL_TERMINAL_ROOT (was REMOTE_TERMINAL_ROOT; config.py +
  server.json must be updated together)

SWEEP: the copy carries old hardcoded values (8080, remote_terminal.db, old MCP
name, log paths, REMOTE_TERMINAL_ROOT). Reassign every occurrence before first run.

================================================================================
## 10. PROJECT IDENTITY RESET (new GitHub project, new PyPI package)

.git already deleted by user -> clean `git init` later, no history surgery.

Credentials in the copy - REMOVE NOW, never commit:
- .mcpregistry_github_token, .mcpregistry_registry_token
- Add both to .gitignore.

Identity files rewritten (done this turn; old identity -> new):
- pyproject.toml: name remote-terminal-mcp -> powershell-terminal-mcp; version
  reset to 0.1.0; description -> PS; requires-python >=3.10; deps drop paramiko, add
  pywinpty; OS classifier -> Microsoft :: Windows; URLs -> new repo; scripts ->
  single powershell-terminal-mcp entry (standalone entry dropped); packages drop
  standalone*.
- server.json: name io.github.TiM00R/remote-terminal -> .../powershell-terminal;
  version 0.1.0; title/description -> PS; homepage/sourceRepositoryUrl -> new repo;
  pypi identifier -> powershell-terminal-mcp; env var -> POWERSHELL_TERMINAL_ROOT.
- requirements.txt: drop paramiko, add pywinpty; drop legacy dataclasses shim.
- build.ps1: all remote_terminal_mcp / remote-terminal-mcp strings -> powershell.

Still to rewrite during retool (carry old identity): README.md, LICENSE (author
line ok to keep), MANIFEST.in, claude_desktop_config_example.json,
remote_terminal_mcp.egg-info/ (delete; regenerated on build), .vscode/settings.json
and .claude/settings.local.json (check for old paths/names), docs/.

New repo name: powershell-terminal-mcp (GitHub: TiM00R/powershell-terminal-mcp).
Re-auth registry/PyPI fresh; do not reuse the deleted token files.

================================================================================
## 11. VENV + DEPENDENCIES

- Python: 3.10+ (pywinpty supports 3.8+, but standardize on the build target;
  build uses py -3.11).
- New dependency set (vs SSH): DROP paramiko (no SSH). ADD pywinpty (ConPTY).
  Keep: nicegui, pyyaml, python-dotenv, aiofiles, python-json-logger, mcp,
  starlette, uvicorn.
- setup_venv.ps1 (written this turn): creates .venv with py -3.11, upgrades pip,
  installs requirements.txt, installs project editable (pip install -e .). ASCII,
  no curl.
- The copied .venv/ is the OLD environment (has paramiko, lacks pywinpty). Recreate
  it: delete .venv and run setup_venv.ps1.

================================================================================
## 12. PROPOSED FILE LAYOUT (post-retool)

D:\powershell_terminal\
  src\
    mcp_server.py        (reuse, retooled tool set)
    shared_state.py      (reuse, SSH/transfer refs removed)
    command_state.py     (reuse)
    pwsh_session.py      (NEW - ConPTY/pywinpty)
    completion_token.py  (NEW - token detection)
    script_store.py      (NEW)
    output\              (reuse; filter table rewritten for PS)
    web\                 (reuse; SSH/SFTP UI stripped)
    static\              (reuse; transfer-panel removed)
    state\               (reuse; shared_state_transfer removed)
    database\            (reuse; servers/recipes/batch trimmed)
    config\              (reuse; simplified, POWERSHELL_TERMINAL_ROOT)
    utils\               (reuse; utils_machine_id removed)
  config.yaml            (shell path, encoding, port 8090, filter, timeouts, paths)
  setup_venv.ps1         (NEW)
  build.ps1              (rewritten)
  pyproject.toml         (rewritten)
  server.json            (rewritten)
  requirements.txt       (rewritten)
  README.md              (rewrite during retool)

================================================================================
## 13. RISKS / OPEN ITEMS

- pywinpty / ConPTY reliability is the main unknown: reader-thread shutdown, clean
  respawn on restart_session, interactive-prompt behavior. SPIKE before full port.
- Prompt override vs activation scripts: confirm venv Activate.ps1 and conda
  activate WRAP (prepend) the prompt rather than replace it, so the token survives.
  If any path replaces it, re-assert the override.
- Token stripping from web display must handle the token mid-line cleanly without
  eating real output.
- Manual + AI interleaving: detection scans for the token (not by position) so it
  tolerates interleaved human output; verify in testing.
- Encoding edge cases: legacy tools emitting non-UTF-8 bytes; choose a reader
  replacement policy so the buffer never corrupts.

================================================================================
## 14. EXPLICIT NON-GOALS (v1)

- No rollback / command undo / automated file backups (single local machine; use
  git or your own backups). Drops update_command_status, backup-on-write, and undo
  sequencing. Conversations are history-only.
- No secret/password scrubbing (single user, own machine).
- No multi-server / remote support.
- No recipes.
- No standalone (8081) UI.
- No SFTP / file transfer.
- No output pruning / retention limits.
- No real-time streaming of full output to the AI.

================================================================================
## 15. SUGGESTED SEQUENCE

BUILD STATUS:
- [DONE] pywinpty spike (all 5 probes pass).
- [DONE] pwsh/ package: completion_token.py, pwsh_launch.py, pwsh_reader.py,
  pwsh_io.py, output_clean.py, pwsh_session.py. PwshSession proven by
  test_pwsh_session.py (all probes pass): spawn/settle, token completion,
  $?/$LASTEXITCODE, interactive send_input, Ctrl+C, restart, clean output
  extraction (start-marker strips echo; newline-trim drops next-prompt base).
  Integration hook: on_output(raw_chunk). NOT yet wired to buffer/web/db.
- [DONE] output wiring: pwsh/session_output.py (SessionOutput) ties PwshSession to
  OutputBuffer (full human scrollback) + SmartOutputFilter (AI token reduction).
  Proven by test_session_output.py: dual stream works (e.g. 199 raw -> 54 filtered),
  buffer captures full stream, on_broadcast feeds the web layer. Output normalized
  to \n-only for AI/DB; raw \r\n chunks go to the web terminal. AI path bypasses
  FilteredBuffer (clean per-command output already extracted by PwshSession).
  Fixed import-coupling: removed deleted utils_machine_id imports from utils/utils.py
  and utils/__init__.py. Rewrote detect_command_type for PowerShell verbs.
- [DONE] web terminal: rewired web/ (web_terminal, web_terminal_ui,
  web_terminal_websocket) + static/terminal.js from SSH to the local session via a
  rewritten lean shared_state.py hub (owns SessionOutput, feeds the broadcast queue,
  raw input + ConPTY resize). Preserved: multi-terminal sync, session_superseded /
  close_all / open_terminal, broadcast loop, graceful shutdown. Removed: SSH/SFTP
  UI, connection display.
  Display correctness solved by moving the completion token AND output start-marker
  into INVISIBLE OSC sequences (7000/7001) that xterm.js swallows (no text-stripping
  of the screen-addressed stream). Detection scans the RAW buffer; output extraction
  finds the OSC start marker. Start marker emitted by a PSReadLine Enter handler so
  AI command echo is clean (no visible wrapper). write_line uses CR-only (no double
  accept / '>>'). convertEol=false. Initial resize re-sent on ws open (ConPTY size
  matches xterm -> no line-wrap drift). Banner + clear emitted from the init script
  via Write-Host (rendered through ConPTY, no command echo, cursor rows in sync;
  first prompt positioned correctly). All MCP-only (temp init script, not $PROFILE).
  Verified working in-browser.
- [DONE] lean MCP server: mcp_server.py rewritten to register 6 tools directly
  against the hub (execute_command, get_command_output, send_input, send_interrupt,
  get_terminal_status, restart_session). First tool call starts the pwsh session +
  opens the shared web terminal. tools/__init__.py parked (TOOL_MODULES=[]).
  __main__.py entry point fixed (src. or top-level import). Imports clean
  (verified). DB/conversation tools, script store still pending.
- [DONE] headless dispatch test: test_mcp_dispatch.py drives every tool through
  _dispatch (no MCP client) - all pass: get_terminal_status/_ensure_started,
  execute_command (shape/exit/output/command_id), get_command_output (filtered+raw,
  unknown-id error), failing exit, interactive send_input->wait_more, send_interrupt,
  restart_session, unknown-tool error. Config loads from config.yaml (port 8090).
  ONLY the stdio JSON-RPC transport remains untested (mcp library code) -> validate
  via Claude Desktop registration. Everything testable headlessly is GREEN.
- [DONE] END-TO-END MCP VERIFIED (real Claude Desktop client): server registered in
  claude_desktop_config.json (port 8090, PYTHONPATH=src), stdio handshake works,
  tools listed. Live calls confirmed: get_terminal_status (alive+url), execute_command
  (Get-Process, clean filtered output + exit code), get_command_output (round-trip by
  id), and the shared-session manual/AI separation (human-typed pwd invisible to AI
  capture while AI command captured cleanly). Web terminal opens on first call. CORE
  MCP COMPLETE.
- [DONE] DB layer: src/db.py (lean SQLite, spec section 6) - conversations, commands
  (metadata always; output_text only for failed/script/flagged, kept forever),
  scripts (upsert + usage). DB at %LOCALAPPDATA%\powershell-terminal\commands.db.
  Hub wired: initialize creates DB, start_session opens a conversation, run_command
  logs each AI command with selective persistence. Verified by test_db.py (all pass).
  Tools to EXPOSE these (conversation + script tools) still pending in mcp_server.
- [DONE] full tool surface (13 tools): added conversation tools (start/end/list/
  get_conversation_commands) + script store (save/list/run_script, runs temp .ps1 via
  & 'path' with Set-ExecutionPolicy Process Bypass in init). All wired hub->mcp_server.
  Fixed bug: is_error_output(text, error_patterns) needs patterns (SSH-era signature) -
  logging was silently failing; now passes config.claude.error_patterns. Verified by
  extended test_mcp_dispatch.py (all 13 tools green headless, incl. command logged
  under conversation + script run). PENDING: one Claude Desktop restart to confirm the
  7 new tools over real stdio; then release work (README, git init, publish).
- [DONE] FULL 13-TOOL SURFACE VERIFIED LIVE (real Claude Desktop, post-restart):
  start_conversation (id 6), save_script + run_script (sysinfo: PS 7.5.5, exec-policy
  bypass worked, output captured/filtered), get_conversation_commands showed the run
  logged under the conversation with output_persisted='script' (selective policy kept
  full output for a script run). CORE PRODUCT FUNCTIONALLY COMPLETE.
  Minor note: $env:COMPUTERNAME came back empty in the spawned PS7 session (cosmetic).
  REMAINING = release only: README rewrite, fresh git init + new GitHub repo, PyPI
  publish; optional polish (auto reassert_prompt for conda/venv, delete orphaned
  shared_state_monitor.py).
- [DONE] cleanup pass (cleanup2.ps1): removed dead SSH-era code - old database/ dir
  (replaced by db.py), command_state.py, error_check_helper.py, output_formatter.py,
  all tools_commands*/tools_conversations*/tools_info/decorators, config/config.py,
  shared_state_monitor.py, check_schema.py, view_db.py, init-scripts/, egg-info.
  Two live deps that surfaced were fixed (not deleted-after-all): output/__init__.py
  no longer re-exports output_formatter; is_error_output() inlined its pattern check
  (was importing error_check_helper). All 13 tools still green (test_mcp_dispatch).

NEXT SESSION:
1. Register server in claude_desktop_config.json; do a real end-to-end call.
2. Wire DB layer (conversations + selective output persistence per section 6).
3. Add conversation tools (retool tools_conversations) + script store tools.
4. Re-add/retool tools_commands, tools_info if richer surface wanted.

1. Remove token files; recreate venv (setup_venv.ps1).
2. pywinpty spike (ConPTY respawn + interactive prompt) - throwaway script.
3. Add pwsh_session.py + completion_token.py; wire mcp_server execute_command to
   the new session with token completion; get one AI command round-tripping.
4. Strip SSH/hosts/recipes/sftp/standalone/batch (with companion import edits).
5. Rewrite output filter command-type table for PS verbs.
6. Trim DB layer (drop servers/recipes/batch; add output_persisted policy).
7. Strip web/static SFTP UI; verify human typing + AI commands in one terminal.
8. Rewrite README; fresh git init; new repo; publish when ready.
