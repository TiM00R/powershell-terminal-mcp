"""
AI batch commands run external programs through wrappers (pwsh_launch.py:
NATIVE_WRAPPERS for a fixed list like python/git, NATIVE_EXE_HOOK for every
other console exe). Piped input must reach the program; today it is dropped.

Each test starts a real `pwsh -NoProfile`, loads ONLY the wrapper code, and
turns on the AI-batch flag. No MCP server, port or browser is used.

  python tests/test_native_wrapper_stdin.py      (no pytest needed)
  python -m pytest tests/test_native_wrapper_stdin.py
"""

import base64
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))   # pwsh package imports its siblings by bare name

from pwsh import pwsh_launch


def ai_batch(body, timeout=30):
    """Run `body` in a fresh pwsh with the session's encoding setup and the
    wrappers loaded (same order as the real init script) and the AI-batch
    flag on. Returns the output lines (blank lines dropped)."""
    script = (pwsh_launch.ENCODING_SETUP + "\n"
              + pwsh_launch.NATIVE_WRAPPERS + "\n"
              + pwsh_launch.NATIVE_EXE_HOOK + "\n"
              + "$global:__mcp_ai_batch = $true\n"
              + body)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    # Put this Python first on PATH so the `python` wrapper is predictable.
    env = dict(os.environ,
               PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=timeout, env=env)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# --- #5: piped input must reach the program (fail on the current code) ------

def test_pipe_into_hook_wrapped_exe():
    # findstr is not in the fixed list, so NATIVE_EXE_HOOK wraps it.
    out = ai_batch('"hello" | findstr hello; "exit=$LASTEXITCODE"')
    assert out == ["hello", "exit=0"]


def test_pipe_into_list_wrapped_exe():
    # python is in NATIVE_WRAPPERS.
    out = ai_batch('"hello" | python -c "import sys; print(repr(sys.stdin.read()))"')
    assert out == ["'hello\\n'"]


def test_pipe_several_lines():
    out = ai_batch('"banana","apple","cherry" | sort.exe')
    assert out == ["apple", "banana", "cherry"]


def test_pipe_with_flag_off_list_wrapper():
    # Human-typed commands (flag off) go through the same python wrapper.
    out = ai_batch('$global:__mcp_ai_batch = $false; '
                   '"hello" | python -c "import sys; print(repr(sys.stdin.read()))"')
    assert out == ["'hello\\n'"]


def test_human_pipe_into_python_has_no_bom():
    # Exact bytes the program receives: no EF BB BF in front.
    out = ai_batch('$global:__mcp_ai_batch = $false; '
                   '"hello" | python -c "import sys; print(sys.stdin.buffer.read())"')
    assert out == ["b'hello\\r\\n'"]


def test_human_pipe_into_unwrapped_exe_has_no_bom():
    # findstr is not wrapped when the flag is off: plain PowerShell piping.
    out = ai_batch('$global:__mcp_ai_batch = $false; "hello" | findstr /n .')
    assert out == ["1:hello"]


def test_python_utf8_mode_on():
    out = ai_batch('python -c "import sys; print(sys.flags.utf8_mode)"')
    assert out == ["1"]


def test_emoji_pipe_round_trip():
    # Read an emoji from the pipe and print it back to a pipe: no garbling, no crash.
    out = ai_batch('"hi \U0001F600" | python -c "import sys; print(ascii(sys.stdin.read()))"')
    assert out == ["'hi \\U0001f600\\n'"]


# --- must keep working ---------------------------------------------------------

def test_stdin_still_closed_without_pipe():
    # The reason the wrapper exists: a program that reads stdin must get
    # end-of-input at once instead of hanging the AI.
    out = ai_batch('python -c "import sys; print(repr(sys.stdin.read()))"', timeout=20)
    assert out == ["''"]


def test_output_and_exit_code_captured():
    out = ai_batch('python -c "import sys; print(\'out\'); sys.exit(3)"; "exit=$LASTEXITCODE"')
    assert out == ["out", "exit=3"]


def test_hook_wrapper_does_not_persist():
    # Guard for #4: after the AI runs an exe, the same name typed later
    # (flag off) must still be the real program, not a leftover wrapper.
    out = ai_batch('whoami | Out-Null; $global:__mcp_ai_batch = $false; '
                   '(Get-Command whoami).CommandType')
    assert out == ["Application"]


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError:
                failed += 1
                print("FAIL", name)
    print("\n%d failed" % failed)
    sys.exit(1 if failed else 0)
