"""
db.py - lean local SQLite store for powershell-terminal-mcp (spec section 6).

Self-contained: stores command metadata + exit codes always; full output_text ONLY
for failures, script runs, or explicitly flagged commands (selective persistence to
avoid bloat). Kept forever (no pruning). Conversations group commands; scripts hold
saved .ps1. No buffer line-pointers, no servers, no machine_id, no rollback.

DB location: <project_root>/data/commands.db
"""

import os
import time
import sqlite3
import threading
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def default_db_path():
    # Project root is one level above src/
    project_root = Path(__file__).parent.parent
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)
    return str(data_dir / "commands.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT,
    status      TEXT DEFAULT 'active',
    started_at  REAL,
    ended_at    REAL
);
CREATE TABLE IF NOT EXISTS commands (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  INTEGER,
    sequence_num     INTEGER,
    command_text     TEXT,
    exit_code        INTEGER,
    success          INTEGER,
    status           TEXT,
    has_errors       INTEGER,
    error_context    TEXT,
    line_count       INTEGER,
    output_text      TEXT,
    output_persisted TEXT,
    executed_at      REAL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
CREATE TABLE IF NOT EXISTS scripts (
    name         TEXT PRIMARY KEY,
    content      TEXT,
    created_at   REAL,
    updated_at   REAL,
    times_used   INTEGER DEFAULT 0,
    last_used_at REAL
);
"""


class Database:
    def __init__(self, path=None):
        self.path = path or default_db_path()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        logger.info("DB ready at %s", self.path)

    # --- conversations ------------------------------------------------------

    def create_conversation(self, label=None):
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversations (label, status, started_at) VALUES (?, 'active', ?)",
                (label, time.time()))
            self._conn.commit()
            return cur.lastrowid

    def end_conversation(self, conversation_id, status="completed"):
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET status=?, ended_at=? WHERE id=?",
                (status, time.time(), conversation_id))
            self._conn.commit()

    def list_conversations(self, limit=20):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- commands -----------------------------------------------------------

    def _next_seq(self, conversation_id):
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence_num), 0) + 1 AS n FROM commands WHERE conversation_id=?",
            (conversation_id,)).fetchone()
        return row["n"]

    def log_command(self, conversation_id, command_text, exit_code, success, status,
                    has_errors, error_context, line_count, output_text=None,
                    output_persisted=None):
        """Insert a command row. Caller decides output_text/output_persisted via the
        selective policy (see should_persist_output)."""
        with self._lock:
            seq = self._next_seq(conversation_id) if conversation_id else None
            cur = self._conn.execute(
                """INSERT INTO commands
                   (conversation_id, sequence_num, command_text, exit_code, success,
                    status, has_errors, error_context, line_count, output_text,
                    output_persisted, executed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (conversation_id, seq, command_text, exit_code,
                 1 if success else 0, status, 1 if has_errors else 0, error_context,
                 line_count, output_text, output_persisted, time.time()))
            self._conn.commit()
            return cur.lastrowid

    def get_conversation_commands(self, conversation_id):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM commands WHERE conversation_id=? ORDER BY sequence_num",
                (conversation_id,)).fetchall()
        return [dict(r) for r in rows]

    # --- scripts ------------------------------------------------------------

    def save_script(self, name, content):
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO scripts (name, content, created_at, updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET content=excluded.content,
                       updated_at=excluded.updated_at""",
                (name, content, now, now))
            self._conn.commit()

    def list_scripts(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, created_at, updated_at, times_used, last_used_at "
                "FROM scripts ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_script(self, name):
        with self._lock:
            row = self._conn.execute("SELECT * FROM scripts WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def touch_script(self, name):
        with self._lock:
            self._conn.execute(
                "UPDATE scripts SET times_used=times_used+1, last_used_at=? WHERE name=?",
                (time.time(), name))
            self._conn.commit()


def should_persist_output(success, is_script=False, flagged=False):
    """Selective full-output persistence policy. Returns a reason string or None.
    Persist full output ONLY for: failed commands, script runs, or flagged ones."""
    if not success:
        return "failed"
    if is_script:
        return "script"
    if flagged:
        return "flagged"
    return None
