"""
Where the history database lives (#9), and that a database that cannot be
opened does not stop the server from starting.

  B: running from source (config.yaml next to src/)  -> <project>/data/commands.db
     installed copy (pip / pipx)                      -> POWERSHELL_TERMINAL_HOME
                                                         or ~/.powershell-terminal
     old history at <site-packages>/data is copied over once
  A: an unopenable database -> continue without history (no crash)

Uses temporary folders only; your real databases are not touched.
  python tests/test_db_location.py      (no pytest needed)
  python -m pytest tests/test_db_location.py
"""

import contextlib
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))   # modules import their siblings by bare name

import db


@contextlib.contextmanager
def env(**values):
    """Temporarily set (or with None, remove) environment variables."""
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def make_install(tmp, source_checkout):
    """Fake install folder: <tmp>/root/src, plus config.yaml at root for a
    source checkout (an installed copy has none there)."""
    root = os.path.join(tmp, "root")
    os.makedirs(os.path.join(root, "src"))
    if source_checkout:
        open(os.path.join(root, "config.yaml"), "w").close()
    return root


# --- must keep working ---------------------------------------------------------

def test_source_checkout_keeps_data_folder():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_install(tmp, source_checkout=True)
        with env(POWERSHELL_TERMINAL_HOME=os.path.join(tmp, "home")):
            path = db.default_db_path(project_root=root)
        assert path == os.path.join(root, "data", "commands.db")


# --- fail on the current code ------------------------------------------------

def test_installed_copy_uses_powershell_terminal_home():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_install(tmp, source_checkout=False)
        home = os.path.join(tmp, "home")
        with env(POWERSHELL_TERMINAL_HOME=home):
            path = db.default_db_path(project_root=root)
        assert path == os.path.join(home, "commands.db")
        assert os.path.isdir(home)                       # folder created
        assert not os.path.exists(os.path.join(root, "data"))   # nothing in site-packages


def test_installed_copy_default_home_folder():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_install(tmp, source_checkout=False)
        user = os.path.join(tmp, "user")
        os.makedirs(user)
        with env(POWERSHELL_TERMINAL_HOME=None, USERPROFILE=user, HOME=user):
            path = db.default_db_path(project_root=root)
        assert path == os.path.join(user, ".powershell-terminal", "commands.db")


def test_installed_copy_migrates_old_history_once():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_install(tmp, source_checkout=False)
        old = os.path.join(root, "data", "commands.db")
        os.makedirs(os.path.dirname(old))
        with open(old, "wb") as f:
            f.write(b"old history")
        home = os.path.join(tmp, "home")
        with env(POWERSHELL_TERMINAL_HOME=home):
            path = db.default_db_path(project_root=root)
        with open(path, "rb") as f:
            assert f.read() == b"old history"            # copied over
        assert os.path.exists(old)                       # original left in place


def test_existing_new_history_is_not_overwritten():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_install(tmp, source_checkout=False)
        old = os.path.join(root, "data", "commands.db")
        os.makedirs(os.path.dirname(old))
        with open(old, "wb") as f:
            f.write(b"old history")
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        with open(os.path.join(home, "commands.db"), "wb") as f:
            f.write(b"newer history")
        with env(POWERSHELL_TERMINAL_HOME=home):
            path = db.default_db_path(project_root=root)
        assert path == os.path.join(home, "commands.db")
        with open(path, "rb") as f:
            assert f.read() == b"newer history"


def test_unopenable_database_does_not_stop_startup():
    from config.config_loader import Config
    from shared_state import SharedTerminalState
    cfg = Config(os.path.join(ROOT, "config.yaml"))
    with tempfile.TemporaryDirectory() as tmp:
        blocker = os.path.join(tmp, "a_file")
        open(blocker, "w").close()
        cfg.database.path = os.path.join(blocker, "commands.db")   # parent is a file: cannot open
        state = SharedTerminalState()
        state.initialize(cfg)                                       # today: raises
        assert state.database is None                               # running without history
        assert state.session_output is not None                     # rest of startup done


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:          # today some fail with TypeError, not AssertionError
                failed += 1
                print("FAIL", name, "-", type(e).__name__, str(e)[:100])
    print("\n%d failed" % failed)
    sys.exit(1 if failed else 0)
