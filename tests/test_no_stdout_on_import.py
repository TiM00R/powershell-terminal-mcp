"""
Loading the package must never print to stdout. stdout is the MCP JSON-RPC
channel, and the installed powershell-terminal-mcp.exe loads the `src`
package before anything else, so a stray line breaks the connection to Claude.

  python tests/test_no_stdout_on_import.py      (no pytest needed)
  python -m pytest tests/test_no_stdout_on_import.py
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def stdout_when_imported(argv0):
    """Import `src` fresh as if the running program were argv0, and return
    whatever it printed to stdout."""
    for name in [m for m in sys.modules if m == "src" or m.startswith("src.")]:
        del sys.modules[name]                 # forget earlier imports: run __init__ again
    saved_argv, saved_out = sys.argv, sys.stdout
    buf = io.StringIO()
    sys.argv, sys.stdout = [argv0], buf       # pretend program path, capture stdout
    try:
        import src  # noqa: F401
    finally:
        sys.argv, sys.stdout = saved_argv, saved_out
    return buf.getvalue()


# --- fail on the current code ------------------------------------------------

def test_pipx_install_prints_nothing():
    out = stdout_when_imported(
        r"C:\Users\me\.local\pipx\venvs\powershell-terminal-mcp\Scripts\powershell-terminal-mcp.exe")
    assert out == ""


def test_path_containing_pip_prints_nothing():
    out = stdout_when_imported(r"D:\pipeline\tools\venv\Scripts\powershell-terminal-mcp.exe")
    assert out == ""


# --- must keep working --------------------------------------------------------

def test_normal_install_prints_nothing():
    out = stdout_when_imported(r"D:\apps\venv\Scripts\powershell-terminal-mcp.exe")
    assert out == ""


def test_version_still_available():
    stdout_when_imported(r"D:\apps\venv\Scripts\powershell-terminal-mcp.exe")
    import src
    assert isinstance(src.__version__, str) and src.__version__


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
