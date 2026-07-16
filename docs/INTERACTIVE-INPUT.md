# powershell-terminal-mcp - INTERACTIVE INPUT

Status: IMPLEMENTED (v0.2.0). Companion to SPEC.md ("Interactive operation").
Project folder: D:\powershell_terminal

This document describes the AS-BUILT interactive-input mechanism. The original
draft proposed a "wrap by default, skip-wrapper flag for interactive" design; the
implementation reversed the default (see section 2) after testing exposed an
asymmetry between AI and human input. What shipped is documented below.

================================================================================
## 1. PROBLEM

The base execution model (SPEC): an AI command is written to the ConPTY and
completion is detected by the in-session prompt-override TOKEN firing when the
shell returns to its prompt. This works for batch commands. It failed for
interactive programs (a REPL, an installer, a prompting tool) for two reasons:

  (P1) DEAD STDIN. Native console exes were wrapped (NATIVE_WRAPPERS /
       NATIVE_EXE_HOOK in pwsh_launch.py) via ProcessStartInfo, which closes
       StandardInput immediately - added to suppress popup windows and capture
       output for piping. Side effect: every native exe was non-interactive, so
       REPLs hit EOF and exited instead of prompting. This broke HUMAN-typed
       interactive commands in the web terminal too, not only the AI.

  (P2) COMPLETION TIMING. An interactive program never returns to the PS prompt
       while it holds the foreground, so the TOKEN never fires. The old send_input
       called wait_more(timeout=60), blocking the full 60 s on every turn.

================================================================================
## 2. KEY DECISIONS (as built)

The draft kept the wrapper wrapping by DEFAULT and made interactive a per-command
"skip the wrapper" flag. Implementation REVERSED the default because of an
asymmetry found in testing:

  - The AI can flag a command (it calls a tool). A HUMAN typing in the web terminal
    CANNOT - their keystrokes go straight to the shell via raw passthrough. So
    whatever the DEFAULT is for an unflagged command is what humans get. Under "wrap
    by default", human-typed ftp/python/etc. died on EOF.

Final model: DEFAULT = BYPASS. The native-exe wrapper attaches the exe to the
ConPTY with stdin OPEN unless a per-command flag says otherwise. Only AI BATCH
commands opt IN to the stdin-closed capture wrapper.

  - Human (web terminal)        -> bypass  -> interactive exes work.
  - AI interactive (opt-in)     -> bypass  -> stdin open, driven via the primitive.
  - AI batch (default AI mode)  -> wrapped -> stdin closed, output captured.

The AI-batch mark is INVISIBLE (section 6): a zero-width sentinel, not a visible
prefix. Everything else from the original decisions holds:

  - The AI is the "expect" logic (server returns output + state; AI reads the
    prompt in natural language and drives the loop). expect-patterns are an
    optional fast-path hint, not the contract.
  - IDLE is a latency signal (when to hand the turn back), NEVER an input trigger.
  - Completeness is best-effort: a prompt-shaped-tail heuristic plus a re-pollable
    loop so the AI can accumulate more before answering.
  - Batch capture + exit codes are byte-for-byte the old behavior - the wrapper
    runs the identical ProcessStartInfo path when the flag is set.

================================================================================
## 3. STATE-MACHINE CONTRACT

The interactive wait returns a record, not just text:

    { state, output, tail, exit_code }

  output    - text accumulated since the last return (buffer slice).
  tail      - last ~120 chars of the ANSI-stripped buffer (prompt inspection).
  exit_code - int when state == EXITED, else null.

STATES (returned by execute_command(interactive=true), send_input, and poll):

  EXITED
    Completion token fired: the program returned control to the PS prompt.
    Terminal state; exit_code is authoritative (python exit(), installer done, a
    Ctrl+C'd process reporting its exit code, etc.).

  AWAITING_INPUT
    Strong "blocked on stdin" signal. Fires when EITHER:
      - an expect-pattern (if supplied) matches the tail, OR
      - output has been idle >= idle_ms AND the tail is PROMPT-SHAPED: the
        ANSI-stripped tail ends with prompt punctuation and NO trailing newline
        (roughly [:?>#$)\]] followed by optional spaces).
    Caller reads output, decides a response, and calls send_input.

  IDLE
    Output idle >= idle_ms but the tail is NOT prompt-shaped. Ambiguous: a mid-work
    pause or a partial prompt. Caller polls again to accumulate more; answers only
    if it judges the output already complete.

  RUNNING
    Hit the max-wait cap while output was still growing. Partial output returned.
    Caller polls to continue.

AI LOOP:

    EXITED          -> done; use exit_code.
    AWAITING_INPUT  -> read output; if a COMPLETE request, send_input(answer);
                       if it looks TRUNCATED, poll() first, then decide.
    IDLE            -> if output looks complete + actionable, answer; else poll().
    RUNNING         -> poll() to keep draining.

Two-layer completeness guarantee:
  Layer 1 (server): prompt-shaped-tail gate on AWAITING_INPUT. A fragment cut
    mid-sentence usually does not end in prompt punctuation, so it surfaces as
    IDLE, not AWAITING_INPUT.
  Layer 2 (AI): on IDLE / suspected truncation, the AI re-polls to gather the rest
    before responding. A wrong server guess costs a round-trip, not a bad answer.

================================================================================
## 4. THE INTERACTIVE PRIMITIVE

Session method in pwsh_session.py, alongside the existing wait_token:

    wait_interactive(idle_ms=600, max_s=30, expect=None)
        -> { state, output, tail, exit_code }

Loop (poll cadence ~30 ms):
  - Track buffer length; "idle" = length unchanged for idle_ms.
  - If the completion token appears in the scanned region -> EXITED (+ exit_code).
  - EOF guard: if the shell process is gone and the buffer is quiescent, return
    terminal instead of spinning to the cap.
  - Else if expect supplied and tail matches -> AWAITING_INPUT.
  - Else on idle: classify tail -> AWAITING_INPUT (prompt-shaped) or IDLE.
  - If elapsed > max_s and still growing -> RUNNING.

Notes:
  - idle_ms defaults HIGHER for interactive (600 ms, config) than the pure
    turn-latency value, because an early classify costs more here.
  - max_s is the safety cap so a long-but-silent step returns RUNNING, not a hang.
  - Idles on the ConPTY buffer (shared stream), so echo and the live web terminal
    are preserved. Interactive output is returned RAW (unfiltered) - the batch
    token-reducing filter is skipped so the prompt the AI must read is never hidden.

================================================================================
## 5. TOOL SURFACE (as built)

  execute_command
    + "interactive" (bool, default false), + "idle_ms" / "max_s" / "expect".
      false -> BATCH: sentinel-marked (section 6), wrapped, wait_token. Returns the
               unchanged {command_id, status, success, exit_code, output}.
      true  -> INTERACTIVE: bypass, wait_interactive. Returns
               {command_id, state, output, exit_code, tail}.

  send_input(text [, idle_ms, max_s, expect])
    Writes a line to the ConPTY, then wait_interactive.
    Returns {state, output, exit_code, tail}. (Was: token-shaped result.)

  poll([idle_ms, max_s, expect])   NEW
    wait_interactive with no write - the accumulate-more / re-poll primitive.

  send_interrupt   unchanged (Ctrl+C to foreground; ends a stuck interactive run).

================================================================================
## 6. AI-BATCH MARKER (how default-bypass stays safe for batch)

Requirement: AI batch must be wrapped (stdin closed, output captured), but the mark
must be INVISIBLE in the shared web terminal and must NOT depend on fragile byte/key
translation through ConPTY.

Rejected approaches (tested, failed):
  - Visible prefix ($global:...=$true; cmd) - clutters the shared terminal.
  - LF / Ctrl+J accept key - a raw \n injects as "continue line" (>> prompt), not
    the bound Ctrl+J handler; command never runs.
  - Function-key (F8) injection - raw escape bytes could not even be injected for
    testing and are unreliable to register as the intended key.

Shipped - zero-width sentinel (U+200B):
  - pwsh_io.write_accept_batch() prepends U+200B to the command and submits with a
    normal CR. write_line() (CR, no sentinel) is used by humans, the AI interactive
    launch, and send_input.
  - The PSReadLine Enter handler (completion_token.prompt_function_snippet) reads
    the line buffer; if it starts with U+200B it sets $global:__mcp_ai_batch and
    deletes that one char before AcceptLine. No sentinel => default bypass.
  - The prompt function resets $global:__mcp_ai_batch = $false on every return
    (after capturing $? / $LASTEXITCODE), so the flag stays set across a whole
    "a; b" line and clears before the next (possibly human) command.
  - pwsh_launch.py wrapper + hook wrap ONLY when $global:__mcp_ai_batch is set;
    otherwise they pass the exe through to the ConPTY with stdin open.
  - U+200B renders as nothing -> no visible marker, no flicker.

================================================================================
## 7. CREDENTIALS / CAVEATS / NON-GOALS

- Human-driven auth now works. Because the human raw-passthrough path bypasses the
  wrapper, a person can run ssh / ftp / etc. in the web terminal and answer the
  prompt directly - AI-invisible, not logged. Recommended path for REAL secrets.
- AI-entered credentials via send_input: discouraged for real secrets, since
  send_input output is broadcast to the web terminal and logged to the DB.
  (Public throwaway test credentials are a different matter.)
- Windows OpenSSH caveat: ssh reads its password from the CONSOLE (CONIN$), not
  stdin, and does not engage as a bypassed grandchild under winpty's ConPTY - so
  ssh password auth does not prompt via the interactive path (silent exit 255).
  Use key auth / BatchMode, a scripted client (ftp -s:file), or drive ssh in the
  top-level web terminal. Tools using ordinary stdin work: python -i, node, cmd,
  ftp, PowerShell Read-Host scripts.
- Cost model: each genuine interactive step is one AI round-trip (~seconds,
  dominated by model inference + transport + context size), not a tool cost. If the
  whole sequence is known in advance, script it (one turn) instead - e.g. ftp -s:
  login+get+bye ran ~1.4 s in a single command vs. 5 interactive turns.
- Perfect completeness detection: best-effort by design.
- Auto-detect interactive vs batch: not implemented; the flag is explicit.

================================================================================
## 8. IMPLEMENTATION (as built)

  src/pwsh/pwsh_session.py    - wait_interactive + _interactive_result /
                                _clean_interactive / _interactive_tail /
                                _is_prompt_shaped / _proc_exit_code; interactive
                                branch in run_command; send_input_interactive;
                                poll_interactive; EOF guard; get_raw_buffer.
  src/pwsh/pwsh_launch.py      - wrapper function + PostCommandLookupAction hook wrap
                                ONLY when $global:__mcp_ai_batch; default = bypass
                                (attach to ConPTY, stdin open).
  src/pwsh/pwsh_io.py          - write_accept_batch (U+200B sentinel + CR);
                                write_line (CR).
  src/completion_token.py      - Enter handler detects/strips the sentinel and sets
                                the flag; prompt resets the flag each return.
  src/pwsh/session_output.py   - run_command_interactive / send_input_interactive /
                                wait_interactive passthroughs (unfiltered output).
  src/shared_state.py          - same passthroughs + best-effort interactive logging.
  src/mcp_server.py            - interactive flag + idle_ms/max_s/expect on
                                execute_command; send_input -> interactive; poll tool.
  src/config/config_dataclasses.py, config_loader.py, config.yaml
                               - [interactive] section: idle_ms 600, max_s 30,
                                poll_ms 30.

================================================================================
## 9. VALIDATION (on hardware)

  - python -i, node -i, cmd, ftp driven end-to-end (connect / credentials / list /
    get / exit).
  - IDLE -> poll, expect= forcing AWAITING_INPUT on a non-punctuation prompt,
    RUNNING at the max_s cap, send_interrupt (Ctrl+C exit code) all verified.
  - Batch path unregressed: stdin closed (isatty False), exit codes propagate
    (7, 3); wrapped exes (python, git) and hook exes (whoami, ipconfig, cmd) and
    cmdlets all captured.
  - Human-typed ftp in the web terminal prompts and completes (the P1 fix).
  - Tool-side per interactive step is sub-second; the felt latency is the AI turn.
