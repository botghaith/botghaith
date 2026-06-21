import logging

from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ApplicationHandlerStop,
)

from config import WELCOME_MESSAGE, ADMIN_USERNAME
from database.db import Database
from services.channel_check import check_channel_subscription, handle_check_subscription
from handlers.exams import show_exam_entry
from utils.helpers import is_admin, parse_exam_id_from_start
from utils.keyboards import MAIN_MENU, ADMIN_MENU, admin_dashboard_inline

logger = logging.getLogger(__name__)

MAIN_MENU_PATTERN = filters.Regex("^🏠 القائمة الرئيسية$|^🔙 القائمة الرئيسية$")


def setup_start_handlers(db: Database) -> list:
    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        chat_id = update.effective_chat.id
        db.upsert_user(user.id, user.username or "", user.full_name or "")

        exam_id = parse_exam_id_from_start(context.args or [])
        if exam_id:
            context.user_data["pending_exam"] = exam_id
            context.user_data["exam_direct_start"] = True

        try:
            if not await check_channel_subscription(update, context, db):
                return

            pending = context.user_data.pop("pending_exam", None)
            direct = context.user_data.pop("exam_direct_start", False)
            if pending:
                from handlers.exams import open_exam_for_user
                await open_exam_for_user(
                    context, db, pending,
                    chat_id, user.id,
                    direct=direct,
                )
                return

            text = WELCOME_MESSAGE
            if is_admin(user.username or "", ADMIN_USERNAME):
                text += "\n\n🔧 أنت الأدمن — أرسل /admin للوحة التحكم"

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=MAIN_MENU,
            )
            db.log_activity(user.id, "start")
        except Exception:
            logger.exception("start_cmd failed for user %s", user.id)
            await context.bot.send_message(
                chat_id,
                "مرحباً بك! 👋\nاختر قسماً من القائمة أدناه.",
                reply_markup=MAIN_MENU,
            )

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_subscription(update, context, db):
            return
        help_text = """ℹ️ **دليل استخدام البوت**

📚 **الترجمة** — ترجمة نصوص وملفات وصور (عربي ↔ إنجليزي)
📄 **أدوات PDF** — تحويل، دمج، تقسيم، ضغط
📝 **الامتحانات** — اختبارات إلكترونية مع تصحيح تلقائي
🧑‍🎓 **حسابي** — نقاطك ونتائجك وترتيبك

🔧 الأوامر:
/start — البداية
/help — المساعدة
/admin — لوحة الأدمن (للأدمن فقط)
/cancel — إلغاء العملية الحالية

من إعداد **المهندس غيث اسعد**"""
        await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=MAIN_MENU)

    async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.username or "", ADMIN_USERNAME):
            await update.message.reply_text("❌ هذا الأمر للأدمن فقط.")
            return
        await update.message.reply_text(
            "🔧 لوحة تحكم الأدمن\n"
            "جميع الخدمات مفعّلة ✅\n"
            "اختر الإجراء من القائمة:",
            reply_markup=ADMIN_MENU,
        )
        stats = db.get_stats()
        await update.message.reply_text(
            f"📊 ملخص سريع: {stats['users']} مستخدم | {stats['exams']} امتحان | {stats['results']} نتيجة",
            reply_markup=admin_dashboard_inline(),
        )

    async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=MAIN_MENU)

    async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_check_subscription(update, context, db)

    async def go_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.username or "", ADMIN_USERNAME):
            if not await check_channel_subscription(update, context, db):
                return ConversationHandler.END

        context.user_data.clear()
        chat_id = update.effective_chat.id
        await context.bot.send_message(
            chat_id,
            "🏠 القائمة الرئيسية\n\n"
            "💡 العمليات الجارية تستمر في الخلفية — سيصلك الناتج عند الانتهاء.",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END

    async def main_menu_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await go_main_menu(update, context)
        raise ApplicationHandlerStop()

    async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await go_main_menu(update, context)

    return [
        MessageHandler(MAIN_MENU_PATTERN, main_menu_priority),
        CommandHandler("start", start_cmd),
        CommandHandler("help", help_cmd),
        CommandHandler("admin", admin_cmd),
        CommandHandler("cancel", cancel_cmd),
        CallbackQueryHandler(check_sub_callback, pattern="^check_subscription$"),
    ], back_to_main
