"""
db_admin.py - manual maintenance CLI for the powershell-terminal command store.

Kept OFF the MCP tool surface on purpose: destructive maintenance is rare and must
not be reachable by the AI mid-debug. Run it yourself (see db-admin.ps1 wrapper).

Destructive commands (clean-stale, delete-ids, prune) DEFAULT TO A DRY RUN and only
execute when you pass --yes, so you always see what will be removed first.

DB path resolution: config.yaml database.path if set, else db.default_db_path()
(<project_root>/data/commands.db). Override with --db PATH.

Usage:
  python db_admin.py list
  python db_admin.py clean-stale            # dry run: active + unnamed, keep newest
  python db_admin.py clean-stale --yes
  python db_admin.py delete-ids 3 4 5       # dry run
  python db_admin.py delete-ids 3 4 5 --yes
  python db_admin.py prune --days 30        # dry run (active conv protected)
  python db_admin.py prune --days 30 --yes
  python db_admin.py vacuum
  python db_admin.py integrity
"""

import os
import sys
import time
import sqlite3
import argparse

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, SRC)

import db as dbmod   # noqa: E402


def resolve_db_path(explicit=None):
    if explicit:
        return explicit
    try:
        from config.config_loader import Config
        cfg = Config(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "config.yaml"))
        if cfg.database and cfg.database.path:
            return cfg.database.path
    except Exception:
        pass
    return dbmod.default_db_path()


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_ts(v):
    if not v:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(v)))
    except Exception:
        return str(v)


def cmd_list(conn, args):
    rows = conn.execute(
        "SELECT c.id, c.label, c.status, c.started_at, c.ended_at, "
        "(SELECT COUNT(*) FROM commands m WHERE m.conversation_id=c.id) AS cmds "
        "FROM conversations c ORDER BY c.id DESC").fetchall()
    print("id    status      cmds  started              ended                label")
    print("-" * 84)
    for r in rows:
        print("%-5s %-11s %-5s %-20s %-20s %s" % (
            r["id"], r["status"], r["cmds"], _fmt_ts(r["started_at"]),
            _fmt_ts(r["ended_at"]), r["label"] if r["label"] is not None else "(none)"))
    print("-" * 84)
    print("total conversations:", len(rows))


def _stale_victims(conn):
    """active + unnamed (label IS NULL or 'session'), keep the newest such id."""
    rows = conn.execute(
        "SELECT id FROM conversations "
        "WHERE status='active' AND (label IS NULL OR label='session') "
        "ORDER BY id DESC").fetchall()
    ids = [r["id"] for r in rows]
    keep = ids[0] if ids else None
    victims = ids[1:] if len(ids) > 1 else []
    return keep, victims


def _delete_conversations(conn, ids):
    cmd_del = 0
    conv_del = 0
    for cid in ids:
        cur = conn.execute("DELETE FROM commands WHERE conversation_id=?", (cid,))
        cmd_del += cur.rowcount
        cur = conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        conv_del += cur.rowcount
    conn.commit()
    return conv_del, cmd_del


def cmd_clean_stale(conn, args):
    keep, victims = _stale_victims(conn)
    print("active + unnamed conversations")
    print("keep (newest):", keep)
    print("delete       :", victims if victims else "(none)")
    if not victims:
        return
    if not args.yes:
        print("\nDRY RUN. Re-run with --yes to delete the above.")
        return
    conv_del, cmd_del = _delete_conversations(conn, victims)
    print("\ndeleted conversations:", conv_del, " commands:", cmd_del)


def cmd_delete_ids(conn, args):
    ids = list(dict.fromkeys(args.ids))
    print("delete conversation ids:", ids)
    if not args.yes:
        print("DRY RUN. Re-run with --yes to delete.")
        return
    conv_del, cmd_del = _delete_conversations(conn, ids)
    print("deleted conversations:", conv_del, " commands:", cmd_del)


def cmd_delete_commands(conn, args):
    ids = list(dict.fromkeys(args.ids))
    rows = conn.execute(
        "SELECT id, conversation_id, command_text FROM commands "
        "WHERE id IN (%s)" % ",".join("?" * len(ids)), ids).fetchall()
    print("delete command rows:", ids)
    for r in rows:
        txt = (r["command_text"] or "")
        print("  id %-5s conv %-5s %s" % (
            r["id"], r["conversation_id"], (txt[:70] + "...") if len(txt) > 70 else txt))
    if not args.yes:
        print("DRY RUN. Re-run with --yes to delete.")
        return
    cur = conn.execute(
        "DELETE FROM commands WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
    conn.commit()
    print("deleted command rows:", cur.rowcount)


def cmd_delete_script(conn, args):
    row = conn.execute("SELECT name FROM scripts WHERE name=?", (args.name,)).fetchone()
    print("delete script:", args.name, "(exists)" if row else "(not found)")
    if not row:
        return
    if not args.yes:
        print("DRY RUN. Re-run with --yes to delete.")
        return
    cur = conn.execute("DELETE FROM scripts WHERE name=?", (args.name,))
    conn.commit()
    print("deleted scripts:", cur.rowcount)


def cmd_prune(conn, args):
    days = args.days
    cutoff = time.time() - days * 86400
    keep_row = conn.execute(
        "SELECT id FROM conversations WHERE status='active' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    keep = [keep_row["id"]] if keep_row else []
    rows = conn.execute(
        "SELECT c.id AS id, "
        "COALESCE(c.ended_at, x.last_cmd, c.started_at) AS last_activity "
        "FROM conversations c "
        "LEFT JOIN (SELECT conversation_id, MAX(executed_at) AS last_cmd "
        "           FROM commands GROUP BY conversation_id) x "
        "ON x.conversation_id = c.id").fetchall()
    victims = [r["id"] for r in rows
               if r["id"] not in set(keep)
               and r["last_activity"] is not None
               and r["last_activity"] < cutoff]
    print("prune older than", days, "days (keep active:", keep, ")")
    print("delete:", victims if victims else "(none)")
    if not victims:
        return
    if not args.yes:
        print("DRY RUN. Re-run with --yes to delete.")
        return
    conv_del, cmd_del = _delete_conversations(conn, victims)
    print("deleted conversations:", conv_del, " commands:", cmd_del)


def cmd_show(conn, args):
    convs = conn.execute(
        "SELECT id, label, status, started_at, ended_at FROM conversations "
        "ORDER BY id DESC").fetchall()
    for c in convs:
        cmds = conn.execute(
            "SELECT sequence_num, command_text, exit_code, status, executed_at "
            "FROM commands WHERE conversation_id=? ORDER BY sequence_num",
            (c["id"],)).fetchall()
        print("=" * 92)
        print("conv %s | %s | %s | started %s | cmds %d" % (
            c["id"], c["status"],
            c["label"] if c["label"] is not None else "(none)",
            _fmt_ts(c["started_at"]), len(cmds)))
        print("-" * 92)
        for m in cmds:
            txt = (m["command_text"] or "").replace("\n", " ")
            if len(txt) > 56:
                txt = txt[:56] + "..."
            print("  %-3s %-16s exit=%-6s %-24s %s" % (
                m["sequence_num"], _fmt_ts(m["executed_at"]),
                m["exit_code"], m["status"], txt))
    print("=" * 92)
    print("total conversations:", len(convs))


def cmd_vacuum(conn, args):
    conn.execute("VACUUM")
    conn.commit()
    print("vacuum done")


def cmd_integrity(conn, args):
    r = conn.execute("PRAGMA integrity_check").fetchone()
    print("integrity_check:", r[0])


def main():
    p = argparse.ArgumentParser(description="powershell-terminal DB admin")
    p.add_argument("--db", default=None, help="explicit DB path override")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list")
    sub.add_parser("show")

    sp = sub.add_parser("clean-stale")
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser("delete-ids")
    sp.add_argument("ids", nargs="+", type=int)
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser("delete-commands")
    sp.add_argument("ids", nargs="+", type=int)
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser("delete-script")
    sp.add_argument("name")
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser("prune")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--yes", action="store_true")

    sub.add_parser("vacuum")
    sub.add_parser("integrity")

    args = p.parse_args()
    path = resolve_db_path(args.db)
    print("DB:", path)
    conn = connect(path)
    try:
        dispatch = {
            "list": cmd_list,
            "show": cmd_show,
            "clean-stale": cmd_clean_stale,
            "delete-ids": cmd_delete_ids,
            "delete-commands": cmd_delete_commands,
            "delete-script": cmd_delete_script,
            "prune": cmd_prune,
            "vacuum": cmd_vacuum,
            "integrity": cmd_integrity,
        }
        dispatch[args.command](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
