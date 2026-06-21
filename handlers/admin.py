import logging
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import ADMIN_USERNAME
from database.db import Database
from utils.helpers import generate_exam_id, get_user_temp_dir, is_admin, build_exam_link, sanitize_text_for_send
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
)
from utils import states

logger = logging.getLogger(__name__)

ACTION_LABELS = {
    "start": "بدء البوت",
    "translate_text": "ترجمة نص",
    "translate_file": "ترجمة ملف",
    "pdf_extract": "استخراج نص",
    "exam": "امتحان",
}


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

    async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        users = db.get_all_users()
        if not users:
            await update.message.reply_text("لا يوجد مستخدمون.", reply_markup=ADMIN_MENU)
            return
        lines = ["👥 المستخدمون\n"]
        for u in users[:40]:
            name = u.get("full_name") or u.get("username") or f"ID:{u['user_id']}"
            lines.append(f"• {name} — {u['points']} نقطة")
        if len(users) > 40:
            lines.append(f"\n... و {len(users) - 40} آخرين")
        await update.message.reply_text("\n".join(lines), reply_markup=ADMIN_MENU)

    async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        board = db.get_leaderboard(15)
        if not board:
            await update.message.reply_text("لا يوجد ترتيب بعد.", reply_markup=ADMIN_MENU)
            return
        lines = ["🏆 ترتيب الطلاب\n"]
        for i, u in enumerate(board, 1):
            name = u.get("full_name") or u.get("username") or f"ID:{u['user_id']}"
            lines.append(f"{i}. {name} — {u['points']} نقطة ({u['exams_taken']} امتحان)")
        await update.message.reply_text("\n".join(lines), reply_markup=ADMIN_MENU)

    async def show_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        rows = db.get_recent_activities(20)
        if not rows:
            await update.message.reply_text("لا يوجد نشاط مسجّل.", reply_markup=ADMIN_MENU)
            return
        lines = ["📜 سجل النشاط الأخير\n"]
        for r in rows:
            name = r.get("full_name") or r.get("username") or str(r.get("user_id", ""))
            action = ACTION_LABELS.get(r["action"], r["action"])
            detail = f" — {r['details']}" if r.get("details") else ""
            lines.append(f"• {name}: {action}{detail}")
        await update.message.reply_text("\n".join(lines)[:4000], reply_markup=ADMIN_MENU)

    async def show_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _admin_only(update):
            return await _deny(update)
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if not msg:
            return
        text = (
            "🛠️ حالة الخدمات — الكل مفعّل ✅\n\n"
            "📚 الترجمة — نص + ملف (4 أنماط)\n"
            "📄 PDF — تحويل، دمج، تقسيم، ضغط، استخراج OCR\n"
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
        if data == "adm_stats":
            stats = db.get_stats()
            await query.message.reply_text(
                f"📈 مستخدمون: {stats['users']} | امتحانات: {stats['exams']} | نتائج: {stats['results']}",
                reply_markup=ADMIN_MENU,
            )
            return
        if data == "adm_activity":
            rows = db.get_recent_activities(10)
            if not rows:
                await query.message.reply_text("لا نشاط.", reply_markup=ADMIN_MENU)
                return
            lines = ["📜 آخر النشاطات:"]
            for r in rows[:10]:
                action = ACTION_LABELS.get(r["action"], r["action"])
                lines.append(f"• {action}: {r.get('details', '')}")
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
            results = db.get_exam_results(exam_id)
            lines = [
                f"📊 نتائج {exam_id}",
                f"المشاركون: {stats['participants']}",
                f"المتوسط: {stats['avg_score']:.1f}%",
            ]
            for i, r in enumerate(results[:10], 1):
                name = r.get("full_name") or r.get("username") or r["user_id"]
                lines.append(f"{i}. {name} — {r['percentage']:.0f}%")
            await query.message.reply_text("\n".join(lines), reply_markup=ADMIN_MENU)
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

    return [
        exam_conv,
        notify_conv,
        channel_conv,
        question_conv,
        CallbackQueryHandler(admin_callback, pattern="^adm_"),
        MessageHandler(filters.Regex("^📊 لوحة التحكم$"), show_dashboard),
        MessageHandler(filters.Regex("^📈 الإحصائيات$"), show_stats),
        MessageHandler(filters.Regex("^👥 المستخدمون$"), show_users),
        MessageHandler(filters.Regex("^🏆 ترتيب الطلاب$"), show_leaderboard),
        MessageHandler(filters.Regex("^📋 إدارة الامتحانات$"), list_exams),
        MessageHandler(filters.Regex("^❓ الأسئلة الجاهزة$"), list_questions),
        MessageHandler(filters.Regex("^📜 سجل النشاط$"), show_activity),
        MessageHandler(filters.Regex("^🛠️ حالة الخدمات$"), show_services),
        MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), back_main_admin),
    ]
