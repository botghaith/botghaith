import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from database.db import Database
from services.channel_check import check_channel_subscription
from utils.helpers import format_percentage
from utils.keyboards import MAIN_MENU

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

        text = f"""🧑‍🎓 **ملفي الشخصي**

👤 الاسم: {profile.get('full_name', '—')}
🆔 المعرف: @{profile.get('username', '—')}
⭐ النقاط: {profile['points']}
📝 الامتحانات: {profile['exams_taken']}
🏆 الترتيب: #{rank}
📈 متوسط الدرجات: {format_percentage(avg_score) if results else '—'}
📅 تاريخ التسجيل: {profile['created_at'][:10]}
"""
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_MENU)

    async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_subscription(update, context, db):
            return
        help_text = """ℹ️ **دليل استخدام البوت**

📚 **الترجمة** — ترجمة نصوص وملفات وصور (عربي ↔ إنجليزي)
📄 **أدوات PDF** — تحويل، دمج، تقسيم، ضغط
📝 **الامتحانات** — اختبارات إلكترونية مع تصحيح تلقائي
🧑‍🎓 **حسابي** — نقاطك ونتائجك وترتيبك

من إعداد **المهندس غيث اسعد**"""
        await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=MAIN_MENU)

    return [
        MessageHandler(filters.Regex("^🧑‍🎓 حسابي$"), show_profile),
        MessageHandler(filters.Regex("^ℹ️ المساعدة$"), show_help),
    ]
