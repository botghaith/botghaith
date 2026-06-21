#!/usr/bin/env python3
"""نقل البيانات من SQLite المحلي إلى Supabase."""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DATABASE_PATH, is_supabase_enabled
from services.supabase_client import get_supabase


def _rows(conn, table: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(f"SELECT * FROM {table}")
    return [dict(r) for r in cur.fetchall()]


def _json_field(val):
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    return json.loads(val)


def migrate():
    if not is_supabase_enabled():
        print("❌ Supabase غير مُعد في .env")
        sys.exit(1)

    db_path = Path(DATABASE_PATH)
    if not db_path.exists():
        print(f"❌ ملف SQLite غير موجود: {db_path}")
        sys.exit(1)

    client = get_supabase()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    stats = {}

    # ── settings ──
    rows = _rows(conn, "settings")
    if rows:
        client.table("settings").upsert(rows, on_conflict="key").execute()
    stats["settings"] = len(rows)

    # ── users ──
    rows = _rows(conn, "users")
    if rows:
        batch_size = 100
        for i in range(0, len(rows), batch_size):
            client.table("users").upsert(rows[i : i + batch_size], on_conflict="user_id").execute()
    stats["users"] = len(rows)

    # ── required_channels ──
    rows = _rows(conn, "required_channels")
    if rows:
        for r in rows:
            r.pop("id", None)
        client.table("required_channels").upsert(rows, on_conflict="username").execute()
    stats["required_channels"] = len(rows)

    # ── exams ──
    rows = _rows(conn, "exams")
    exam_rows = []
    for r in rows:
        d = dict(r)
        d["questions"] = _json_field(d["questions"])
        exam_rows.append(d)
    if exam_rows:
        client.table("exams").upsert(exam_rows, on_conflict="exam_id").execute()
    stats["exams"] = len(exam_rows)

    # ── exam_results ──
    rows = _rows(conn, "exam_results")
    result_rows = []
    for r in rows:
        d = dict(r)
        d.pop("id", None)
        d["answers"] = _json_field(d.get("answers"))
        result_rows.append(d)
    if result_rows:
        batch_size = 100
        for i in range(0, len(result_rows), batch_size):
            client.table("exam_results").insert(result_rows[i : i + batch_size]).execute()
    stats["exam_results"] = len(result_rows)

    # ── admin_questions ──
    rows = _rows(conn, "admin_questions")
    q_rows = []
    for r in rows:
        d = dict(r)
        d.pop("id", None)
        d["options"] = _json_field(d["options"])
        q_rows.append(d)
    if q_rows:
        client.table("admin_questions").insert(q_rows).execute()
    stats["admin_questions"] = len(q_rows)

    # ── activity_log ──
    rows = _rows(conn, "activity_log")
    log_rows = []
    for r in rows:
        d = dict(r)
        d.pop("id", None)
        log_rows.append(d)
    if log_rows:
        batch_size = 200
        for i in range(0, len(log_rows), batch_size):
            client.table("activity_log").insert(log_rows[i : i + batch_size]).execute()
    stats["activity_log"] = len(log_rows)

    conn.close()

    print("\nتم النقل بنجاح:\n")
    for table, count in stats.items():
        print(f"  {table}: {count}")

    verify = client.table("users").select("*", count="exact").limit(0).execute()
    print(f"\nالمستخدمون في Supabase الآن: {verify.count}")


if __name__ == "__main__":
    migrate()
