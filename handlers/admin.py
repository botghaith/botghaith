import asyncio
import html
import logging
from pathlib import Path

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import ADMIN_USERNAME
from database.db import Database
from utils.helpers import (
    generate_exam_id, get_user_temp_dir, is_admin, build_exam_link,
    sanitize_text_for_send,
)
from utils.keyboards import (
    ADMIN_MENU,
    MAIN_MENU,
    admin_exam_source_menu,
    admin_exams_list_keyboard,
    admin_exam_actions_keyboard,
    admin_dashboard_inline,
    admin_channels_menu_keyboard,
    admin_channels_list_keyboard,
    admin_channel_actions_keyboard,
    admin_users_menu_keyboard,
    admin_users_page_keyboard,
    admin_user_chat_keyboard,
    admin_user_search_keyboard,
)
from utils import states
from utils.activity_log import ACTIVITY_LABELS, format_activity_line, log_user_activity

logger = logging.getLogger(__name__)

ACTION_LABELS = ACTIVITY_LABELS


def _chunk_lines(lines: list[str], max_len: int = 3900) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > max_len:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _account_username(u: dict) -> str:
    return (u.get("username") or "").strip().lstrip("@")


def _profile_href(u: dict) -> str:
    uname = _account_username(u)
    if uname:
        return f"https://t.me/{uname}"
    uid = u.get("user_id")
    if uid:
        return f"tg://user?id={uid}"
    return ""


def _profile_link(text: str, u: dict) -> str:
    href = _profile_href(u)
    if not href:
        return _esc(text)
    return f'<a href="{_esc(href)}">{_esc(text)}</a>'


def _username_html(u: dict) -> str:
    uname = _account_username(u)
    if not uname:
        return "يوزر الحساب: —"
    return f"يوزر الحساب: {_profile_link('@' + uname, u)}"


def _name_html(u: dict) -> str:
    name = (u.get("full_name") or "").strip() or "—"
    return _profile_link(name, u)


async def _refresh_user_from_telegram(bot, db, u: dict) -> dict:
    uid = u.get("user_id")
    if not uid:
        return u
    try:
        chat = await bot.get_chat(int(uid))
    except Exception:
        return u
    uname = (getattr(chat, "username", None) or "").strip().lstrip("@")
    name = (
        getattr(chat, "full_name", None)
        or getattr(chat, "first_name", None)
        or ""
    ).strip()
    if uname:
        u["username"] = uname
    if name:
        u["full_name"] = name
    if uname or name:
        try:
            db.upsert_user(int(uid), uname, name)
        except Exception:
            logger.warning("Failed to save refreshed username for %s", uid)
    return u


async def _enrich_users_from_telegram(bot, db, users: list[dict]) -> list[dict]:
    if not users:
        return users
    sem = asyncio.Semaphore(8)

    async def one(u: dict) -> dict:
        async with sem:
            return await _refresh_user_from_telegram(bot, db, u)

    return list(await asyncio.gather(*(one(u) for u in users)))


def _format_user_admin_line(seq: int, u: dict) -> str:
    uid = u.get("user_id", "?")
    return (
        f"{seq}. {_name_html(u)}\n"
        f"   {_username_html(u)}\n"
        f"   المعرف: <code>{_esc(uid)}</code>"
    )


def _join_rank_map(users: list[dict]) -> dict[int, int]:
    return {u["user_id"]: i for i, u in enumerate(users, 1)}


def _format_user_search_block(u: dict, rank: int | str) -> str:
    uid = u.get("user_id", "?")
    return "\n".join(
        [
            f"#{rank} — {_name_html(u)}",
            _username_html(u),
            f"المعرف: <code>{_esc(uid)}</code>",
        ]
    )


def _admin_only(update: Update) -> bool:
    user = update.effective_user
    return is_admin(user.username or "", ADMIN_USERNAME)


async def _deny(update: Update):
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.reply_text("❌ هذا القسم للأدمن فقط.")


def setup_admin_handlers(db: Database, back_to_main) -> list:
    async def _dashboard_text() -> str:
        stats = db.get_stats()
        channel = db.get_channel_config()
        channels = db.list_channels()
        q_count = db.get_admin_questions_count()
        active_count = sum(1 for c in channels if c.get("is_active", 1))
        return (
            "╔══════════════════════════╗\n"
            "║   🔧 لوحة تحكم الأدمن   ║\n"
            "╚══════════════════════════╝\n\n"
            f"👤 الأدمن: @{ADMIN_USERNAME}\n"
            f"👥 المستخدمون: {stats['users']}\n"
            f"📝 الامتحانات: {stats['exams']}\n"
            f"📋 النتائج: {stats['results']}\n"
            f"❓ أسئلة جاهزة: {q_count}\n"
            f"📈 النشاطات: {stats['activities']}\n"
            f"⭐ إجمالي النقاط: {stats['total_points']}\n\n"
            f"📺 القنوات: {active_count} مفعّلة / {len(channels)} إجمالي\n"
            f"🔒 الاشتراك الإجباري: {'مفعّل ✅' if channel['enabled'] else 'معطّل ❌'}\n\n"
            "🛠️ جميع الخدمات: مفعّلة ✅"
        )

    async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        await update.message.reply_text(await _dashboard_text(), reply_markup=ADMIN_MENU)
        await update.message.reply_text("⚡ إجراءات سريعة:", reply_markup=admin_dashboard_inline())

    async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        stats = db.get_stats()
        channels = db.list_channels()
        active = [c for c in channels if c.get("is_active", 1)]
        text = (
            "📈 إحصائيات تفصيلية\n\n"
            f"👥 المستخدمون: {stats['users']}\n"
            f"📝 الامتحانات: {stats['exams']}\n"
            f"📋 النتائج: {stats['results']}\n"
            f"⭐ إجمالي النقاط: {stats['total_points']}\n"
            f"📈 النشاطات: {stats['activities']}\n\n"
            f"📺 القنوات المفعّلة: {len(active)} / {len(channels)}\n"
            f"🔒 الاشتراك الإجباري: {'مفعّل ✅' if db.is_channel_required_enabled() else 'معطّل ❌'}"
        )
        await update.message.reply_text(text, reply_markup=ADMIN_MENU)

    _USERS_PAGE = 8
    _CHAT_PAGE = 6

    def _users_page_text(total: int, page: int, pages: int) -> str:
        if total == 0:
            return "👥 لا يوجد مستخدمون بعد."
        return (
            f"👥 المستخدمون ({total})\n"
            f"الصفحة {page + 1} من {pages}\n\n"
            "اضغط على الشخص لعرض نشاطه مع البوت.\n"
            "◀️ السابق / التالي ▶️ للتنقل بين الصفحات."
        )

    async def _edit_or_send(query, text: str, **kwargs):
        try:
            await query.edit_message_text(text, **kwargs)
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            await query.message.reply_text(text, **kwargs)

    async def _show_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
        users = db.get_all_users()
        total = len(users)
        pages = max(1, (total + _USERS_PAGE - 1) // _USERS_PAGE) if total else 1
        page = max(0, min(page, pages - 1))
        text = _users_page_text(total, page, pages)
        kb = admin_users_page_keyboard(users, page, _USERS_PAGE)
        query = update.callback_query
        if query and query.message:
            await _edit_or_send(query, text, reply_markup=kb)
            return
        await update.message.reply_text(text, reply_markup=kb)

    async def _show_user_chat(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        msg_page: int | None,
        list_page: int,
    ):
        users = db.get_all_users()
        ids = [u["user_id"] for u in users]
        try:
            index = ids.index(user_id)
        except ValueError:
            index = -1
        stored = next((u for u in users if u["user_id"] == user_id), None)
        if stored is None:
            stored = db.get_user(user_id) or {
                "user_id": user_id, "username": "", "full_name": "",
            }
        stored = await _refresh_user_from_telegram(context.bot, db, dict(stored))

        total = db.count_user_activities(user_id)
        pages = max(1, (total + _CHAT_PAGE - 1) // _CHAT_PAGE) if total else 1
        if msg_page is None:
            msg_page = 0
        msg_page = max(0, min(msg_page, pages - 1))
        rows = db.get_user_activities(user_id, _CHAT_PAGE, msg_page * _CHAT_PAGE) if total else []

        prev_user = None
        next_user = None
        if index > 0:
            prev_id = ids[index - 1]
            prev_user = (prev_id, (index - 1) // _USERS_PAGE)
        if 0 <= index < len(ids) - 1:
            next_id = ids[index + 1]
            next_user = (next_id, (index + 1) // _USERS_PAGE)

        header = [
            f"👤 {_name_html(stored)}",
            _username_html(stored),
            f"🆔 <code>{_esc(user_id)}</code>",
            "",
        ]
        if total:
            start = msg_page * _CHAT_PAGE + 1
            end = start + len(rows) - 1
            header.append(f"📜 نشاطه مع البوت: {start}–{end} من {total}")
            header.append("")
            for act in rows:
                action = ACTIVITY_LABELS.get(act.get("action", ""), act.get("action", ""))
                detail = f" — {_esc(act['details'])}" if act.get("details") else ""
                ts = str(act.get("created_at") or "")[:16].replace("T", " ")
                when = f"[{_esc(ts)}] " if ts else ""
                header.append(f"• {when}{_esc(action)}{detail}")
        else:
            header.append("ما مسجّل نشاط لهذا الشخص بعد.")

        text = "\n".join(header)
        if len(text) > 3900:
            text = text[:3900] + "…"
        kb = admin_user_chat_keyboard(
            user_id, msg_page, pages, max(0, list_page), prev_user, next_user,
        )
        query = update.callback_query
        kwargs = {
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": kb,
        }
        if query and query.message:
            await _edit_or_send(query, text, **kwargs)
            return
        await update.message.reply_text(text, **kwargs)

    async def _browse_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        data = update.callback_query.data or ""
        try:
            if data.startswith("adm_up_"):
                await _show_users_page(update, context, int(data.rsplit("_", 1)[-1]))
                return True
            if data.startswith("adm_um_"):
                uid_s, list_page_s = data[len("adm_um_"):].rsplit("_", 1)
                await _show_user_chat(
                    update, context, int(uid_s), None, int(list_page_s),
                )
                return True
            if data.startswith("adm_uc_"):
                uid_s, msg_page_s, list_page_s = data[len("adm_uc_"):].rsplit("_", 2)
                await _show_user_chat(
                    update, context, int(uid_s), int(msg_page_s), int(list_page_s),
                )
                return True
        except (ValueError, BadRequest):
            logger.exception("Failed to open admin user chat view")
            await update.callback_query.message.reply_text("❌ تعذر فتح هذه المحادثة.")
            return True
        return False

    async def show_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        await _show_users_page(update, context, 0)
        return ConversationHandler.END

    async def users_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not _admin_only(update):
            return await _deny(update)
        await query.edit_message_text(
            "🔍 بحث عن مستخدم\n\n"
            "أرسل:\n"
            "• @يوزر أو يوزر\n"
            "• جزء من الاسم\n"
            "• رقم Telegram ID\n\n"
            "سيظهر هل الشخص مسجّل في البوت أم لا.",
        )
        return states.ADMIN_USER_SEARCH

    async def users_browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not _admin_only(update):
            return await _deny(update)
        await _browse_users(update, context)
        return ConversationHandler.END

    async def user_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        if update.message.text in ("🏠 القائمة الرئيسية", "👥 المستخدمون"):
            if update.message.text == "👥 المستخدمون":
                await show_users_menu(update, context)
            else:
                await back_main_admin(update, context)
            return ConversationHandler.END

        query_text = (update.message.text or "").strip()
        if not query_text:
            await update.message.reply_text("❌ أرسل نصاً للبحث.")
            return states.ADMIN_USER_SEARCH

        matches = db.search_users(query_text)
        ranks = _join_rank_map(db.get_all_users())

        if matches:
            matches = await _enrich_users_from_telegram(context.bot, db, matches)
            blocks = [f"🔍 نتائج البحث: {_esc(query_text)}\n"]
            for u in matches:
                rank = ranks.get(u["user_id"], "?")
                blocks.append(_format_user_search_block(u, rank))
                blocks.append("")
            await update.message.reply_text(
                "\n".join(blocks).strip(),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=admin_user_search_keyboard(matches),
            )
            return ConversationHandler.END

        q = query_text.lstrip("@")
        tg_user = None
        try:
            tg_user = await context.bot.get_chat(int(q) if q.isdigit() else f"@{q}")
        except BadRequest:
            pass
        except Exception as e:
            logger.warning("Telegram user lookup failed: %s", e)

        if tg_user:
            fake = {
                "user_id": tg_user.id,
                "username": getattr(tg_user, "username", None) or "",
                "full_name": getattr(tg_user, "full_name", None)
                or getattr(tg_user, "first_name", "")
                or "—",
            }
            text = (
                f"🔍 نتائج البحث: {_esc(query_text)}\n\n"
                "❌ ليس مستخدماً للبوت\n\n"
                "📱 الحساب موجود على تيليجرام:\n"
                f"• {_name_html(fake)}\n"
                f"• {_username_html(fake)}\n"
                f"• 🆔 <code>{tg_user.id}</code>"
            )
        else:
            text = (
                f"🔍 نتائج البحث: {_esc(query_text)}\n\n"
                "❌ غير مسجّل في البوت\n"
                "❌ لم يُعثر على الحساب في تيليجرام"
            )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=admin_users_menu_keyboard(),
        )
        return ConversationHandler.END

    async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        board = await _enrich_users_from_telegram(context.bot, db, db.get_leaderboard(15))
        if not board:
            await update.message.reply_text("لا يوجد ترتيب بعد.", reply_markup=ADMIN_MENU)
            return
        lines = ["🏆 ترتيب الطلاب\n"]
        for i, u in enumerate(board, 1):
            lines.append(
                f"{i}. {_name_html(u)} — {_username_html(u)}\n"
                f"   {u['points']} نقطة ({u.get('exams_taken', 0)} امتحان)"
            )
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=ADMIN_MENU,
        )

    async def show_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        rows = db.get_recent_activities(50)
        if not rows:
            await update.message.reply_text("لا يوجد نشاط مسجّل.", reply_markup=ADMIN_MENU)
            return
        lines = [f"📜 سجل النشاط ({len(rows)})\n"]
        for r in rows:
            lines.append(format_activity_line(r))
        chunks = _chunk_lines(lines)
        for i, chunk in enumerate(chunks):
            await update.message.reply_text(
                chunk,
                reply_markup=ADMIN_MENU if i == len(chunks) - 1 else None,
            )

    async def show_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if not msg:
            return
        text = (
            "🛠️ حالة الخدمات — الكل مفعّل ✅\n\n"
            "📚 الترجمة — نص + ملف (4 أنماط)\n"
            "📄 PDF — تحويل، دمج، تقسيم، ضغط، استخراج OCR، دمج سلايدات\n"
            "📝 امتحانات — إنشاء، حل، نتائج، تصدير\n"
            "🧑‍🎓 حساب طلابي — نقاط وترتيب\n\n"
            "اضغط 🏠 القائمة الرئيسية لاستخدام أي خدمة."
        )
        await msg.reply_text(text, reply_markup=ADMIN_MENU)

    async def list_exams(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        exams = db.get_all_exams()
        text = f"📋 الامتحانات ({len(exams)})\nاختر امتحاناً لإدارته:"
        await update.message.reply_text(text, reply_markup=admin_exams_list_keyboard(exams))

    async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not _admin_only(update):
            return await _deny(update)

        data = query.data
        if data == "adm_noop":
            return
        if data.startswith(("adm_up_", "adm_um_", "adm_uc_")):
            await _browse_users(update, context)
            return
        if data == "adm_stats":
            stats = db.get_stats()
            await query.message.reply_text(
                f"📈 مستخدمون: {stats['users']} | امتحانات: {stats['exams']} | نتائج: {stats['results']}",
                reply_markup=ADMIN_MENU,
            )
            return
        if data == "adm_activity":
            rows = db.get_recent_activities(15)
            if not rows:
                await query.message.reply_text("لا نشاط.", reply_markup=ADMIN_MENU)
                return
            lines = ["📜 آخر النشاطات:\n"]
            for r in rows:
                lines.append(format_activity_line(r))
            await query.message.reply_text("\n".join(lines)[:3500], reply_markup=ADMIN_MENU)
            return
        if data == "adm_services":
            await show_services(update, context)
            return
        if data == "adm_exams_list":
            exams = db.get_all_exams()
            await query.edit_message_text(
                f"📋 الامتحانات ({len(exams)}):",
                reply_markup=admin_exams_list_keyboard(exams),
            )
            return
        if data.startswith("adm_exam_off_"):
            exam_id = data.replace("adm_exam_off_", "")
            db.deactivate_exam(exam_id)
            await query.edit_message_text(f"⛔ تم إيقاف الامتحان: {exam_id}")
            return
        if data.startswith("adm_exam_res_"):
            exam_id = data.replace("adm_exam_res_", "")
            stats = db.get_exam_stats(exam_id)
            results = await _enrich_users_from_telegram(
                context.bot, db, db.get_exam_results(exam_id)[:10]
            )
            lines = [
                f"📊 نتائج {exam_id}",
                f"المشاركون: {stats['participants']}",
                f"المتوسط: {stats['avg_score']:.1f}%",
            ]
            for i, r in enumerate(results, 1):
                lines.append(
                    f"{i}. {_name_html(r)} — {_username_html(r)} — {r['percentage']:.0f}%"
                )
            await query.message.reply_text(
                "\n".join(lines),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=ADMIN_MENU,
            )
            return
        if data.startswith("adm_exam_"):
            exam_id = data.replace("adm_exam_", "")
            exam = db.get_exam_any(exam_id)
            if not exam:
                await query.message.reply_text("❌ الامتحان غير موجود.")
                return
            bot_username = (await context.bot.get_me()).username
            link = build_exam_link(bot_username, exam_id)
            active = bool(exam.get("is_active", 1))
            text = (
                f"📝 {exam['title']}\n"
                f"🔑 {exam_id}\n"
                f"⏱ {exam['duration_minutes']} دقيقة\n"
                f"❓ {len(exam['questions'])} سؤال\n"
                f"الحالة: {'نشط 🟢' if active else 'موقوف 🔴'}\n"
                f"🔗 {link}"
            )
            await query.message.reply_text(
                text,
                reply_markup=admin_exam_actions_keyboard(exam_id, active),
            )
            return

        if data == "adm_ch_list":
            channels = db.list_channels()
            enabled = db.is_channel_required_enabled()
            text = (
                "📺 إدارة القنوات\n\n"
                f"🔒 الاشتراك الإجباري: {'مفعّل ✅' if enabled else 'معطّل ❌'}\n"
                f"📋 عدد القنوات: {len(channels)}\n\n"
                "اختر قناة للتعديل أو الإضافة:"
            )
            await query.edit_message_text(
                text,
                reply_markup=admin_channels_list_keyboard(channels),
            )
            return
        if data == "adm_ch_req_on":
            db.set_channel_required(True)
            await query.answer("✅ تم تفعيل الاشتراك الإجباري")
            channels = db.list_channels()
            await query.edit_message_text(
                "📺 إدارة القنوات\n\n🔒 الاشتراك الإجباري: مفعّل ✅",
                reply_markup=admin_channels_menu_keyboard(True),
            )
            await query.message.reply_text(
                "📋 القنوات:",
                reply_markup=admin_channels_list_keyboard(channels),
            )
            return
        if data == "adm_ch_req_off":
            db.set_channel_required(False)
            await query.answer("⛔ تم تعطيل الاشتراك الإجباري")
            channels = db.list_channels()
            await query.edit_message_text(
                "📺 إدارة القنوات\n\n🔒 الاشتراك الإجباري: معطّل ❌",
                reply_markup=admin_channels_menu_keyboard(False),
            )
            await query.message.reply_text(
                "📋 القنوات:",
                reply_markup=admin_channels_list_keyboard(channels),
            )
            return
        if data.startswith("adm_ch_view_"):
            channel_id = int(data.replace("adm_ch_view_", ""))
            ch = db.get_channel(channel_id)
            if not ch:
                await query.answer("❌ القناة غير موجودة", show_alert=True)
                return
            active = bool(ch.get("is_active", 1))
            text = (
                f"📺 {ch.get('title') or '@' + ch['username']}\n\n"
                f"👤 اليوزر: @{ch['username']}\n"
                f"🔗 الرابط: {ch['link']}\n"
                f"الحالة: {'مفعّلة 🟢' if active else 'معطّلة 🔴'}"
            )
            await query.edit_message_text(
                text,
                reply_markup=admin_channel_actions_keyboard(channel_id, active),
            )
            return
        if data.startswith("adm_ch_toggle_"):
            channel_id = int(data.replace("adm_ch_toggle_", ""))
            new_state = db.toggle_channel(channel_id)
            if new_state is None:
                await query.answer("❌ القناة غير موجودة", show_alert=True)
                return
            ch = db.get_channel(channel_id)
            status = "مفعّلة 🟢" if new_state else "معطّلة 🔴"
            await query.answer(f"تم التحديث: {status}")
            text = (
                f"📺 {ch.get('title') or '@' + ch['username']}\n\n"
                f"👤 اليوزر: @{ch['username']}\n"
                f"🔗 الرابط: {ch['link']}\n"
                f"الحالة: {status}"
            )
            await query.edit_message_text(
                text,
                reply_markup=admin_channel_actions_keyboard(channel_id, new_state),
            )
            return
        if data.startswith("adm_ch_del_"):
            channel_id = int(data.replace("adm_ch_del_", ""))
            ch = db.get_channel(channel_id)
            if not ch:
                await query.answer("❌ القناة غير موجودة", show_alert=True)
                return
            db.delete_channel(channel_id)
            await query.answer("🗑️ تم حذف القناة")
            channels = db.list_channels()
            await query.edit_message_text(
                f"✅ تم حذف @{ch['username']}\n\n📋 القنوات المتبقية:",
                reply_markup=admin_channels_list_keyboard(channels),
            )
            return

    async def start_create_exam(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        context.user_data["admin_exam"] = {}
        await update.message.reply_text("📝 أرسل عنوان الامتحان:")
        return states.ADMIN_EXAM_TITLE

    async def set_exam_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        context.user_data["admin_exam"]["title"] = update.message.text
        await update.message.reply_text("⏱ أرسل مدة الامتحان بالدقائق (مثال: 30):")
        return states.ADMIN_EXAM_DURATION

    async def set_exam_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        try:
            duration = int(update.message.text.strip())
            context.user_data["admin_exam"]["duration"] = duration
        except ValueError:
            await update.message.reply_text("❌ أرسل رقماً صحيحاً.")
            return states.ADMIN_EXAM_DURATION
        await update.message.reply_text("اختر مصدر الأسئلة:", reply_markup=admin_exam_source_menu())
        return states.ADMIN_EXAM_SOURCE

    async def exam_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        query = update.callback_query
        await query.answer()
        if query.data != "admin_exam_ready":
            return ConversationHandler.END
        questions = db.get_admin_questions(20)
        if not questions:
            await query.edit_message_text("❌ لا توجد أسئلة جاهزة. أضف أسئلة أولاً من «➕ سؤال جديد».")
            return ConversationHandler.END
        q_list = [
            {"question": q["question"], "options": q["options"], "correct_index": q["correct_index"]}
            for q in questions
        ]
        await _finalize_exam(update, context, q_list, query.message)
        return ConversationHandler.END

    async def _finalize_exam(update, context, questions, msg=None):
        exam_data = context.user_data.get("admin_exam", {})
        exam_id = generate_exam_id()
        title = exam_data.get("title", "امتحان")
        duration = exam_data.get("duration", 30)
        db.create_exam(exam_id, title, questions, duration, update.effective_user.id)
        log_user_activity(
            db, update.effective_user.id, "exam_create",
            f"{exam_id} — {title}",
        )
        bot_username = (await context.bot.get_me()).username
        link = build_exam_link(bot_username, exam_id)
        text = (
            f"✅ تم إنشاء الامتحان!\n\n"
            f"📝 العنوان: {title}\n"
            f"🔑 الرمز: {exam_id}\n"
            f"⏱ المدة: {duration} دقيقة\n"
            f"❓ الأسئلة: {len(questions)}\n\n"
            f"🔗 رابط الامتحان:\n{link}\n\n"
            f"أرسل للطلاب: /exam {exam_id}"
        )
        target = msg or update.message
        await target.reply_text(text, reply_markup=ADMIN_MENU)
        context.user_data.pop("admin_exam", None)

    async def start_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        await update.message.reply_text("📢 أرسل نص الإشعار لإرساله لجميع المستخدمين:")
        return states.ADMIN_NOTIFY

    async def send_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        text = sanitize_text_for_send(update.message.text)
        users = db.get_all_users()
        sent = failed = 0
        await update.message.reply_text(f"⏳ جاري الإرسال لـ {len(users)} مستخدم...")
        for u in users:
            try:
                await context.bot.send_message(
                    u["user_id"],
                    f"📢 إشعار من الإدارة\n\n{text}",
                )
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(
            f"✅ تم الإرسال: {sent}\n❌ فشل: {failed}",
            reply_markup=ADMIN_MENU,
        )
        return ConversationHandler.END

    def _normalize_username(text: str) -> str:
        username = text.lstrip("@").strip()
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if username.lower().startswith(prefix):
                username = username[len(prefix):]
        return username.split("/")[0].split("?")[0].lstrip("@").strip()

    async def show_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        enabled = db.is_channel_required_enabled()
        channels = db.list_channels()
        text = (
            "📺 إدارة القنوات\n\n"
            f"🔒 الاشتراك الإجباري: {'مفعّل ✅' if enabled else 'معطّل ❌'}\n"
            f"📋 عدد القنوات: {len(channels)}\n\n"
            "استخدم الأزرار لإضافة أو تعديل أو حذف القنوات:"
        )
        await update.message.reply_text(
            text,
            reply_markup=admin_channels_menu_keyboard(enabled),
        )
        await update.message.reply_text(
            "📋 قائمة القنوات:",
            reply_markup=admin_channels_list_keyboard(channels),
        )
        return ConversationHandler.END

    async def start_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not _admin_only(update):
            return ConversationHandler.END
        context.user_data["channel_add"] = {"step": "username"}
        await query.message.reply_text(
            "➕ إضافة قناة جديدة\n\n"
            "أرسل يوزر القناة (مثال: @mychannel أو رابط t.me/mychannel)\n"
            "أو أرسل ❌ إلغاء للعودة.",
        )
        return states.ADMIN_CHANNEL_ADD

    async def channel_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        text = update.message.text.strip()
        if text in ("❌ إلغاء", "إلغاء"):
            context.user_data.pop("channel_add", None)
            await update.message.reply_text("تم الإلغاء.", reply_markup=ADMIN_MENU)
            return ConversationHandler.END

        flow = context.user_data.setdefault("channel_add", {"step": "username"})
        step = flow.get("step", "username")

        if step == "username":
            username = _normalize_username(text)
            if not username:
                await update.message.reply_text("❌ يوزر غير صالح. أرسل مثال: @mychannel")
                return states.ADMIN_CHANNEL_ADD
            flow["username"] = username
            flow["step"] = "link"
            await update.message.reply_text(
                f"✅ اليوزر: @{username}\n\nأرسل رابط القناة (مثال: https://t.me/{username})"
            )
            return states.ADMIN_CHANNEL_ADD

        if step == "link":
            link = text.strip()
            if not link.startswith("http"):
                link = f"https://t.me/{flow['username']}"
            flow["link"] = link
            flow["step"] = "title"
            await update.message.reply_text(
                f"✅ الرابط: {link}\n\n"
                "أرسل اسم عرض للقناة (يظهر للمستخدمين)\n"
                "أو أرسل - لاستخدام اليوزر كاسم."
            )
            return states.ADMIN_CHANNEL_ADD

        if step == "title":
            title = "" if text == "-" else text
            try:
                channel_id = db.add_channel(flow["username"], flow["link"], title)
                db.set_channel_required(True)
            except Exception as e:
                logger.warning(f"add_channel failed: {e}")
                await update.message.reply_text(
                    "❌ فشل الإضافة. قد يكون اليوزر مكرراً.",
                    reply_markup=ADMIN_MENU,
                )
                context.user_data.pop("channel_add", None)
                return ConversationHandler.END
            ch = db.get_channel(channel_id)
            context.user_data.pop("channel_add", None)
            await update.message.reply_text(
                f"✅ تمت إضافة القناة!\n\n"
                f"📺 {ch.get('title') or '@' + ch['username']}\n"
                f"👤 @{ch['username']}\n"
                f"🔗 {ch['link']}",
                reply_markup=ADMIN_MENU,
            )
            channels = db.list_channels()
            await update.message.reply_text(
                "📋 القنوات:",
                reply_markup=admin_channels_list_keyboard(channels),
            )
            return ConversationHandler.END

        return states.ADMIN_CHANNEL_ADD

    async def start_edit_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not _admin_only(update):
            return ConversationHandler.END
        data = query.data
        parts = data.replace("adm_ch_edit_", "").split("_", 1)
        if len(parts) != 2:
            return ConversationHandler.END
        field_key, channel_id_str = parts
        field_map = {
            "user": ("username", "يوزر القناة"),
            "link": ("link", "رابط القناة"),
            "title": ("title", "اسم العرض"),
        }
        if field_key not in field_map:
            return ConversationHandler.END
        field, label = field_map[field_key]
        channel_id = int(channel_id_str)
        ch = db.get_channel(channel_id)
        if not ch:
            await query.message.reply_text("❌ القناة غير موجودة.")
            return ConversationHandler.END
        context.user_data["channel_edit"] = {"id": channel_id, "field": field}
        current = ch.get(field, "")
        if field == "username":
            current = f"@{current}"
        await query.message.reply_text(
            f"✏️ تعديل {label}\n\n"
            f"القيمة الحالية: {current or '—'}\n\n"
            f"أرسل القيمة الجديدة أو ❌ إلغاء."
        )
        return states.ADMIN_CHANNEL_EDIT

    async def channel_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        text = update.message.text.strip()
        edit = context.user_data.get("channel_edit")
        if not edit:
            await update.message.reply_text("انتهت الجلسة.", reply_markup=ADMIN_MENU)
            return ConversationHandler.END
        if text in ("❌ إلغاء", "إلغاء"):
            context.user_data.pop("channel_edit", None)
            await update.message.reply_text("تم الإلغاء.", reply_markup=ADMIN_MENU)
            return ConversationHandler.END

        channel_id = edit["id"]
        field = edit["field"]
        value = text
        if field == "username":
            value = _normalize_username(text)
            if not value:
                await update.message.reply_text("❌ يوزر غير صالح.")
                return states.ADMIN_CHANNEL_EDIT
        elif field == "link" and not value.startswith("http"):
            ch = db.get_channel(channel_id)
            uname = ch["username"] if ch else ""
            value = f"https://t.me/{uname}"

        try:
            db.update_channel(channel_id, **{field: value})
        except Exception as e:
            logger.warning(f"update_channel failed: {e}")
            await update.message.reply_text("❌ فشل التحديث. قد يكون اليوزر مكرراً.")
            return states.ADMIN_CHANNEL_EDIT

        ch = db.get_channel(channel_id)
        context.user_data.pop("channel_edit", None)
        active = bool(ch.get("is_active", 1))
        await update.message.reply_text(
            f"✅ تم التحديث!\n\n"
            f"📺 {ch.get('title') or '@' + ch['username']}\n"
            f"👤 @{ch['username']}\n"
            f"🔗 {ch['link']}",
            reply_markup=ADMIN_MENU,
        )
        await update.message.reply_text(
            "⚙️ إجراءات القناة:",
            reply_markup=admin_channel_actions_keyboard(channel_id, active),
        )
        return ConversationHandler.END

    async def start_add_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        await update.message.reply_text("❓ أرسل نص السؤال:")
        return states.ADMIN_ADD_QUESTION

    async def list_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return
        questions = db.get_admin_questions(20)
        if not questions:
            await update.message.reply_text(
                "لا توجد أسئلة جاهزة.\nاضغط ➕ سؤال جديد لإضافة سؤال.",
                reply_markup=ADMIN_MENU,
            )
            return
        lines = [f"❓ الأسئلة الجاهزة ({len(questions)})\n"]
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. {q['question'][:55]}")
        await update.message.reply_text("\n".join(lines), reply_markup=ADMIN_MENU)

    async def add_question_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        context.user_data["new_question"] = {"question": update.message.text}
        await update.message.reply_text(
            "أرسل الخيارات (كل خيار في سطر):\nمثال:\nخيار 1\nخيار 2\nخيار 3\nخيار 4"
        )
        return states.ADMIN_QUESTION_OPTIONS

    async def add_question_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        options = [o.strip() for o in update.message.text.strip().split("\n") if o.strip()]
        if len(options) < 2:
            await update.message.reply_text("❌ أرسل خيارين على الأقل.")
            return states.ADMIN_QUESTION_OPTIONS
        context.user_data["new_question"]["options"] = options
        labels = ["أ", "ب", "ج", "د"]
        opts_text = "\n".join(f"{labels[i] if i < 4 else i+1}) {o}" for i, o in enumerate(options))
        await update.message.reply_text(f"الخيارات:\n{opts_text}\n\nأرسل حرف الإجابة الصحيحة (أ/ب/ج/د):")
        return states.ADMIN_QUESTION_ANSWER

    async def add_question_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return ConversationHandler.END
        answer_map = {"أ": 0, "ب": 1, "ج": 2, "د": 3, "a": 0, "b": 1, "c": 2, "d": 3}
        ans = update.message.text.strip().lower()
        correct_idx = answer_map.get(ans)
        if correct_idx is None:
            try:
                correct_idx = int(ans) - 1
            except ValueError:
                await update.message.reply_text("❌ أرسل أ/ب/ج/د")
                return states.ADMIN_QUESTION_ANSWER
        q = context.user_data["new_question"]
        if correct_idx < 0 or correct_idx >= len(q["options"]):
            await update.message.reply_text("❌ رقم غير صالح.")
            return states.ADMIN_QUESTION_ANSWER
        db.add_admin_question(q["question"], q["options"], correct_idx)
        await update.message.reply_text("✅ تم إضافة السؤال!", reply_markup=ADMIN_MENU)
        context.user_data.pop("new_question", None)
        return ConversationHandler.END

    async def back_main_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await back_to_main(update, context)

    exam_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 إنشاء امتحان$"), start_create_exam)],
        states={
            states.ADMIN_EXAM_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_exam_title)],
            states.ADMIN_EXAM_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_exam_duration)],
            states.ADMIN_EXAM_SOURCE: [CallbackQueryHandler(exam_source_callback, pattern="^admin_exam_")],
        },
        fallbacks=[MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), back_main_admin)],
    )

    notify_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📢 إشعار جماعي$"), start_notify)],
        states={states.ADMIN_NOTIFY: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_notify)]},
        fallbacks=[MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), back_main_admin)],
    )

    channel_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📺 إدارة القنوات$"), show_channels_menu),
            CallbackQueryHandler(start_add_channel, pattern="^adm_ch_add$"),
            CallbackQueryHandler(start_edit_channel, pattern="^adm_ch_edit_(user|link|title)_"),
        ],
        states={
            states.ADMIN_CHANNEL_ADD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channel_add_input),
            ],
            states.ADMIN_CHANNEL_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channel_edit_input),
            ],
        },
        fallbacks=[MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), back_main_admin)],
    )

    question_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ سؤال جديد$"), start_add_question)],
        states={
            states.ADMIN_ADD_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_question_text)],
            states.ADMIN_QUESTION_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_question_options)],
            states.ADMIN_QUESTION_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_question_answer)],
        },
        fallbacks=[MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), back_main_admin)],
    )

    users_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(users_menu_callback, pattern="^adm_users_search$"),
        ],
        states={
            states.ADMIN_USER_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, user_search_input),
                CallbackQueryHandler(users_menu_callback, pattern="^adm_users_search$"),
                CallbackQueryHandler(users_browse_callback, pattern=r"^adm_u[pmc]_"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), back_main_admin),
            MessageHandler(filters.Regex("^👥 المستخدمون$"), show_users_menu),
            CallbackQueryHandler(users_browse_callback, pattern=r"^adm_u[pmc]_"),
        ],
    )

    return [
        exam_conv,
        notify_conv,
        channel_conv,
        question_conv,
        users_conv,
        CallbackQueryHandler(admin_callback, pattern="^adm_"),
        MessageHandler(filters.Regex("^📊 لوحة التحكم$"), show_dashboard),
        MessageHandler(filters.Regex("^📈 الإحصائيات$"), show_stats),
        MessageHandler(filters.Regex("^👥 المستخدمون$"), show_users_menu),
        MessageHandler(filters.Regex("^🏆 ترتيب الطلاب$"), show_leaderboard),
        MessageHandler(filters.Regex("^📋 إدارة الامتحانات$"), list_exams),
        MessageHandler(filters.Regex("^❓ الأسئلة الجاهزة$"), list_questions),
        MessageHandler(filters.Regex("^📜 سجل النشاط$"), show_activity),
        MessageHandler(filters.Regex("^🛠️ حالة الخدمات$"), show_services),
        MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), back_main_admin),
    ]
