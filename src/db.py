"""
db.py - lean local SQLite store for powershell-terminal-mcp (spec section 6).

The memory of the session: what was run, whether it worked, and what it printed
when that matters. It exists so history survives a server restart and so a failure
can be investigated after the fact, not so every byte is archived.

Selective persistence is the central policy (see should_persist_output): metadata
and exit codes are stored for every command, but full output_text only for
failures, script runs, or explicitly flagged commands. Successful commands are the
bulk of traffic and their output is the least interesting, so dropping it is what
keeps the DB small enough to never need attention.

Conversations group commands; scripts hold saved .ps1. Old conversations are pruned
on startup per config database.retention_days. No buffer line-pointers, no servers,
no machine_id, no rollback.

DB location: config database.path, else <project_root>/data/commands.db
"""

import time
import sqlite3
import threading
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def default_db_path():
    """Locate the DB relative to the install so the store follows the project
    rather than the working directory (which is unpredictable for an MCP server
    launched by Claude Desktop). Config database.path overrides this."""
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
    """Owns the SQLite connection and every statement issued against it.

    All SQL lives here so the rest of the app deals in dicts and never in cursors;
    callers supply policy (what to keep, what to prune) and this class only
    executes it.
    """

    def __init__(self, path=None):
        """Open (creating if needed) the store and ensure the schema exists.

        check_same_thread=False because the MCP server, the web server, and the
        reader thread all reach the DB through this one object; a mutex serializes
        them instead of SQLite's per-thread guard.
        """
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
        """Open a new conversation -- the grouping every logged command hangs off."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversations (label, status, started_at) VALUES (?, 'active', ?)",
                (label, time.time()))
            self._conn.commit()
            return cur.lastrowid

    def end_conversation(self, conversation_id, status="completed"):
        """Mark a conversation ended. ended_at reflects the LAST ACTIVITY (newest
        command's executed_at), not the moment we close it -- so retroactively
        closing a stale conversation records when work actually stopped. Falls back
        to started_at (no commands), then to now (nothing else known)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(executed_at) AS last_cmd FROM commands "
                "WHERE conversation_id=?", (conversation_id,)).fetchone()
            conv = self._conn.execute(
                "SELECT started_at FROM conversations WHERE id=?",
                (conversation_id,)).fetchone()
            if row and row["last_cmd"] is not None:
                ended = row["last_cmd"]
            elif conv and conv["started_at"] is not None:
                ended = conv["started_at"]
            else:
                ended = time.time()
            self._conn.execute(
                "UPDATE conversations SET status=?, ended_at=? WHERE id=?",
                (status, ended, conversation_id))
            self._conn.commit()

    def get_active_conversation(self):
        """Newest still-open conversation, if any. This is what lets a restarted
        server resume the existing thread instead of fragmenting history into a new
        one on every launch."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE status='active' "
                "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def list_conversations(self, limit=20):
        """Recent conversations, newest first -- the index the user browses when
        deciding which history to look at."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- commands -----------------------------------------------------------

    def _next_seq(self, conversation_id):
        """Position of the next command within its conversation, so history reads
        back in execution order regardless of global row ids. Assumes the caller
        already holds the lock."""
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
        """Replay one conversation in order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM commands WHERE conversation_id=? ORDER BY sequence_num",
                (conversation_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_commands_by_time(self, start, end):
        """Commands executed within [start, end] (epoch seconds), across all
        conversations, ordered by time."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM commands WHERE executed_at BETWEEN ? AND ? "
                "ORDER BY executed_at", (start, end)).fetchall()
        return [dict(r) for r in rows]

    def prune_older_than(self, cutoff, keep_ids=None):
        """Delete conversations (and their commands) whose last activity is older
        than cutoff (epoch seconds). Last activity = COALESCE(ended_at, newest
        command executed_at, started_at). Never deletes an id in keep_ids. Also
        removes orphan commands (no conversation) older than cutoff. Returns a
        {conversations_deleted, commands_deleted} summary."""
        keep = set(keep_ids or [])
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id AS id, "
                "COALESCE(c.ended_at, x.last_cmd, c.started_at) AS last_activity "
                "FROM conversations c "
                "LEFT JOIN (SELECT conversation_id, MAX(executed_at) AS last_cmd "
                "           FROM commands GROUP BY conversation_id) x "
                "ON x.conversation_id = c.id").fetchall()
            victims = [r["id"] for r in rows
                       if r["id"] not in keep
                       and r["last_activity"] is not None
                       and r["last_activity"] < cutoff]
            conv_deleted = 0
            cmd_deleted = 0
            for cid in victims:
                cur = self._conn.execute(
                    "DELETE FROM commands WHERE conversation_id=?", (cid,))
                cmd_deleted += cur.rowcount
                cur = self._conn.execute(
                    "DELETE FROM conversations WHERE id=?", (cid,))
                conv_deleted += cur.rowcount
            cur = self._conn.execute(
                "DELETE FROM commands WHERE conversation_id IS NULL AND executed_at < ?",
                (cutoff,))
            cmd_deleted += cur.rowcount
            self._conn.commit()
        return {"conversations_deleted": conv_deleted, "commands_deleted": cmd_deleted}

    # --- scripts ------------------------------------------------------------

    def save_script(self, name, content):
        """Store a reusable .ps1 by name, overwriting any previous body. Scripts are
        kept in the DB rather than on disk so they travel with the history and need
        no file management."""
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
        """Saved scripts with usage metadata, deliberately without their bodies so
        listing stays cheap."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, created_at, updated_at, times_used, last_used_at "
                "FROM scripts ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_script(self, name):
        """Fetch one script including its content, for execution."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM scripts WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def touch_script(self, name):
        """Record a run. The usage counters are what make a stale or unused script
        obvious later."""
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
