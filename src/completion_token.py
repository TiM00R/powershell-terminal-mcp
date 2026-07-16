"""
completion_token.py
Completion detection via an in-session prompt override.

The completion token and the output start-marker ride INSIDE OSC escape sequences.
xterm.js (and real terminals) silently swallow unknown OSC sequences, so they are
INVISIBLE on the human terminal, while remaining in the raw byte stream for the AI
detector to scan. This avoids text-stripping the stream (which corrupts a
screen-addressed ConPTY render). See SPEC.md section 4 and the spike lessons.
"""

import re
import uuid

TOKEN_NAME = "PWSH_MCP_READY"

# Private OSC numbers no terminal renders. Token + start marker live inside these.
OSC_TOKEN = "7000"   # completion token (in the prompt)
OSC_START = "7001"   # output start marker (before an AI command's output)

# Strip OSC (BEL- or ST-terminated), single-char escapes, and CSI sequences. Used
# for the stored human-readable buffer and for AI output text (NOT for detection,
# which scans the raw stream so the OSC-wrapped token survives).
ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
    r"|\x1b\[[0-9;?]*[ -/]*[@-~]"
)


def strip_ansi(text):
    return ANSI_RE.sub("", text)


class CompletionToken:
    """Generates the prompt-override snippet and detects/parses the token."""

    def __init__(self, session_uuid=None):
        self.session_uuid = session_uuid or uuid.uuid4().hex
        self.prefix = TOKEN_NAME + ":" + self.session_uuid + ":"
        self.start_marker = "MCP_OUT_START:" + self.session_uuid
        # Detected inside the OSC payload: prefix<True|False>:<exit code>
        self._re = re.compile(re.escape(self.prefix) + r"(True|False):(-?\d+)")

    def wrap_command(self, command):
        """AI command sent as-is. The start marker is emitted by the PSReadLine
        Enter handler (installed in the prompt snippet) right after the line is
        accepted, so the human terminal echoes the CLEAN command (no visible
        wrapper) while the marker stays invisible for output extraction.
        """
        return command

    def prompt_function_snippet(self):
        """PowerShell that (a) wraps the existing prompt, appending the completion
        token inside an INVISIBLE OSC sequence, (b) resets the AI-batch wrap flag on
        each return to the prompt, and (c) installs a PSReadLine Enter handler that
        checks the line for a leading INVISIBLE batch sentinel (U+200B): if present,
        it sets $global:__mcp_ai_batch and strips the sentinel (AI batch -> wrapped
        capture); if absent, it accepts normally (default BYPASS -> stdin open, used
        by humans and the AI interactive path). It then emits the INVISIBLE OSC start
        marker. xterm.js swallows the OSCs; detection scans the raw stream.

        Idempotent (guarded by __mcp_installed). $? captured FIRST. ${ok}/${code}
        braces avoid the drive-qualified-variable parse error.
        """
        u = self.session_uuid
        start_osc = '[char]27 + "]' + OSC_START + ";" + self.start_marker + '" + [char]7'
        return (
            "if (-not $global:__mcp_installed) {\n"
            "  $global:__mcp_prev = $function:prompt\n"
            "  $global:__mcp_installed = $true\n"
            "}\n"
            "function prompt {\n"
            "  $ok = $?\n"
            "  $code = $LASTEXITCODE\n"
            # Reset AFTER capturing $? / $LASTEXITCODE so the assignment does not
            # clobber them. Clears batch mode set by the Enter handler (U+200B
            # sentinel), so the next command (which may be human-typed) defaults to bypass.
            "  $global:__mcp_ai_batch = $false\n"
            "  if ($null -eq $code) { $code = 0 }\n"
            "  $base = ''\n"
            "  if ($global:__mcp_prev) {\n"
            "    try { $base = [string](& $global:__mcp_prev) } catch { $base = '' }\n"
            "  }\n"
            "  $tok = [char]27 + \"]" + OSC_TOKEN + ";" + TOKEN_NAME + ":" + u + ":${ok}:${code}\" + [char]7\n"
            "  \"$base$tok\"\n"
            "}\n"
            "if (Get-Module PSReadLine) {\n"
            "  Set-PSReadLineKeyHandler -Key Enter -ScriptBlock {\n"
            # Detect the invisible AI-batch sentinel (U+200B) at the start of the
            # line: set the wrap flag and strip that one char before accepting. No
            # sentinel => default bypass (humans, AI interactive). $__l is $null on
            # an empty line, so guard before StartsWith.
            "    $__l = $null; $__c = 0\n"
            "    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$__l, [ref]$__c)\n"
            # Only strip the sentinel / emit the start marker when this Enter will
            # actually SUBMIT (input syntactically complete). On the interior CRs of a
            # multi-line block the input is IncompleteInput (e.g. an open brace), so we
            # skip both -- doing them mid-continuation redraws the buffer and scrambles
            # the block (lines reverse, cursor jumps, wedges at '>>'). AcceptLine()
            # still continues-or-submits correctly on its own either way.
            "    if ($null -eq $__l) { $__l = '' }\n"
            "    $__toks = $null; $__errs = $null\n"
            "    $null = [System.Management.Automation.Language.Parser]::ParseInput($__l, [ref]$__toks, [ref]$__errs)\n"
            "    $__submit = -not ($__errs | Where-Object { $_.IncompleteInput })\n"
            "    if ($__submit -and $__l.StartsWith([char]0x200B)) {\n"
            "      $global:__mcp_ai_batch = $true\n"
            "      [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, 1, '')\n"
            "    }\n"
            "    [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()\n"
            "    if ($__submit) { [Console]::Write(" + start_osc + ") }\n"
            "  }\n"
            "}\n"
        )

    def search(self, text, pos=0):
        """Find the next token at or after pos (scan the RAW stream). Returns match."""
        return self._re.search(text, pos)

    @staticmethod
    def parse(match):
        """Return (success_bool, exit_code_int) from a token match."""
        success = (match.group(1) == "True")
        code = int(match.group(2)) if match.group(2) != "" else 0
        return success, code

