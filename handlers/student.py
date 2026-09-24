import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from database.db import Database
from services.channel_check import check_channel_subscription
from utils.helpers import format_percentage
from utils.keyboards import MAIN_MENU
from utils.activity_log import log_user_activity

logger = logging.getLogger(__name__)


def setup_student_handlers(db: Database) -> list:
    async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_subscription(update, context, db):
            return

        user = update.effective_user
        db.upsert_user(user.id, user.username or "", user.full_name or "")
        profile = db.get_user(user.id)

        if not profile:
            await update.message.reply_text("❌ لم يتم العثور على ملفك.")
            return

        board = db.get_leaderboard(100)
        rank = next(
            (i + 1 for i, s in enumerate(board) if s["user_id"] == user.id),
            "—",
        )

        results = db.get_user_results(user.id)
        avg_score = 0
        if results:
            avg_score = sum(r["percentage"] for r in results) / len(results)

        uname = (profile.get("username") or "").strip().lstrip("@")
        handle = f"@{uname}" if uname else "بدون يوزر"
        text = (
            "🧑‍🎓 ملفي الشخصي\n\n"
            f"👤 الاسم: {profile.get('full_name') or '—'}\n"
            f"🆔 اليوزر: {handle}\n"
            f"🔢 المعرف: {profile.get('user_id', user.id)}\n"
            f"⭐ النقاط: {profile['points']}\n"
            f"📝 الامتحانات: {profile['exams_taken']}\n"
            f"🏆 الترتيب: #{rank}\n"
            f"📈 متوسط الدرجات: {format_percentage(avg_score) if results else '—'}\n"
            f"📅 تاريخ التسجيل: {str(profile.get('created_at') or '')[:10]}"
        )
        await update.message.reply_text(text, reply_markup=MAIN_MENU)
        log_user_activity(db, user.id, "view_profile")

    async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_subscription(update, context, db):
            return
        help_text = """ℹ️ **دليل استخدام البوت**

📚 **الترجمة** — ترجمة نصوص وملفات وصور (عربي ↔ إنجليزي)
📋 **واجهة تقرير** — غلاف أكاديمي PDF أو Word
📄 **أدوات PDF** — تحويل، دمج، تقسيم، ضغط، سلايدات
📝 **الامتحانات** — اختبارات إلكترونية مع تصحيح تلقائي
🧑‍🎓 **حسابي** — نقاطك ونتائجك وترتيبك
🛡️ **حماية الكروبات** — منع تحويل وروابط القنوات غير المسموحة

من إعداد **المهندس غيث اسعد**"""
        await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=MAIN_MENU)

    return [
        MessageHandler(filters.Regex("^🧑‍🎓 حسابي$"), show_profile),
        MessageHandler(filters.Regex("^ℹ️ المساعدة$"), show_help),
    ]
