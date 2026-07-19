"""
state_history.py - DB-facade + command-logging mixin for SharedTerminalState.

Split out of shared_state.py: everything here talks to self.database -- command
logging (with selective output persistence), conversation lifecycle, history
range queries + startup prune, and the script store.

The mixin relies on attributes owned by SharedTerminalState and set in its
initialize(): self.database, self._active_conversation_id, self.config, and
self.session_output. It defines no state of its own.
"""

import logging
import time
from datetime import datetime, timedelta

from db import should_persist_output
from utils import is_error_output, extract_error_context, count_lines

logger = logging.getLogger(__name__)


def _parse_dt(s, end=False):
    """Parse 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' (naive local time) to epoch
    seconds. For a date-only value, end=True snaps to the last second of that day
    so a from/to range is inclusive of the whole end day."""
    s = (s or "").strip()
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, f)
            if f == "%Y-%m-%d" and end:
                dt = dt + timedelta(days=1) - timedelta(seconds=1)
            return dt.timestamp()
        except ValueError:
            continue
    raise ValueError("bad date (use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS): " + s)


class StateHistoryMixin:
    """DB-backed command logging, conversation tools, history, and script store.

    Mixed into SharedTerminalState; expects self.database, self.config,
    self.session_output, and self._active_conversation_id to exist.
    """

    # --- command logging ----------------------------------------------------

    def _log_command(self, command, result, is_script=False, flagged=False):
        """Log a command with selective full-output persistence (spec section 6)."""
        if not self.database:
            return
        try:
            output = result.get("output", "") or ""
            success = bool(result.get("success"))
            status = result.get("status", "executed")
            try:
                patterns = self.config.claude.error_patterns or []
            except Exception:
                patterns = []
            has_err = (not success) or is_error_output(output, patterns)
            err_ctx = extract_error_context(output) if has_err else None
            reason = should_persist_output(success, is_script=is_script, flagged=flagged)
            self.database.log_command(
                conversation_id=self._active_conversation_id,
                command_text=command,
                exit_code=result.get("exit_code"),
                success=success,
                status=status,
                has_errors=has_err,
                error_context=err_ctx,
                line_count=count_lines(output),
                output_text=(output if reason else None),
                output_persisted=reason,
            )
        except Exception:
            logger.exception("command logging failed")

    def _log_interactive(self, command, result):
        """Best-effort log of an interactive launch. Maps the {state,...} record onto
        the command-log shape; success is unknown mid-session so treat a pending
        prompt as non-error (only a non-zero EXITED code counts as failure)."""
        if not self.database:
            return
        state = result.get("state")
        exit_code = result.get("exit_code")
        mapped = {
            "output": result.get("output", "") or "",
            "success": (exit_code == 0) if exit_code is not None else True,
            "status": ("interactive:" + str(state)) if state else "interactive",
            "exit_code": exit_code,
        }
        self._log_command(command, mapped, is_script=False)

    # --- conversation tools (DB-backed) -------------------------------------

    def start_conversation(self, label=None):
        """Begin a new conversation and make it the one commands are logged under.
        Used to separate a distinct piece of work from prior history."""
        if not self.database:
            return None
        cid = self.database.create_conversation(label)
        self._active_conversation_id = cid
        return cid

    def end_conversation(self, conversation_id=None, status="completed"):
        """Close out a conversation, defaulting to the active one. Closing is what
        makes it eligible for retention pruning later."""
        cid = conversation_id or self._active_conversation_id
        if self.database and cid:
            self.database.end_conversation(cid, status)
        return cid

    def list_conversations(self, limit=20):
        """Browse recent conversations. Degrades to an empty list rather than
        failing when no database is configured."""
        return self.database.list_conversations(limit) if self.database else []

    def get_conversation_commands(self, conversation_id):
        """Read one conversation's commands back in execution order."""
        return (self.database.get_conversation_commands(conversation_id)
                if self.database else [])

    def get_command_history(self, from_date, to_date):
        """Commands across all conversations within a date/time range. Dates accept
        'YYYY-MM-DD' (whole day) or 'YYYY-MM-DD HH:MM:SS'."""
        if not self.database:
            return []
        start = _parse_dt(from_date, end=False)
        end = _parse_dt(to_date, end=True)
        return self.database.get_commands_by_time(start, end)

    def prune_history(self):
        """Startup auto-prune per config.database.retention_days. Never touches the
        active conversation. retention_days <= 0 disables pruning."""
        if not self.database or not self.config:
            return None
        try:
            days = int(getattr(self.config.database, "retention_days", 30) or 0)
        except Exception:
            days = 30
        if days <= 0:
            return None
        cutoff = time.time() - days * 86400
        keep = [self._active_conversation_id] if self._active_conversation_id else []
        result = self.database.prune_older_than(cutoff, keep_ids=keep)
        if result and (result.get("conversations_deleted") or result.get("commands_deleted")):
            logger.info("history prune: %s", result)
        return result

    # --- script store -------------------------------------------------------

    def save_script(self, name, content):
        """Persist a reusable script under a name, so a routine task becomes a
        one-word call instead of a re-typed block."""
        if self.database:
            self.database.save_script(name, content)
        return name

    def list_scripts(self):
        """Names and usage stats of saved scripts (bodies excluded)."""
        return self.database.list_scripts() if self.database else []

    def run_script(self, name, timeout=120.0):
        """Execute a saved script in the live session.

        The body is written to a temp .ps1 and invoked by path rather than pasted
        into the shell, which sidesteps the quoting and multi-line submission
        problems a long block would otherwise hit. The temp file is always removed,
        even on failure, and the run is logged as a script (so its full output IS
        persisted, unlike an ordinary successful command).
        """
        import os
        import tempfile
        sc = self.database.get_script(name) if self.database else None
        if not sc:
            return {"status": "error", "error": "unknown script: " + name}
        fd, path = tempfile.mkstemp(suffix=".ps1", prefix="pwsh_script_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sc["content"])
        try:
            result = self.session_output.run_command("& '" + path + "'", timeout=timeout)
            self._log_command("run_script:" + name, result, is_script=True)
            if self.database:
                self.database.touch_script(name)
            return result
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
