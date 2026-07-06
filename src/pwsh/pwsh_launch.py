"""
pwsh_launch.py
Shell discovery, ConPTY spawn, and the session-init script (UTF-8 + prompt
override). Kept small and side-effect-light so it is easy to test.
"""

import os
import shutil
import tempfile
import logging

from winpty import PtyProcess

logger = logging.getLogger(__name__)

DEFAULT_DIMENSIONS = (40, 200)  # (rows, cols); wide to avoid prompt wrapping
READ_CHUNK = 4096

# Force UTF-8 so the buffer never corrupts on non-ASCII output (top Windows gotcha).
# Also move out of the system/launch directory to a sensible default working folder:
# POWERSHELL_TERMINAL_HOME if set, else the user's home ($HOME / USERPROFILE).
ENCODING_SETUP = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
    "$OutputEncoding = [System.Text.Encoding]::UTF8\n"
    "Set-ExecutionPolicy -Scope Process Bypass -Force\n"
    # Restore PATHEXT in case the MCP server inherited a stripped-down value (e.g. only .CPL).
    # Without .EXE in PATHEXT, bare command names like 'git' and 'ipconfig' are not resolved.
    "$env:PATHEXT = '.COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC'\n"
    "$__start = $env:POWERSHELL_TERMINAL_HOME\n"
    "if (-not $__start) { $__start = $HOME }\n"
    "if (-not $__start) { $__start = $env:USERPROFILE }\n"
    "if ($__start -and (Test-Path $__start)) { Set-Location $__start }\n"
)


# NATIVE_WRAPPERS: proactively wraps known short exe names at session start.
# Covers python/git/node/etc. by name -- zero per-command overhead.
# .GetNewClosure() works here because we are in normal session scope (not a hook).
# Each loop iteration captures the correct $__src for that exe via closure.
# Both "python" and "python.exe" forms are registered so either invocation works.
NATIVE_WRAPPERS = (
    "foreach ($__exe in @('python','python3','node','npm','npx','git','cargo','go','java','javac','pip','pip3','docker','kubectl','ffmpeg')) {\n"
    "  $__cmd = Get-Command $__exe -ErrorAction SilentlyContinue\n"
    "  if (-not $__cmd) { continue }\n"
    "  $__src = $__cmd.Source\n"
    "  $__fn = {\n"
    "    param()\n"
    "    $__live = (Get-Command $__exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source\n"
    "    if (-not $__live) { $__live = $__src }\n"
    "    $psi = [System.Diagnostics.ProcessStartInfo]::new($__live)\n"
    "    $psi.UseShellExecute = $false\n"
    "    $psi.CreateNoWindow = $true\n"
    "    $psi.RedirectStandardInput = $true\n"
    "    $psi.RedirectStandardOutput = $true\n"
    "    $psi.RedirectStandardError = $true\n"
    "    foreach ($a in $args) { $psi.ArgumentList.Add($a) }\n"
    "    $proc = [System.Diagnostics.Process]::Start($psi)\n"
    "    if ($proc) {\n"
    "      $proc.StandardInput.Close()\n"
    "      $outTask = $proc.StandardOutput.ReadToEndAsync()\n"
    "      $errTask = $proc.StandardError.ReadToEndAsync()\n"
    "      $proc.WaitForExit()\n"
    "      [System.Threading.Tasks.Task]::WhenAll($outTask,$errTask) | Out-Null\n"
    "      if ($outTask.Result) { Write-Host $outTask.Result -NoNewline }\n"
    "      if ($errTask.Result) { [Console]::Error.Write($errTask.Result) }\n"
    "      $global:LASTEXITCODE = $proc.ExitCode\n"
    "    }\n"
    "  }.GetNewClosure()\n"
    "  New-Item -Force -Path Function: -Name $__exe -Value $__fn | Out-Null\n"
    "  New-Item -Force -Path Function: -Name ($__exe + '.exe') -Value $__fn | Out-Null\n"
    "}\n"
    "Remove-Variable __exe, __cmd, __src, __fn -ErrorAction SilentlyContinue\n"
)

# NATIVE_EXE_HOOK: global PostCommandLookupAction that catches ALL native console exes
# not already covered by NATIVE_WRAPPERS -- including full-path invocations like
# D:\path\to\foo.exe and any exe not in the list above.
#
# Previous attempts used $MyInvocation.MyCommand.Name to look up the exe path at
# invocation time, but that is null when PS invokes a function via $EventArgs.Command
# (PS dispatches the ScriptBlock directly, bypassing named function lookup).
#
# Fix: use [scriptblock]::Create() to embed the exe path as a LITERAL STRING in the
# scriptblock source -- no name lookup needed at call time. The path is resolved once
# inside the hook (where $exe IS in scope) and baked into the generated code string.
#
# PE header Subsystem check (offset 0x3C->PE offset->0x5C, value 3 = CUI/console)
# skips GUI apps (notepad, code.exe, etc.) so they open normally without -Wait.
# PE results are cached per exe path to avoid re-reading on every invocation.
# Guard flag prevents re-registering the hook on reassert_prompt.
NATIVE_EXE_HOOK = (
    "if (-not $global:__mcp_hook_active) {\n"
    "  $global:__mcp_hook_active = $true\n"
    "  $global:__mcp_pe_cache = @{}\n"
    "  $ExecutionContext.InvokeCommand.PostCommandLookupAction = {\n"
    "    param($Name, $EventArgs)\n"
    "    try {\n"
    "      if (-not ($EventArgs.Command -is [System.Management.Automation.ApplicationInfo])) { return }\n"
    "      $exe = $EventArgs.Command.Path\n"
    "      if (-not $global:__mcp_pe_cache.ContainsKey($exe)) {\n"
    "        try {\n"
    "          $b = [System.IO.File]::ReadAllBytes($exe)\n"
    "          $off = [System.BitConverter]::ToInt32($b, 0x3C)\n"
    "          $sub = [System.BitConverter]::ToUInt16($b, $off + 0x5C)\n"
    "          $global:__mcp_pe_cache[$exe] = ($sub -eq 3)\n"
    "        } catch { $global:__mcp_pe_cache[$exe] = $false }\n"
    "      }\n"
    "      if (-not $global:__mcp_pe_cache[$exe]) { return }\n"
    "      if ($Name -match \"[\\\\:]\") {\n"
    "        $funcName = \"__mcp_\" + [Math]::Abs($exe.GetHashCode()).ToString()\n"
    "      } else {\n"
    "        $funcName = $Name\n"
    "      }\n"
    "      $exeQ = $exe.Replace(\"'\", \"''\")\n"
    "      $code = \"param(); `$psi = [System.Diagnostics.ProcessStartInfo]::new('$exeQ'); `$psi.UseShellExecute = `$false; `$psi.CreateNoWindow = `$true; `$psi.RedirectStandardInput = `$true; `$psi.RedirectStandardOutput = `$true; `$psi.RedirectStandardError = `$true; foreach (`$a in `$args) { `$psi.ArgumentList.Add(`$a) }; `$proc = [System.Diagnostics.Process]::Start(`$psi); if (`$proc) { `$proc.StandardInput.Close(); `$outTask = `$proc.StandardOutput.ReadToEndAsync(); `$errTask = `$proc.StandardError.ReadToEndAsync(); `$proc.WaitForExit(); [System.Threading.Tasks.Task]::WhenAll(`$outTask,`$errTask) | Out-Null; if (`$outTask.Result) { Write-Host `$outTask.Result -NoNewline }; if (`$errTask.Result) { [Console]::Error.Write(`$errTask.Result) }; `$global:LASTEXITCODE = `$proc.ExitCode }\"\n"
    "      $fn = [scriptblock]::Create($code)\n"
    "      New-Item -Force -Path Function: -Name $funcName -Value $fn | Out-Null\n"
    "      $fi = $ExecutionContext.InvokeCommand.GetCommand($funcName, 'Function')\n"
    "      if ($fi) { $EventArgs.Command = $fi; $EventArgs.StopSearch = $true }\n"
    "    } catch { }\n"
    "  }\n"
    "}\n"
)

# Clear screen + scrollback + home, emitted from inside the sourced script so it
# runs through ConPTY (ConPTY's cursor model stays in sync) and wipes the one-time
# dot-source echo line.
CLEAR_SCREEN = (
    "[Console]::Write([char]27 + \"[2J\" + [char]27 + \"[3J\" + [char]27 + \"[H\")\n"
)

# Banner shown ONLY in the MCP session (this temp script is not your $PROFILE).
# Emitted as script output (Write-Host) so there is no command echo, and ConPTY
# counts the rows so the first prompt sits correctly below it.
BANNER_LINES = (
    "PowerShell Terminal - shared session (AI + you)",
    "Multi-terminal sync: type in ANY terminal, see in ALL terminals.",
    "Tip: right-click for Copy/Paste, or Ctrl+Shift+C / Ctrl+Shift+V",
)


def _banner_ps():
    out = "".join("Write-Host '" + line + "'\n" for line in BANNER_LINES)
    return out + "Write-Host ''\n"


def find_shell(preferred=None):
    """Prefer pwsh (PS7); fall back to powershell.exe (5.1)."""
    if preferred and shutil.which(preferred):
        return preferred
    if shutil.which("pwsh"):
        return "pwsh"
    return "powershell.exe"


def spawn_command(shell):
    """Build the spawn command. Profile is LOADED (no -NoProfile) per spec."""
    return shell + " -NoLogo"


def spawn(shell_cmd, dimensions=DEFAULT_DIMENSIONS):
    """Spawn the shell inside a ConPTY and return the PtyProcess."""
    logger.debug("spawning ConPTY: %s dims=%s", shell_cmd, dimensions)
    return PtyProcess.spawn(shell_cmd, dimensions=dimensions)


def write_init_script(token):
    """Write UTF-8 setup + prompt override + clear + banner to a temp .ps1 (ASCII).
    Return path.

    Injected by dot-sourcing rather than typing a long quoted line, which avoids
    the PowerShell quoting / line-continuation pitfalls found in the spike. The
    clear + banner run as sourced-script statements, so they render through ConPTY
    with no command echo.
    """
    content = (
        ENCODING_SETUP + "\n"
        + NATIVE_WRAPPERS + "\n"
        + NATIVE_EXE_HOOK + "\n"
        + token.prompt_function_snippet() + "\n"
        + CLEAR_SCREEN
        + _banner_ps()
    )
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="pwsh_mcp_init_")
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(content)
    logger.debug("init script written: %s", path)
    return path
