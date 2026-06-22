import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import ADMIN_USERNAME
from database.db import Database
from utils.helpers import is_admin
from utils.keyboards import channel_subscribe_keyboard

logger = logging.getLogger(__name__)


async def _is_subscribed(bot, username: str, user_id: int) -> bool:
    channel = f"@{username.lstrip('@')}"
    try:
        member = await bot.get_chat_member(channel, user_id)
        return member.status in ("member", "administrator", "creator", "restricted")
    except Exception as e:
        logger.warning(f"Channel check failed for {channel}: {e}")
        return False


async def _get_missing_channels(bot, channels: list, user_id: int) -> list[dict]:
    missing = []
    for ch in channels:
        if not await _is_subscribed(bot, ch["username"], user_id):
            missing.append(ch)
    return missing


def _subscription_message(missing: list[dict]) -> str:
    lines = [
        "⚠️ الاشتراك في القنوات مطلوب!\n",
        "يجب الاشتراك في جميع القنوات التالية:",
    ]
    for ch in missing:
        label = ch.get("title") or f"@{ch['username']}"
        lines.append(f"• {label}")
    lines.append("\nاضغط الأزرار للاشتراك ثم «تحقق من الاشتراك».")
    return "\n".join(lines)


async def _send_subscription_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, markup
) -> None:
    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(text, reply_markup=markup)
        elif update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=markup)
        elif update.effective_chat:
            await context.bot.send_message(
                update.effective_chat.id, text, reply_markup=markup
            )
        else:
            logger.error("Cannot send subscription prompt: no chat/message")
    except Exception as e:
        logger.exception(f"Failed to send subscription prompt: {e}")
        if update.effective_chat:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ يجب الاشتراك في القنوات المطلوبة أولاً.\n"
                "أرسل /start ثم اضغط «تحقق من الاشتراك».",
            )


async def check_channel_for_translation(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """تحقق اشتراك القناة بدون Supabase — من env فقط."""
    from config import CHANNEL_REQUIRED, CHANNEL_USERNAME, CHANNEL_LINK

    if context.user_data.get("tr_channel_ok"):
        return True

    if not CHANNEL_REQUIRED:
        context.user_data["tr_channel_ok"] = True
        return True

    username = CHANNEL_USERNAME.lstrip("@")
    if not username:
        context.user_data["tr_channel_ok"] = True
        return True

    channels = [{
        "username": username,
        "link": CHANNEL_LINK or f"https://t.me/{username}",
        "title": username,
    }]
    user_id = update.effective_user.id
    missing = await _get_missing_channels(context.bot, channels, user_id)
    if missing:
        text = _subscription_message(missing)
        markup = channel_subscribe_keyboard(missing)
        await _send_subscription_prompt(update, context, text, markup)
        return False

    context.user_data["tr_channel_ok"] = True
    return True


async def check_channel_subscription(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database
) -> bool:
    """يرجع True إذا كان المستخدم مشتركاً في كل القنوات أو الاشتراك غير مطلوب"""
    config = db.get_channels_config()
    if not config["enabled"]:
        return True

    channels = config["channels"]
    if not channels:
        return True

    user_id = update.effective_user.id
    missing = await _get_missing_channels(context.bot, channels, user_id)
    if not missing:
        return True

    text = _subscription_message(missing)
    markup = channel_subscribe_keyboard(missing)
    await _send_subscription_prompt(update, context, text, markup)
    return False


async def handle_check_subscription(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database
):
    query = update.callback_query
    await query.answer()

    config = db.get_channels_config()
    user_id = update.effective_user.id

    if not config["enabled"] or not config["channels"]:
        await query.edit_message_text("✅ الاشتراك غير مطلوب حالياً.")
        return True

    missing = await _get_missing_channels(context.bot, config["channels"], user_id)
    if missing:
        await _send_subscription_prompt(
            update,
            context,
            _subscription_message(missing),
            channel_subscribe_keyboard(missing),
        )
        await query.answer("❌ اشترك في كل القنوات أولاً!", show_alert=True)
        return False

    pending_exam = context.user_data.pop("pending_exam", None)
    direct = context.user_data.pop("exam_direct_start", False)
    if pending_exam:
        from handlers.exams import open_exam_for_user
        await query.edit_message_text("✅ تم التحقق! جاري فتح الامتحان...")
        await open_exam_for_user(
            context, db, pending_exam,
            query.message.chat_id, user_id,
            direct=direct,
        )
    else:
        from utils.keyboards import MAIN_MENU
        await query.edit_message_text(
            "✅ تم التحقق! أنت مشترك في جميع القنوات.\nمرحباً بك في البوت 👋"
        )
        await query.message.reply_text(
            "اختر القسم من القائمة:",
            reply_markup=MAIN_MENU,
        )
    return True


def _is_guard_exempt(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return True

    if is_admin(user.username or "", ADMIN_USERNAME):
        return True

    if update.callback_query and update.callback_query.data == "check_subscription":
        return True

    if update.message and update.message.text:
        text = update.message.text.strip()
        if text.startswith("/cancel"):
            return True
        if text.startswith("/start"):
            return True

    return False


def make_channel_guard(db: Database):
    async def channel_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if _is_guard_exempt(update):
            return

        config = db.get_channels_config()
        if not config["enabled"] or not config["channels"]:
            return

        if not await check_channel_subscription(update, context, db):
            raise ApplicationHandlerStop()

    return channel_guard
