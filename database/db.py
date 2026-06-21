import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config import DATABASE_PATH, CHANNEL_USERNAME, CHANNEL_LINK, CHANNEL_REQUIRED


class Database:
    def __init__(self, db_path: Path = DATABASE_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    points INTEGER DEFAULT 0,
                    exams_taken INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS exams (
                    exam_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    questions TEXT NOT NULL,
                    duration_minutes INTEGER DEFAULT 30,
                    created_by INTEGER,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS exam_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exam_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    score REAL NOT NULL,
                    total INTEGER NOT NULL,
                    percentage REAL NOT NULL,
                    answers TEXT,
                    completed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (exam_id) REFERENCES exams(exam_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS admin_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    options TEXT NOT NULL,
                    correct_index INTEGER NOT NULL,
                    category TEXT DEFAULT 'general',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT,
                    details TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS required_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    title TEXT DEFAULT '',
                    link TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            self._migrate_exams(conn)
            self._migrate_channel_settings(conn)
            self._migrate_channels_table(conn)

    def _migrate_channels_table(self, conn):
        count = conn.execute("SELECT COUNT(*) FROM required_channels").fetchone()[0]
        if count > 0:
            return
        row_u = conn.execute(
            "SELECT value FROM settings WHERE key = 'channel_username'"
        ).fetchone()
        row_l = conn.execute(
            "SELECT value FROM settings WHERE key = 'channel_link'"
        ).fetchone()
        username = self._normalize_channel_username(
            (row_u["value"] if row_u else "") or CHANNEL_USERNAME
        )
        if not username:
            return
        link = (row_l["value"] if row_l else "") or CHANNEL_LINK
        if not link:
            link = f"https://t.me/{username}"
        conn.execute(
            """INSERT OR IGNORE INTO required_channels (username, title, link, is_active)
               VALUES (?, ?, ?, 1)""",
            (username, f"@{username}", link),
        )

    def _normalize_channel_username(self, username: str) -> str:
        u = (username or "").strip()
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if u.lower().startswith(prefix):
                u = u[len(prefix):]
        return u.split("/")[0].split("?")[0].lstrip("@").strip()

    def _migrate_channel_settings(self, conn):
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('channel_required', ?)",
            ("1" if CHANNEL_REQUIRED else "0",),
        )
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'channel_username'"
        ).fetchone()
        if row and row["value"]:
            fixed = self._normalize_channel_username(row["value"])
            if fixed != row["value"]:
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'channel_username'",
                    (fixed,),
                )
        elif CHANNEL_USERNAME:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('channel_username', ?)",
                (CHANNEL_USERNAME,),
            )
        if not conn.execute(
            "SELECT 1 FROM settings WHERE key = 'channel_link' AND value != ''"
        ).fetchone() and CHANNEL_LINK:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('channel_link', ?)",
                (CHANNEL_LINK,),
            )

    def _migrate_exams(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(exams)").fetchall()}
        migrations = [
            ("description", "TEXT DEFAULT ''"),
            ("expires_at", "TEXT"),
            ("allow_multiple", "INTEGER DEFAULT 0"),
            ("is_published", "INTEGER DEFAULT 1"),
        ]
        for col, typedef in migrations:
            if col not in cols:
                conn.execute(f"ALTER TABLE exams ADD COLUMN {col} {typedef}")

    # ── Settings ──

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    def is_channel_required_enabled(self) -> bool:
        return self.get_setting("channel_required", "1" if CHANNEL_REQUIRED else "0") == "1"

    def set_channel_required(self, enabled: bool):
        self.set_setting("channel_required", "1" if enabled else "0")

    def list_channels(self, active_only: bool = False) -> list[dict]:
        with self._connect() as conn:
            sql = "SELECT * FROM required_channels"
            if active_only:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY id ASC"
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_channel(self, channel_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM required_channels WHERE id = ?", (channel_id,)
            ).fetchone()
            return dict(row) if row else None

    def add_channel(self, username: str, link: str, title: str = "") -> int:
        username = self._normalize_channel_username(username)
        if not title:
            title = f"@{username}"
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO required_channels (username, title, link, is_active)
                   VALUES (?, ?, ?, 1)""",
                (username, title, link),
            )
            return cur.lastrowid

    def update_channel(self, channel_id: int, **fields) -> bool:
        allowed = {"username", "title", "link", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "username" in updates:
            updates["username"] = self._normalize_channel_username(updates["username"])
        if "link" in updates:
            updates["link"] = updates["link"].strip()
        cols = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [channel_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE required_channels SET {cols} WHERE id = ?", vals)
            return True

    def delete_channel(self, channel_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM required_channels WHERE id = ?", (channel_id,)
            )
            return cur.rowcount > 0

    def toggle_channel(self, channel_id: int) -> Optional[bool]:
        ch = self.get_channel(channel_id)
        if not ch:
            return None
        new_val = 0 if ch.get("is_active", 1) else 1
        self.update_channel(channel_id, is_active=new_val)
        return bool(new_val)

    def get_channels_config(self) -> dict:
        return {
            "enabled": self.is_channel_required_enabled(),
            "channels": self.list_channels(active_only=True),
        }

    def get_channel_config(self) -> dict:
        cfg = self.get_channels_config()
        if cfg["channels"]:
            ch = cfg["channels"][0]
            return {
                "enabled": cfg["enabled"],
                "username": ch["username"],
                "link": ch["link"],
            }
        enabled = cfg["enabled"]
        username = self._normalize_channel_username(
            self.get_setting("channel_username", "") or CHANNEL_USERNAME
        )
        link = self.get_setting("channel_link", "") or CHANNEL_LINK
        if not link and username:
            link = f"https://t.me/{username}"
        return {"enabled": enabled, "username": username, "link": link}

    # ── Users ──

    def upsert_user(self, user_id: int, username: str = "", full_name: str = ""):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO users (user_id, username, full_name)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   username = excluded.username,
                   full_name = excluded.full_name""",
                (user_id, username, full_name),
            )

    def get_user(self, user_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def add_points(self, user_id: int, points: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET points = points + ? WHERE user_id = ?",
                (points, user_id),
            )

    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT user_id, username, full_name, points, exams_taken
                   FROM users ORDER BY points DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_users(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_user_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ── Exams ──

    def create_exam(
        self,
        exam_id: str,
        title: str,
        questions: list,
        duration_minutes: int,
        created_by: int,
        description: str = "",
        expires_at: str | None = None,
        allow_multiple: bool = False,
        is_published: bool = True,
    ):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO exams
                   (exam_id, title, questions, duration_minutes, created_by,
                    description, expires_at, allow_multiple, is_published)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (exam_id, title, json.dumps(questions, ensure_ascii=False),
                 duration_minutes, created_by, description, expires_at,
                 1 if allow_multiple else 0, 1 if is_published else 0),
            )

    def _parse_exam_row(self, row) -> dict:
        d = dict(row)
        d["questions"] = json.loads(d["questions"])
        d["allow_multiple"] = bool(d.get("allow_multiple", 0))
        d["is_published"] = bool(d.get("is_published", 1))
        return d

    def get_exam(self, exam_id: str) -> Optional[dict]:
        exam_id = (exam_id or "").strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM exams WHERE exam_id = ? AND is_active = 1", (exam_id,)
            ).fetchone()
            if row:
                return self._parse_exam_row(row)
            return None

    def get_all_exams(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM exams ORDER BY created_at DESC"
            ).fetchall()
            return [self._parse_exam_row(r) for r in rows]

    def get_exams_by_creator(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM exams WHERE created_by = ? AND is_active = 1 ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [self._parse_exam_row(r) for r in rows]

    def publish_exam(self, exam_id: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE exams SET is_published = 1 WHERE exam_id = ?", (exam_id,)
            )

    def get_exam_stats(self, exam_id: str) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT score, total, percentage FROM exam_results WHERE exam_id = ?",
                (exam_id,),
            ).fetchall()
            if not rows:
                return {"participants": 0, "avg_score": 0, "max_score": 0, "min_score": 0}
            pcts = [r["percentage"] for r in rows]
            return {
                "participants": len(rows),
                "avg_score": sum(pcts) / len(pcts),
                "max_score": max(pcts),
                "min_score": min(pcts),
            }

    def get_exam_any(self, exam_id: str) -> Optional[dict]:
        exam_id = (exam_id or "").strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM exams WHERE exam_id = ?", (exam_id,)
            ).fetchone()
            return self._parse_exam_row(row) if row else None

    def deactivate_exam(self, exam_id: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE exams SET is_active = 0 WHERE exam_id = ?", (exam_id,)
            )

    # ── Exam Results ──

    def save_exam_result(
        self,
        exam_id: str,
        user_id: int,
        score: float,
        total: int,
        percentage: float,
        answers: list,
    ):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO exam_results
                   (exam_id, user_id, score, total, percentage, answers)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (exam_id, user_id, score, total, percentage,
                 json.dumps(answers, ensure_ascii=False)),
            )
            conn.execute(
                "UPDATE users SET exams_taken = exams_taken + 1 WHERE user_id = ?",
                (user_id,),
            )
            points = int(percentage)
            conn.execute(
                "UPDATE users SET points = points + ? WHERE user_id = ?",
                (points, user_id),
            )

    def get_exam_results(self, exam_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT er.*, u.username, u.full_name
                   FROM exam_results er
                   JOIN users u ON er.user_id = u.user_id
                   WHERE er.exam_id = ?
                   ORDER BY er.percentage DESC""",
                (exam_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_user_results(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT er.*, e.title
                   FROM exam_results er
                   JOIN exams e ON er.exam_id = e.exam_id
                   WHERE er.user_id = ?
                   ORDER BY er.completed_at DESC""",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def has_taken_exam(self, exam_id: str, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM exam_results WHERE exam_id = ? AND user_id = ?",
                (exam_id, user_id),
            ).fetchone()
            return row is not None

    # ── Admin Questions ──

    def add_admin_question(
        self, question: str, options: list, correct_index: int, category: str = "general"
    ):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO admin_questions (question, options, correct_index, category)
                   VALUES (?, ?, ?, ?)""",
                (question, json.dumps(options, ensure_ascii=False), correct_index, category),
            )

    def get_admin_questions(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM admin_questions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["options"] = json.loads(d["options"])
                result.append(d)
            return result

    # ── Activity Log ──

    def log_activity(self, user_id: int, action: str, details: str = ""):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO activity_log (user_id, action, details) VALUES (?, ?, ?)",
                (user_id, action, details),
            )

    def get_recent_activities(self, limit: int = 25) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT a.action, a.details, a.created_at, a.user_id,
                          u.username, u.full_name
                   FROM activity_log a
                   LEFT JOIN users u ON a.user_id = u.user_id
                   ORDER BY a.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_admin_questions_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM admin_questions").fetchone()[0]

    # ── Stats ──

    def get_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            exams = conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0]
            results = conn.execute("SELECT COUNT(*) FROM exam_results").fetchone()[0]
            total_points = conn.execute(
                "SELECT COALESCE(SUM(points), 0) FROM users"
            ).fetchone()[0]
            activities = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
            return {
                "users": users,
                "exams": exams,
                "results": results,
                "total_points": total_points,
                "activities": activities,
            }
