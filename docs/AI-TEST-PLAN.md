# PowerShell Terminal MCP — Full AI Test Plan

Paste this whole document into a Claude chat that has the **powershell-terminal**
MCP server connected (Claude Desktop or Claude Code). Claude runs every test
through the MCP tools and reports the results.

---

## Instructions for Claude (read first)

You are testing the `powershell-terminal` MCP server. Work through every section
below in order.

**Rules**

1. Use **only the powershell-terminal MCP tools** to run commands
   (`execute_command`, `send_input`, `poll`, `send_interrupt`, `get_command_output`,
   `get_terminal_status`, `restart_session`, `open_terminal`, the conversation tools
   and the script tools). Do not use any other shell or terminal tool.
2. Do all file work inside the scratch folder `$env:TEMP\pt-ai-test` (created in
   test 0.2). Never create, change or delete anything outside it, except saved
   scripts named `aitest_*` and the history rows the tests produce.
3. Run one tool call at a time and wait for its result before the next one. Never
   send two shell commands in parallel.
4. If a command is left running (status `running`, `busy`, or state `RUNNING`),
   finish it before moving on: `poll` until it ends, or `send_interrupt`
   (then `send_interrupt` with `force: true` if `stopped` is `false`).
5. Tests marked **[USER]** need the user to do something in the browser. Ask them,
   wait for their answer, then check. If the user says skip, mark it SKIPPED.
6. Tests marked **[OPTIONAL]** open windows or take extra time. Ask the user first.
7. Tests marked **[KNOWN]** check a documented limitation. The expected result is
   the limitation itself; report what happened, but don't count it as a failure.
8. For every test, compare the actual result with **Expect**. If anything differs,
   mark it FAIL and quote the exact tool result.
9. If the session becomes stuck (every command returns `busy` and interrupts don't
   help), call `restart_session`, note it in the report, and continue.

**Report format** (at the end):

| ID | Result (PASS / FAIL / SKIPPED / KNOWN) | Notes (quote the tool result when not PASS) |
|---|---|---|

Then a short summary: totals, every FAIL with what was expected vs. what happened,
and anything else that looked wrong even if the test passed.

---

## 0. Setup

**0.1 Status before start**
- Do: `get_terminal_status`
- Expect: a JSON with `alive` (true or false — the shell starts on first use) and
  `web_url` (normally `http://localhost:8090`).

**0.2 Create the scratch folder**
- Do: `execute_command`:
  ```powershell
  $T = "$env:TEMP\pt-ai-test"; Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue; New-Item -ItemType Directory $T | Out-Null; Set-Location $T; Get-Location
  ```
- Expect: `status: completed`, `exit_code: 0`, output shows the scratch folder path.

**0.3 Server identity**
- Do: `execute_command`:
  ```powershell
  $pp = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId; (Get-CimInstance Win32_Process -Filter "ProcessId=$pp").CommandLine; $PSVersionTable.PSVersion.ToString()
  ```
- Expect: the command line of the MCP server that owns this shell (shows whether it
  is a source checkout `...\src\mcp_server.py` or an installed
  `...\powershell-terminal-mcp.exe`), then the PowerShell version. Record both in
  the report.

---

## 1. Web terminal

**1.1 Open the terminal**
- Do: `open_terminal`
- Expect: `status: shown`, `tab` is `new` or `existing`, `url` is the web terminal URL.

**1.2 Reuse, no duplicate tab**
- Do: `open_terminal` again.
- Expect: `tab: existing` (the same tab is reused and its header flashes). No second tab opens.

**1.3 [USER] Commands appear live**
- Do: `execute_command`: `Write-Output "visible-in-browser-123"`
- Ask the user: "Do you see `visible-in-browser-123` in the web terminal?"
- Expect: yes.

**1.4 [USER] Shared session: human → AI**
- Ask the user to type in the web terminal: `$fromHuman = 'typed by human'` and press Enter.
- Do: `execute_command`: `$fromHuman`
- Expect: output `typed by human`.

**1.5 [USER] Shared session: AI → human**
- Do: `execute_command`: `$fromAI = 'set by AI'`
- Ask the user to type `$fromAI` in the web terminal.
- Expect: the user sees `set by AI`.

**1.6 [USER] Reconnect replay**
- Ask the user to refresh the web terminal tab (F5).
- Expect: the tab is not blank; the earlier commands and output are restored in
  color, with a working prompt.

**1.7 [USER] Multi-tab sync**
- Ask the user to open a second tab at the same URL, then type `Get-Date` in one tab.
- Expect: the command and its output appear in both tabs. Then ask the user to close
  the second tab.

**1.8 Websocket from another website is refused (security)**
- Do: `execute_command`:
  ```powershell
  function Test-WsOrigin($origin) {
    $c = [System.Net.Sockets.TcpClient]::new('localhost', 8090)
    $s = $c.GetStream(); $s.ReadTimeout = 3000
    $req = "GET /ws/terminal HTTP/1.1`r`nHost: localhost:8090`r`nUpgrade: websocket`r`nConnection: Upgrade`r`nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==`r`nSec-WebSocket-Version: 13`r`nOrigin: $origin`r`n`r`n"
    $b = [Text.Encoding]::ASCII.GetBytes($req); $s.Write($b, 0, $b.Length)
    $buf = New-Object byte[] 512; $n = $s.Read($buf, 0, 512); $c.Close()
    ([Text.Encoding]::ASCII.GetString($buf, 0, $n) -split "`r`n")[0]
  }
  'foreign site : ' + (Test-WsOrigin 'http://evil.example')
  'wrong port   : ' + (Test-WsOrigin 'http://localhost:8091')
  'real page    : ' + (Test-WsOrigin 'http://localhost:8090')
  ```
- Expect: `foreign site` and `wrong port` show `HTTP/1.1 403 ...`; `real page` shows
  `HTTP/1.1 101 Switching Protocols`. (If the web port is not 8090, use the port
  from `web_url`.)

---

## 2. Basic execution and exit codes

**2.1 Simple cmdlet**
- Do: `execute_command`: `Get-Date -Format 'yyyy-MM-dd'`
- Expect: `completed`, `exit_code: 0`, `success: true`, today's date.

**2.2 Native exit code**
- Do: `execute_command`: `cmd /c exit 3`
- Expect: `exit_code: 3`. [KNOWN] `success` shows `true` here: for native programs
  run by the AI wrapper, `success` reflects the wrapper, not the program's exit code.
  Use `exit_code` to judge native programs.

**2.3 $LASTEXITCODE inside a command; reset before the next one**
- Do: `execute_command`: `cmd /c exit 3; "same-command LASTEXITCODE=$LASTEXITCODE"`
- Expect: `same-command LASTEXITCODE=3`, `exit_code: 3`.
- Then: `execute_command`: `$LASTEXITCODE`
- Expect: `0` (the server resets it before each AI command, so a command never
  reports an earlier program's code).

**2.4 PowerShell error**
- Do: `execute_command`: `Get-Item C:\does-not-exist-pt-test`
- Expect: `success: false`; output contains `Cannot find path`.

**2.5 Unknown command**
- Do: `execute_command`: `ThisCommandDoesNotExist-PtTest`
- Expect: `success: false`; output contains `is not recognized`.

**2.6 Error stream from a native program**
- Do: `execute_command`: `cmd /c "echo to-stderr 1>&2"`
- Expect: output contains `to-stderr`.

**2.7 Success after failure resets**
- Do: `execute_command`: `Write-Output ok`
- Expect: `success: true`, `exit_code: 0`.

**2.8 No output**
- Do: `execute_command`: `$null`
- Expect: `completed`, `success: true`, empty output or `[No output]`.

**2.9 Comment only**
- Do: `execute_command`: `# just a comment`
- Expect: `completed`, `success: true`, no output, no hang.

---

## 3. Session state persists between calls

**3.1 Variable**
- Do: `execute_command`: `$ptVar = 12345` then `execute_command`: `$ptVar * 2`
- Expect: `24690`.

**3.2 Function**
- Do: `execute_command`: `function Get-PtDouble($x) { $x * 2 }` then `execute_command`: `Get-PtDouble 21`
- Expect: `42`.

**3.3 Current folder**
- Do: `execute_command`: `New-Item -ItemType Directory "$env:TEMP\pt-ai-test\sub" | Out-Null; Set-Location "$env:TEMP\pt-ai-test\sub"` then `execute_command`: `(Get-Location).Path`
- Expect: path ends with `pt-ai-test\sub`.

**3.4 Environment variable**
- Do: `execute_command`: `$env:PT_TEST_ENV = 'env-ok'` then `execute_command`: `cmd /c echo %PT_TEST_ENV%`
- Expect: `env-ok` (a child program sees the session's environment).

**3.5 Back to the scratch folder**
- Do: `execute_command`: `Set-Location "$env:TEMP\pt-ai-test"`
- Expect: `completed`.

---

## 4. Multi-line commands

**4.1 Block with loop**
- Do: `execute_command` with this exact multi-line text:
  ```powershell
  $sum = 0
  foreach ($i in 1..10) {
      $sum += $i
  }
  "sum=$sum"
  ```
- Expect: one result, `sum=55`, `exit_code: 0`.

**4.2 Variables from the block persist**
- Do: `execute_command`: `$sum`
- Expect: `55`.

**4.3 Multi-line function definition**
- Do: `execute_command`:
  ```powershell
  function Get-PtGreeting {
      param($Name)
      "Hello, $Name"
  }
  ```
  then `execute_command`: `Get-PtGreeting -Name Tester`
- Expect: `Hello, Tester`.

**4.4 Here-string**
- Do: `execute_command`:
  ```powershell
  $text = @"
  line one
  line two
  "@
  ($text -split "`n").Count
  ```
- Expect: `2`.

**4.5 Failure in the last statement of a block is reported**
- Do: `execute_command`:
  ```powershell
  Write-Output "first"
  Get-Item C:\does-not-exist-pt-test
  ```
- Expect: output contains `first` and `Cannot find path`; `success: false`.

**4.6 Long piped chain over several lines**
- Do: `execute_command`:
  ```powershell
  1..20 |
      Where-Object { $_ % 2 -eq 0 } |
      ForEach-Object { $_ * 10 } |
      Measure-Object -Sum |
      Select-Object -ExpandProperty Sum
  ```
- Expect: `1100`.

**4.7 [KNOWN] Incomplete block (unbalanced brace)**
- Do: `execute_command` with `timeout: 5`:
  ```powershell
  if ($true) {
      "never closed"
  ```
- Expect (documented limitation): `status: running` after about 5 s, because
  PowerShell waits at the `>>` prompt. Then `send_interrupt` → `stopped: true`.
  Then `execute_command`: `Write-Output recovered` → `recovered`.

---

## 5. Native programs (no popup windows)

**5.1 Programs from the wrapped list**
- Do: `execute_command`: `git --version; python --version`
- Expect: both versions printed, `exit_code: 0`, no console window opens.
  (If git or python is not installed, mark SKIPPED for that part.)

**5.2 Programs found by the hook (not in the list)**
- Do: `execute_command`: `whoami; hostname; ipconfig | Select-String IPv4 | Select-Object -First 1`
- Expect: user name, computer name and an IPv4 line. No popup windows.

**5.3 Full path to an exe**
- Do: `execute_command`: `& "$env:SystemRoot\System32\whoami.exe"`
- Expect: the user name.

**5.4 Program runs in the shell's current folder**
- Do: `execute_command`: `Set-Location "$env:TEMP\pt-ai-test"; python -c "import os; print(os.getcwd())"`
- Expect: the scratch folder path. (Skip if no python.)

**5.5 Folder with non-ASCII name**
- Do: `execute_command`:
  ```powershell
  $d = Join-Path "$env:TEMP\pt-ai-test" 'тест 日本 😀'; New-Item -ItemType Directory $d | Out-Null; Set-Location $d; python -c "import os; print(os.getcwd())"; Set-Location "$env:TEMP\pt-ai-test"
  ```
- Expect: the full path printed correctly, including `тест 日本 😀`.

**5.6 Native exit code through the wrapper**
- Do: `execute_command`: `python -c "import sys; sys.exit(7)"`
- Expect: `exit_code: 7` (`success` shows `true`, the same known behaviour as 2.2).

**5.7 [OPTIONAL] GUI program does not block**
- Ask the user first (a Notepad window will open).
- Do: `execute_command` with `timeout: 10`: `notepad`
- Expect: returns at once (`completed`), Notepad opens normally. Ask the user to close it.

---

## 6. Piping input into programs

**6.1 Pipe into a wrapped program**
- Do: `execute_command`: `"piped-text" | python -c "import sys; print(repr(sys.stdin.read()))"`
- Expect: `'piped-text\n'`.

**6.2 No BOM at the start of piped input**
- Do: `execute_command`: `"x" | python -c "import sys; d=sys.stdin.read(); print('BOM' if d.startswith('\ufeff') else 'no BOM')"`
- Expect: `no BOM`.

**6.3 Several lines**
- Do: `execute_command`: `"a","b","c" | python -c "import sys; print(len(sys.stdin.readlines()), 'lines')"`
- Expect: `3 lines`.

**6.4 Pipe into a program found by the hook**
- Do: `execute_command`: `"banana","apple","cherry" | sort.exe`
- Expect: `apple`, `banana`, `cherry` in that order.

**6.5 Pipe from a file**
- Do: `execute_command`: `Set-Content "$env:TEMP\pt-ai-test\in.txt" "from-file"; Get-Content "$env:TEMP\pt-ai-test\in.txt" | python -c "import sys; print(sys.stdin.read().strip())"`
- Expect: `from-file`.

**6.6 Program with no piped input does not wait for input**
- Do: `execute_command` with `timeout: 15`: `python -c "import sys; print('stdin:', repr(sys.stdin.read()))"`
- Expect: completes quickly with `stdin: ''` (input is closed, not left waiting).

---

## 7. Unicode and emoji

**7.1 PowerShell output**
- Do: `execute_command`: `Write-Output "Привет ✓ 日本語 🎉 émoji"`
- Expect: the text exactly as sent.

**7.2 Python output (UTF-8 mode)**
- Do: `execute_command`: `python -c "print('Привет ✓ 日本語 🎉')"`
- Expect: the text exactly, no `UnicodeEncodeError`, no garbled characters.

**7.3 Python UTF-8 mode is on**
- Do: `execute_command`: `$env:PYTHONUTF8; python -c "import sys; print(sys.flags.utf8_mode)"`
- Expect: `1` and `1`.

**7.4 Unicode through a pipe**
- Do: `execute_command`: `"Привет ✓ 🎉" | python -c "import sys; print(repr(sys.stdin.read()))"`
- Expect: `'Привет ✓ 🎉\n'` (not `ÐŸÑ€...` or `âœ“`).

**7.5 File round trip**
- Do: `execute_command`: `Set-Content "$env:TEMP\pt-ai-test\u.txt" "Ünïcödé 😀" -Encoding utf8; Get-Content "$env:TEMP\pt-ai-test\u.txt" -Encoding utf8`
- Expect: `Ünïcödé 😀`.

**7.6 Unicode in an interactive prompt**
- Do: `execute_command` with `interactive: true`: `$a = Read-Host "Имя"; "Got: $a"`
- Expect: `state: AWAITING_INPUT`, tail ends with `Имя: `.
- Then: `send_input` with `text: Дима 😀`
- Expect: `state: EXITED`, output contains `Got: Дима 😀`.

---

## 8. Output filtering and full output

**8.1 Long output is summarized for the AI**
- Do: `execute_command`: `1..300`
- Expect: `completed`; output starts with `[Output truncated - 300 total lines]`,
  shows `1`–`30`, then `[... 250 lines omitted ...]`, then `281`–`300`. Note the
  `command_id`.

**8.2 Full output on request**
- Do: `get_command_output` with that `command_id` and `raw: true`
- Expect: the full output, every number from `1` to `300`.

**8.3 Filtered output on request**
- Do: `get_command_output` with the same `command_id`, no `raw`.
- Expect: the same short version as 8.1.

**8.4 Unknown command_id**
- Do: `get_command_output` with `command_id: doesnotexist`
- Expect: `{"error": "unknown command_id"}`.

**8.5 Short output is not filtered**
- Do: `execute_command`: `1..10`
- Expect: all 10 numbers.

**8.6 Large directory listing**
- Do: `execute_command`: `Get-ChildItem $env:SystemRoot\System32 | Select-Object -First 300 Name`
- Expect: `completed`, a shortened listing (not all 300 lines), no error.

---

## 9. Timeouts, long commands and the busy guard

**9.1 Timeout returns "running"**
- Do: `execute_command` with `timeout: 3`: `Start-Sleep -Seconds 8; "slept"`
- Expect: `status: running` after about 3 s.

**9.2 Busy guard: nothing is typed into a running command**
- Immediately do: `execute_command`: `Write-Output should-not-run`
- Expect: `status: busy`, an `error` explaining the previous command is still running,
  and a `tail`. The text `should-not-run` must NOT be executed.

**9.3 Busy guard also covers run_script**
- Do: `run_script` with `name: anything`
- Expect: `status: busy` (same message).

**9.4 Collect the rest with poll**
- Do: `poll` with `idle_ms: 8000`, `max_s: 15`.
- Expect: `state: EXITED`, `exit_code: 0`, output `slept`.
- Note: a plain `poll` (default `idle_ms` 600) returns `IDLE` almost at once on a
  command that prints nothing; that is expected, not a failure. Silent commands
  need a larger `idle_ms`.

**9.5 Shell is free again**
- Do: `execute_command`: `Write-Output free-again`
- Expect: `free-again`, and `should-not-run` never appeared anywhere.

---

## 10. Interactive programs

**10.1 Read-Host prompt**
- Do: `execute_command` with `interactive: true`: `$n = Read-Host "Your name"; "Hi $n"`
- Expect: `state: AWAITING_INPUT`, tail `Your name: `.
- Then: `send_input` `text: Tester`
- Expect: `state: EXITED`, `exit_code: 0`, output contains `Hi Tester`.

**10.2 Several prompts in a row**
- Do: `execute_command` with `interactive: true`:
  `$a = Read-Host "First"; $b = Read-Host "Second"; "a=$a b=$b"`
- Expect: `AWAITING_INPUT` (`First: `) → `send_input` `1` → `AWAITING_INPUT`
  (`Second: `) → `send_input` `2` → `EXITED` with `a=1 b=2`.

**10.3 Python REPL**
- Do: `execute_command` with `interactive: true`: `python -i -q`
- Expect: `AWAITING_INPUT`, tail ends with `>>> `.
- Then: `send_input` `x = 6 * 7` → `AWAITING_INPUT`; `send_input` `print(x)` → output
  contains `42`; `send_input` `exit()` → `state: EXITED`.

**10.4 expect regex forces AWAITING_INPUT**
- Do: `execute_command` with `interactive: true` and `expect: "\\(y/n\\)"`:
  `$r = Read-Host "Proceed (y/n)"; "answer=$r"`
- Expect: `AWAITING_INPUT` → `send_input` `y` → `EXITED` with `answer=y`.

**10.5 poll accumulates output without sending input**
- Do: `execute_command` with `interactive: true`, `idle_ms: 300`, `max_s: 2`:
  `python -c "import time; [print('tick', i, flush=True) or time.sleep(1) for i in range(6)]"`
- Expect: `RUNNING` or `IDLE` with only the first ticks.
- Then: `poll` with `idle_ms: 1500`, `max_s: 10` (repeat if needed) until `state: EXITED`.
- Expect: all of `tick 0` … `tick 5` received across the results, final `exit_code: 0`.

**10.6 Interactive exit code**
- Do: `execute_command` with `interactive: true`:
  `python -c "import sys; input('Press Enter: '); sys.exit(4)"`
- Expect: `AWAITING_INPUT` → `send_input` with `text: ""` → `EXITED`, `exit_code: 4`.

**10.7 [KNOWN] Stale exit code on the next interactive command**
- Do: `execute_command` with `interactive: true`: `$z = Read-Host "Anything"; "ok"` →
  `send_input` `z`.
- Expect: `EXITED`. Known issue: `exit_code` may still show `4` from 10.6 instead of
  `0`. Report the value you get.

**10.8 Native console prompt (choice.exe)**
- Do: `execute_command` with `interactive: true`: `choice /c yn /m "Continue"`
- Expect: `AWAITING_INPUT`, tail contains `Continue [Y,N]?`.
- Then: `send_input` `y`
- Expect: `EXITED`, `exit_code: 1` (choice returns the index of the key). Then run
  `execute_command`: `Write-Output after-choice` → `after-choice` (the extra Enter
  did no harm).

**10.9 PowerShell confirmation prompt**
- Do: `execute_command`: `Set-Content "$env:TEMP\pt-ai-test\del-me.txt" "x"`
- Then `execute_command` with `interactive: true`: `Remove-Item "$env:TEMP\pt-ai-test\del-me.txt" -Confirm`
- Expect: `AWAITING_INPUT` with a `[Y] Yes ... [N] No` prompt → `send_input` `Y` →
  `EXITED`. Then `execute_command`: `Test-Path "$env:TEMP\pt-ai-test\del-me.txt"` → `False`.

---

## 11. Ctrl+C and force stop

**11.1 Ctrl+C stops a PowerShell cmdlet**
- Do: `execute_command` with `timeout: 3`: `Start-Sleep -Seconds 60`
- Expect: `status: running`.
- Then: `send_interrupt`
- Expect: `stopped: true`, tail is the prompt.

**11.2 Shell works right after Ctrl+C**
- Do: `execute_command`: `Write-Output after-ctrl-c`
- Expect: `after-ctrl-c` (and not a premature or empty result).

**11.3 Ctrl+C stops an interactive Python loop**
- Do: `execute_command` with `interactive: true`, `max_s: 3`:
  `python -c "import time; [time.sleep(1) for _ in iter(int, 1)]"`
- Expect: `IDLE` or `RUNNING` (it prints nothing). Then `send_interrupt` →
  `stopped: true`, tail shows `KeyboardInterrupt`.

**11.4 Wrapped batch program ignores Ctrl+C, force stops it**
- Do: `execute_command` with `timeout: 3`: `ping -n 60 127.0.0.1`
- Expect: `status: running`.
- Then: `send_interrupt`
- Expect (documented): `stopped: false` with a message to use `force=true`.
- Then: `send_interrupt` with `force: true`
- Expect: `stopped: true`, `ended` lists `PING.EXE`.

**11.5 Shell survives a force stop**
- Do: `execute_command`: `$ptVar; Write-Output after-force`
- Expect: `12345` (variables from section 3 still there) and `after-force`.

**11.6 Ctrl+C at an idle prompt**
- Do: `send_interrupt` (nothing running).
- Expect: a result (`stopped: true`), no error.
- Then: `execute_command`: `Write-Output idle-ok`
- Expect: `idle-ok` as its own normal result.

**11.7 [OPTIONAL] Force also ends background jobs**
- Do: `execute_command`: `$j = Start-Job { Start-Sleep 300 }; $j.State`
- Expect: `Running`.
- Then: `execute_command` with `timeout: 2`: `ping -n 30 127.0.0.1` → `running` →
  `send_interrupt` with `force: true`.
- Expect: `stopped: true`, and `ended` lists both `PING.EXE` and `pwsh.exe` (the job's
  worker process). Note: `(Get-Job -Id $j.Id).State` may still say `Running` right
  afterwards; PowerShell updates the job state late, so judge by `ended`.
- Clean up: `execute_command`: `Get-Job | Remove-Job -Force`.

---

## 12. Saved scripts

**12.1 Save**
- Do: `save_script` `name: aitest_hello`, `content`:
  ```powershell
  param()
  "script says hello ✓"
  "ps " + $PSVersionTable.PSVersion.Major
  ```
- Expect: `{"saved": "aitest_hello"}`.

**12.2 List**
- Do: `list_scripts`
- Expect: the list includes `aitest_hello`.

**12.3 Run**
- Do: `run_script` `name: aitest_hello`
- Expect: `completed`, `exit_code: 0`, output `script says hello ✓` and `ps 7` (or `ps 5`).

**12.4 Overwrite**
- Do: `save_script` `name: aitest_hello`, `content: "version two"` then `run_script` `aitest_hello`
- Expect: output `version two`.

**12.5 Multi-line script with a loop and a function**
- Do: `save_script` `name: aitest_multi`, `content`:
  ```powershell
  function Square($n) { $n * $n }
  $out = foreach ($i in 1..5) { Square $i }
  $out -join ','
  ```
  then `run_script` `aitest_multi`
- Expect: `1,4,9,16,25`.

**12.6 Script that fails**
- Do: `save_script` `name: aitest_fail`, `content: Get-Item C:\does-not-exist-pt-test`
  then `run_script` `aitest_fail`
- Expect: failure reported (`exit_code` not 0 or `success` false / error text with
  `Cannot find path`).

**12.7 Unknown script**
- Do: `run_script` `name: aitest_does_not_exist`
- Expect: `status: error` and `error: unknown script: aitest_does_not_exist`.

**12.8 Script sees session state**
- Do: `save_script` `name: aitest_state`, `content: "ptVar=$ptVar"` then `run_script` `aitest_state`
- Expect: `ptVar=12345` (the script runs in the live session).

---

## 13. History (conversations)

**13.1 Start a labeled conversation**
- Do: `start_conversation` `label: ai-test`
- Expect: a `conversation_id` (note it).

**13.2 Commands are logged**
- Do: `execute_command`: `Write-Output history-success` and
  `execute_command`: `Get-Item C:\does-not-exist-history`
- Then: `get_conversation_commands` with the noted id.
- Expect: both commands listed in order, with exit code / success. The failed one has
  its full output stored; the successful one has no stored output (only failures,
  scripts and flagged commands keep full output).

**13.3 Script runs keep full output**
- Do: `run_script` `aitest_multi`, then `get_conversation_commands` again.
- Expect: a row `run_script:aitest_multi` with its output stored.

**13.4 Interactive commands are logged**
- Do: `execute_command` `interactive: true`: `$q = Read-Host "Log me"; "ok"` →
  `send_input` `x`; then `get_conversation_commands`.
- Expect: a row for it with an `interactive...` status.

**13.5 History by time range**
- Do: `execute_command`: `Get-Date -Format 'yyyy-MM-dd HH:mm:ss'` to get the current
  time, then `get_command_history` with `from_date` = that time minus 3 minutes and
  `to_date` = that time plus 1 minute (format `YYYY-MM-DD HH:MM:SS`).
- Expect: the commands from 13.2–13.4 in time order, and nothing from much earlier.
  (A whole-day range like `from_date: <today>`, `to_date: <today>` also works but can
  return hundreds of rows.)

**13.6 Range in the future is empty**
- Do: `get_command_history` with both dates one day after today (`YYYY-MM-DD`).
- Expect: `[]`.

**13.7 Bad date**
- Do: `get_command_history` `from_date: 2026-13-45`, `to_date: 2026-13-46`
- Expect: a clear error about the date format (`{"error": "bad date ..."}`), not a crash.

**13.8 List and end conversations**
- Do: `list_conversations` `limit: 5` → the `ai-test` conversation is listed as active.
- Do: `end_conversation` (no arguments) → returns the id.
- Do: `list_conversations` `limit: 5`
- Expect: `ai-test` now has status `completed` and an end time.

---

## 14. Restart

**14.1 Restart clears state**
- Do: `execute_command`: `$beforeRestart = 'still here?'`
- Do: `restart_session`
- Expect: a success result.
- Do: `execute_command`: `"[$beforeRestart]"; (Get-Location).Path`
- Expect: `[]` (variable gone) and the start folder: the project folder for a Claude
  Code session, otherwise `POWERSHELL_TERMINAL_HOME` or the home folder. Variables
  from earlier sections (`$ptVar` etc.) are gone from here on.

**14.2 Everything works after restart**
- Do: `execute_command`: `"Привет 🎉"; "a" | python -c "import sys; print(sys.stdin.read().strip())"; cmd /c exit 2`
- Expect: the Unicode text, `a`, and `exit_code: 2`.

**14.3 Status after restart**
- Do: `get_terminal_status`
- Expect: `alive: true`.

---

## 15. Odd inputs

**15.1 Quotes and special characters**
- Do: `execute_command`: ``Write-Output 'single "double" $notAVariable `backtick #hash'``
- Expect: exactly ``single "double" $notAVariable `backtick #hash``.

**15.2 Very long single line**
- Do: `execute_command`: `$s = 'x' * 5000; $s.Length`
- Expect: `5000`.

**15.3 Long argument to a native program**
- Do: `execute_command`: `python -c "print(len('$('y' * 3000)'))"`
- Expect: `3000`.

**15.4 Semicolon chain**
- Do: `execute_command`: `$a=1; $b=2; $a+$b`
- Expect: `3`.

**15.5 Output with ANSI colors**
- Do: `execute_command`: `Write-Host "colored" -ForegroundColor Green; "plain"`
- Expect: `colored` and `plain` as readable text (no raw escape garbage in the AI
  result).

**15.6 Output without a trailing newline**
- Do: `execute_command`: `python -c "import sys; sys.stdout.write('no-newline')"`; then `execute_command`: `Write-Output next-line`
- Expect: `no-newline`, then `next-line` as a separate, correct result.

---

## 16. [USER] [OPTIONAL] Two Claude clients (take-over)

Needs a second Claude client (for example a Claude Code session) with the same server.

1. In this chat: `execute_command`: `$owner = 'chat A'; Set-Location "$env:TEMP\pt-ai-test"`.
2. Ask the user to run any command from the other Claude client.
3. Ask the user: did the web terminal briefly show "Terminal taken over by another
   Claude session, reconnecting…", switch to the other shell, and the tab title
   change owner? Did no new tab open?
4. In this chat: `execute_command`: `$owner; (Get-Location).Path`
5. Expect: `chat A` and the scratch folder (this chat's shell kept its state), and the
   web terminal switches back to this chat's screen.

---

## 17. [KNOWN] Documented limitations (report, don't fail)

**17.1 Full-screen programs** — not supported; skip running `vim`/`htop`.

**17.2 ssh password prompts** — Windows OpenSSH reads passwords from the console, not
stdin, so they can't be answered through `send_input`. Skip unless the user wants to
see it.

**17.3 Blank lines between program outputs**
- Do: `execute_command`: `git --version; python --version`
- Expect: both lines; an extra empty line between them is a known cosmetic issue.

---

## 18. Cleanup

**18.1 Remove scratch files**
- Do: `execute_command`: `Set-Location $HOME; Remove-Item -Recurse -Force "$env:TEMP\pt-ai-test"; Remove-Item Env:PT_TEST_ENV -ErrorAction SilentlyContinue; Get-Job | Remove-Job -Force -ErrorAction SilentlyContinue; Test-Path "$env:TEMP\pt-ai-test"`
- Expect: `False`.

**18.2 Saved test scripts**
- There is no MCP tool to delete scripts (on purpose). Tell the user the scripts
  `aitest_hello`, `aitest_multi`, `aitest_fail` and `aitest_state` remain, and that
  they can be removed from the project folder with
  `python db_admin.py delete-script <name> --yes` (or `db-admin.ps1`).

**18.3 Final status**
- Do: `get_terminal_status`
- Expect: `alive: true`.

Then write the report table and summary described at the top.
