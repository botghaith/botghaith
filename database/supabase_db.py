import json
import logging
from typing import Any, Optional

from config import CHANNEL_LINK, CHANNEL_REQUIRED, CHANNEL_USERNAME
from services.supabase_client import get_supabase

logger = logging.getLogger(__name__)


class SupabaseDatabase:
    """PostgreSQL عبر Supabase — نفس واجهة Database المحلية."""

    def __init__(self):
        self.client = get_supabase()
        self._bootstrap()

    def _bootstrap(self):
        try:
            self._migrate_channel_settings()
            self._migrate_channels_table()
        except Exception as exc:
            logger.error(
                "Supabase bootstrap failed — نفّذ database/supabase_schema.sql في SQL Editor: %s",
                exc,
            )

    def _normalize_channel_username(self, username: str) -> str:
        u = (username or "").strip()
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if u.lower().startswith(prefix):
                u = u[len(prefix):]
        return u.split("/")[0].split("?")[0].lstrip("@").strip()

    def _migrate_channels_table(self):
        rows = self.client.table("required_channels").select("id", count="exact").limit(1).execute()
        if rows.count and rows.count > 0:
            return
        row_u = self.client.table("settings").select("value").eq("key", "channel_username").execute()
        row_l = self.client.table("settings").select("value").eq("key", "channel_link").execute()
        username = self._normalize_channel_username(
            (row_u.data[0]["value"] if row_u.data else "") or CHANNEL_USERNAME
        )
        if not username:
            return
        link = (row_l.data[0]["value"] if row_l.data else "") or CHANNEL_LINK
        if not link:
            link = f"https://t.me/{username}"
        self.client.table("required_channels").upsert(
            {"username": username, "title": f"@{username}", "link": link, "is_active": 1},
            on_conflict="username",
        ).execute()

    def _migrate_channel_settings(self):
        self.set_setting("channel_required", "1" if CHANNEL_REQUIRED else "0")
        row = self.client.table("settings").select("value").eq("key", "channel_username").execute()
        if row.data and row.data[0].get("value"):
            fixed = self._normalize_channel_username(row.data[0]["value"])
            if fixed != row.data[0]["value"]:
                self.set_setting("channel_username", fixed)
        elif CHANNEL_USERNAME:
            self.set_setting("channel_username", CHANNEL_USERNAME)
        row_l = self.client.table("settings").select("value").eq("key", "channel_link").execute()
        if not (row_l.data and row_l.data[0].get("value")) and CHANNEL_LINK:
            self.set_setting("channel_link", CHANNEL_LINK)

    # ── Settings ──

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.client.table("settings").select("value").eq("key", key).execute()
        return row.data[0]["value"] if row.data else default

    def set_setting(self, key: str, value: str):
        self.client.table("settings").upsert({"key": key, "value": value}, on_conflict="key").execute()

    def is_channel_required_enabled(self) -> bool:
        return self.get_setting("channel_required", "1" if CHANNEL_REQUIRED else "0") == "1"

    def set_channel_required(self, enabled: bool):
        self.set_setting("channel_required", "1" if enabled else "0")

    def list_channels(self, active_only: bool = False) -> list[dict]:
        q = self.client.table("required_channels").select("*").order("id")
        if active_only:
            q = q.eq("is_active", 1)
        return q.execute().data or []

    def get_channel(self, channel_id: int) -> Optional[dict]:
        row = self.client.table("required_channels").select("*").eq("id", channel_id).execute()
        return row.data[0] if row.data else None

    def add_channel(self, username: str, link: str, title: str = "") -> int:
        username = self._normalize_channel_username(username)
        if not title:
            title = f"@{username}"
        row = (
            self.client.table("required_channels")
            .insert({"username": username, "title": title, "link": link, "is_active": 1})
            .execute()
        )
        return row.data[0]["id"]

    def update_channel(self, channel_id: int, **fields) -> bool:
        allowed = {"username", "title", "link", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "username" in updates:
            updates["username"] = self._normalize_channel_username(updates["username"])
        if "link" in updates:
            updates["link"] = updates["link"].strip()
        self.client.table("required_channels").update(updates).eq("id", channel_id).execute()
        return True

    def delete_channel(self, channel_id: int) -> bool:
        row = self.client.table("required_channels").delete().eq("id", channel_id).execute()
        return bool(row.data)

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
            return {"enabled": cfg["enabled"], "username": ch["username"], "link": ch["link"]}
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
        self.client.table("users").upsert(
            {"user_id": user_id, "username": username, "full_name": full_name},
            on_conflict="user_id",
        ).execute()

    def get_user(self, user_id: int) -> Optional[dict]:
        row = self.client.table("users").select("*").eq("user_id", user_id).execute()
        return row.data[0] if row.data else None

    def add_points(self, user_id: int, points: int):
        user = self.get_user(user_id)
        if user:
            self.client.table("users").update(
                {"points": user.get("points", 0) + points}
            ).eq("user_id", user_id).execute()

    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        rows = (
            self.client.table("users")
            .select("user_id, username, full_name, points, exams_taken")
            .order("points", desc=True)
            .limit(limit)
            .execute()
        )
        return rows.data or []

    def get_all_users(self) -> list[dict]:
        rows = self.client.table("users").select("*").order("created_at", desc=True).execute()
        return rows.data or []

    def get_user_count(self) -> int:
        row = self.client.table("users").select("*", count="exact").limit(0).execute()
        return row.count or 0

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
        self.client.table("exams").insert(
            {
                "exam_id": exam_id,
                "title": title,
                "questions": questions,
                "duration_minutes": duration_minutes,
                "created_by": created_by,
                "description": description,
                "expires_at": expires_at,
                "allow_multiple": 1 if allow_multiple else 0,
                "is_published": 1 if is_published else 0,
            }
        ).execute()

    def _parse_exam_row(self, row: dict) -> dict:
        d = dict(row)
        q = d.get("questions")
        if isinstance(q, str):
            d["questions"] = json.loads(q)
        d["allow_multiple"] = bool(d.get("allow_multiple", 0))
        d["is_published"] = bool(d.get("is_published", 1))
        return d

    def get_exam(self, exam_id: str) -> Optional[dict]:
        exam_id = (exam_id or "").strip().lower()
        row = (
            self.client.table("exams")
            .select("*")
            .eq("exam_id", exam_id)
            .eq("is_active", 1)
            .execute()
        )
        return self._parse_exam_row(row.data[0]) if row.data else None

    def get_all_exams(self) -> list[dict]:
        rows = self.client.table("exams").select("*").order("created_at", desc=True).execute()
        return [self._parse_exam_row(r) for r in (rows.data or [])]

    def get_exams_by_creator(self, user_id: int) -> list[dict]:
        rows = (
            self.client.table("exams")
            .select("*")
            .eq("created_by", user_id)
            .eq("is_active", 1)
            .order("created_at", desc=True)
            .execute()
        )
        return [self._parse_exam_row(r) for r in (rows.data or [])]

    def publish_exam(self, exam_id: str):
        self.client.table("exams").update({"is_published": 1}).eq("exam_id", exam_id).execute()

    def get_exam_stats(self, exam_id: str) -> dict:
        rows = (
            self.client.table("exam_results")
            .select("score, total, percentage")
            .eq("exam_id", exam_id)
            .execute()
        )
        data = rows.data or []
        if not data:
            return {"participants": 0, "avg_score": 0, "max_score": 0, "min_score": 0}
        pcts = [r["percentage"] for r in data]
        return {
            "participants": len(data),
            "avg_score": sum(pcts) / len(pcts),
            "max_score": max(pcts),
            "min_score": min(pcts),
        }

    def get_exam_any(self, exam_id: str) -> Optional[dict]:
        exam_id = (exam_id or "").strip().lower()
        row = self.client.table("exams").select("*").eq("exam_id", exam_id).execute()
        return self._parse_exam_row(row.data[0]) if row.data else None

    def deactivate_exam(self, exam_id: str):
        self.client.table("exams").update({"is_active": 0}).eq("exam_id", exam_id).execute()

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
        self.client.table("exam_results").insert(
            {
                "exam_id": exam_id,
                "user_id": user_id,
                "score": score,
                "total": total,
                "percentage": percentage,
                "answers": answers,
            }
        ).execute()
        user = self.get_user(user_id)
        if user:
            self.client.table("users").update(
                {
                    "exams_taken": user.get("exams_taken", 0) + 1,
                    "points": user.get("points", 0) + int(percentage),
                }
            ).eq("user_id", user_id).execute()

    def get_exam_results(self, exam_id: str) -> list[dict]:
        rows = (
            self.client.table("exam_results")
            .select("*, users(username, full_name)")
            .eq("exam_id", exam_id)
            .order("percentage", desc=True)
            .execute()
        )
        result = []
        for r in rows.data or []:
            d = dict(r)
            u = d.pop("users", None) or {}
            d["username"] = u.get("username", "")
            d["full_name"] = u.get("full_name", "")
            result.append(d)
        return result

    def get_user_results(self, user_id: int) -> list[dict]:
        rows = (
            self.client.table("exam_results")
            .select("*, exams(title)")
            .eq("user_id", user_id)
            .order("completed_at", desc=True)
            .execute()
        )
        result = []
        for r in rows.data or []:
            d = dict(r)
            e = d.pop("exams", None) or {}
            d["title"] = e.get("title", "")
            result.append(d)
        return result

    def has_taken_exam(self, exam_id: str, user_id: int) -> bool:
        row = (
            self.client.table("exam_results")
            .select("id")
            .eq("exam_id", exam_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return bool(row.data)

    # ── Admin Questions ──

    def add_admin_question(
        self, question: str, options: list, correct_index: int, category: str = "general"
    ):
        self.client.table("admin_questions").insert(
            {
                "question": question,
                "options": options,
                "correct_index": correct_index,
                "category": category,
            }
        ).execute()

    def get_admin_questions(self, limit: int = 50) -> list[dict]:
        rows = (
            self.client.table("admin_questions")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        result = []
        for r in rows.data or []:
            d = dict(r)
            opts = d.get("options")
            if isinstance(opts, str):
                d["options"] = json.loads(opts)
            result.append(d)
        return result

    def get_admin_questions_count(self) -> int:
        row = self.client.table("admin_questions").select("*", count="exact").limit(0).execute()
        return row.count or 0

    # ── Activity Log ──

    def log_activity(self, user_id: int, action: str, details: str = ""):
        self.client.table("activity_log").insert(
            {"user_id": user_id, "action": action, "details": details}
        ).execute()

    def get_recent_activities(self, limit: int = 25) -> list[dict]:
        rows = (
            self.client.table("activity_log")
            .select("action, details, created_at, user_id, users(username, full_name)")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        result = []
        for r in rows.data or []:
            d = dict(r)
            u = d.pop("users", None) or {}
            d["username"] = u.get("username", "")
            d["full_name"] = u.get("full_name", "")
            result.append(d)
        return result

    # ── Stats ──

    def get_stats(self) -> dict[str, Any]:
        users = self.client.table("users").select("*", count="exact").limit(0).execute().count or 0
        exams = self.client.table("exams").select("*", count="exact").limit(0).execute().count or 0
        results = (
            self.client.table("exam_results").select("*", count="exact").limit(0).execute().count or 0
        )
        activities = (
            self.client.table("activity_log").select("*", count="exact").limit(0).execute().count or 0
        )
        all_users = self.client.table("users").select("points").execute().data or []
        total_points = sum(u.get("points", 0) for u in all_users)
        return {
            "users": users,
            "exams": exams,
            "results": results,
            "total_points": total_points,
            "activities": activities,
        }
