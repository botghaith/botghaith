"""حماية الكروبات: حذف تحويلات وروابط القنوات غير المسموحة."""

import logging
import re

from telegram import MessageOriginChannel, MessageOriginChat, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_USERNAME
from database.db import Database
from utils.helpers import is_admin
from utils.keyboards import (
    ADMIN_MENU,
    MAIN_MENU,
    guard_channel_delete_keyboard,
    guard_groups_keyboard,
    guard_home_keyboard,
    guard_input_keyboard,
)
from utils import states

logger = logging.getLogger(__name__)

_SKIP_PATHS = {
    "joinchat", "addstickers", "addemoji", "share", "iv", "proxy", "socks",
    "login", "invoice", "boost", "c", "s", "addlist", "giftcode", "m",
}
_INVITE_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:\+|joinchat/)", re.I)
_PUBLIC_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,})",
    re.I,
)
_PRIVATE_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/c/(\d+)", re.I)
_AT_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,})")
_DONE = {"تم", "/done", "✅ تم"}


def _norm(value: str) -> str:
    return (value or "").strip().lstrip("@").lower()


def _channel_label(row: dict) -> str:
    title = (row.get("title") or "").strip()
    username = (row.get("username") or "").strip()
    if username.startswith("id") and username[2:].lstrip("-").isdigit():
        name = title or username
    elif username:
        name = f"@{username}"
    else:
        name = title or "قناة"
    if title and username and not username.startswith("id"):
        return f"{title} (@{username})"
    return name


def _collect_refs(text: str) -> tuple[list[str], list[int], bool]:
    usernames: list[str] = []
    private_ids: list[int] = []
    for match in _PUBLIC_RE.finditer(text or ""):
        name = match.group(1).lower()
        if name not in _SKIP_PATHS:
            usernames.append(name)
    for match in _PRIVATE_RE.finditer(text or ""):
        private_ids.append(int(f"-100{match.group(1)}"))
    return usernames, private_ids, bool(_INVITE_RE.search(text or ""))


def _message_text(message) -> str:
    parts = [message.text or "", message.caption or ""]
    markup = message.reply_markup
    if markup and getattr(markup, "inline_keyboard", None):
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.url:
                    parts.append(btn.url)
    return "\n".join(parts)


def _origin_channel(message):
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        return origin.chat
    if isinstance(origin, MessageOriginChat) and origin.sender_chat.type == "channel":
        return origin.sender_chat
    sender = message.sender_chat
    if sender is not None and sender.type == "channel":
        return sender
    return None


def _allowed_maps(db: Database, chat_id: int) -> tuple[set[str], set[int]]:
    names: set[str] = set()
    ids: set[int] = set()
    for row in db.list_global_channels() + db.list_group_channels(chat_id):
        username = _norm(row.get("username") or "")
        if username:
            names.add(username)
        if row.get("channel_id"):
            ids.add(int(row["channel_id"]))
    return names, ids


def _chat_allowed(chat, names: set[str], ids: set[int]) -> bool:
    if chat.id in ids:
        return True
    username = _norm(getattr(chat, "username", "") or "")
    return bool(username and username in names)


def message_blocked(db: Database, message, bot_username: str) -> bool:
    """True عندما الرسالة تحويل أو رابط لقناة غير مسموحة."""
    group = db.get_protected_group(message.chat_id)
    if not group or not group.get("is_active", 1):
        return False
    names, ids = _allowed_maps(db, message.chat_id)
    origin = _origin_channel(message)
    if origin is not None and not _chat_allowed(origin, names, ids):
        return True
    usernames, private_ids, invite = _collect_refs(_message_text(message))
    bot_name = _norm(bot_username)
    for username in usernames:
        if username in _SKIP_PATHS or username == bot_name:
            continue
        if username not in names:
            return True
    for channel_id in private_ids:
        if channel_id not in ids:
            return True
    return invite


def _parse_entries(text: str) -> list[dict]:
    entries: list[dict] = []
    seen: set[str] = set()
    body = text or ""
    for match in _PRIVATE_RE.finditer(body):
        channel_id = int(f"-100{match.group(1)}")
        key = f"id{channel_id}"
        if key not in seen:
            seen.add(key)
            entries.append({"username": key, "channel_id": channel_id, "raw": key})
    for match in _PUBLIC_RE.finditer(body):
        name = match.group(1).lower()
        if name in _SKIP_PATHS or name in seen:
            continue
        seen.add(name)
        entries.append({"username": name, "channel_id": None, "raw": f"@{name}"})
    for match in _AT_RE.finditer(body):
        name = match.group(1).lower()
        if name in seen:
            continue
        seen.add(name)
        entries.append({"username": name, "channel_id": None, "raw": f"@{name}"})
    for line in body.splitlines():
        token = _norm(line.split()[0] if line.split() else "")
        if not token or token in seen or token in _DONE or token in _SKIP_PATHS:
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_]{3,}", token):
            continue
        seen.add(token)
        entries.append({"username": token, "channel_id": None, "raw": f"@{token}"})
    return entries


async def _resolve_entry(bot, entry: dict) -> tuple[dict | None, str]:
    if entry.get("channel_id") and str(entry["username"]).startswith("id"):
        return {
            "username": entry["username"],
            "channel_id": entry["channel_id"],
            "title": "",
        }, ""
    try:
        chat = await bot.get_chat(f"@{entry['username']}")
    except TelegramError:
        return {
            "username": entry["username"],
            "channel_id": entry.get("channel_id"),
            "title": "",
        }, ""
    if chat.type != "channel":
        return None, f"{entry['raw']} مو قناة"
    return {
        "username": _norm(chat.username or entry["username"]),
        "channel_id": chat.id,
        "title": chat.title or "",
    }, ""


def _home_text(db: Database, user_id: int) -> str:
    groups = db.list_groups_by_owner(user_id)
    globals_ = db.list_global_channels()
    lines = [
        "🛡️ حماية الكروبات",
        "",
        "أولاً: أضف البوت إلى الكروب واصعده مشرف، وأعطه الصلاحيات:",
        "• حذف الرسائل",
        "• إرسال الرسائل",
        "",
        "بعدها إذا تريد قنوات مسموحة، اضغط الزر وأرسل القنوات التي تريد السماح دائماً بتحويل الرسائل والمنشورات منها.",
        "أي تحويل أو رابط قناة غير مسموحة ينحذف.",
        "القنوات التي يحددها أدمن البوت تبقى مسموحة في كل الكروبات.",
    ]
    if globals_:
        lines.append("")
        lines.append("قنوات الأدمن المسموحة دائماً:")
        lines.extend(f"• {_channel_label(row)}" for row in globals_[:15])
    lines.append("")
    if groups:
        lines.append("كروباتك المفعّلة:")
        for group in groups:
            count = len(db.list_group_channels(group["chat_id"]))
            title = group.get("title") or "كروب"
            lines.append(f"• {title} — {count} قناة")
    else:
        lines.append("ما عندك كروب مفعّل بعد. أضف البوت واصعده مشرف أولاً.")
    return "\n".join(lines)


def _ask_channels_text(db: Database, title: str) -> str:
    globals_ = db.list_global_channels()
    lines = [
        f"الكروب: {title}",
        "",
        "أرسل القنوات التي تريد السماح دائماً بتحويل الرسائل والمنشورات منها.",
        "كل قناة بسطر، أو رابطها، أو @يوزرها.",
        "مثال:",
        "@channel",
        "https://t.me/channel",
        "",
        "إذا انتهيت اضغط ✅ تم.",
    ]
    if globals_:
        lines.append("")
        lines.append("هذه قنوات الأدمن ومسموحة أصلاً بدون ما تضيفها:")
        lines.extend(f"• {_channel_label(row)}" for row in globals_[:15])
    return "\n".join(lines)


def setup_group_guard_handlers(db: Database, back_to_main) -> list:
    async def _show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        text = _home_text(db, user.id)
        query = update.callback_query
        if query:
            await query.answer()
            try:
                await query.edit_message_text(text, reply_markup=guard_home_keyboard())
                return
            except BadRequest:
                await query.message.reply_text(text, reply_markup=guard_home_keyboard())
                return
        await update.message.reply_text(text, reply_markup=guard_home_keyboard())

    async def open_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await _show_home(update, context)
        return ConversationHandler.END

    async def start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        groups = db.list_groups_by_owner(update.effective_user.id)
        if not groups:
            await query.message.reply_text(
                "ما لك كروب مفعّل بعد.\n\n"
                "1. أضف البوت إلى الكروب.\n"
                "2. اصعده مشرف وأعطه حذف الرسائل وإرسال الرسائل.\n"
                "3. ارجع واضغط 🛡️ حماية الكروبات ثم أضف القنوات.",
                reply_markup=MAIN_MENU,
            )
            return ConversationHandler.END
        if len(groups) == 1:
            context.user_data["guard_chat_id"] = groups[0]["chat_id"]
            title = groups[0].get("title") or "الكروب"
            await query.message.reply_text(
                _ask_channels_text(db, title),
                reply_markup=guard_input_keyboard(),
            )
            return states.GUARD_WAIT_CHANNELS
        await query.message.reply_text(
            "اختر الكروب الذي تريد السماح بالتحويل من قنوات فيه:",
            reply_markup=guard_groups_keyboard(groups),
        )
        return states.GUARD_PICK_GROUP

    async def pick_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        chat_id = int(query.data.replace("gg_pick_", "", 1))
        group = db.get_protected_group(chat_id)
        user_id = update.effective_user.id
        if not group or group.get("owner_id") != user_id or not group.get("is_active", 1):
            await query.message.reply_text("هذا الكروب مو من كروباتك المفعّلة.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        context.user_data["guard_chat_id"] = chat_id
        await query.message.reply_text(
            _ask_channels_text(db, group.get("title") or "الكروب"),
            reply_markup=guard_input_keyboard(),
        )
        return states.GUARD_WAIT_CHANNELS

    async def receive_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if text in ("🔙 القائمة الرئيسية", "🏠 القائمة الرئيسية"):
            return await back_to_main(update, context)
        chat_id = context.user_data.get("guard_chat_id")
        group = db.get_protected_group(chat_id) if chat_id else None
        if not group or group.get("owner_id") != update.effective_user.id:
            await update.message.reply_text("انتهت الجلسة. افتح 🛡️ حماية الكروبات من جديد.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        if text in _DONE:
            rows = db.list_group_channels(chat_id)
            shown = "\n".join(f"• {_channel_label(row)}" for row in rows) or "• لا توجد قنوات إضافية"
            await update.message.reply_text(
                f"تم حفظ قنوات {group.get('title') or 'الكروب'}:\n{shown}\n\n"
                "قنوات أدمن البوت تبقى مسموحة حتى لو مو بهالقائمة.",
                reply_markup=MAIN_MENU,
            )
            return ConversationHandler.END
        entries = _parse_entries(text)
        if not entries:
            await update.message.reply_text(
                "أرسل @يوزر القناة أو رابطها، أو اضغط ✅ تم.",
                reply_markup=guard_input_keyboard(),
            )
            return states.GUARD_WAIT_CHANNELS
        added, rejected = [], []
        for entry in entries:
            resolved, reason = await _resolve_entry(context.bot, entry)
            if not resolved:
                rejected.append(reason or entry["raw"])
                continue
            db.add_group_channel(
                chat_id, resolved["username"], resolved.get("channel_id"), resolved.get("title") or "",
            )
            added.append(_channel_label(resolved))
        lines = []
        if added:
            lines.append("تمت الإضافة:\n" + "\n".join(f"• {name}" for name in added))
        if rejected:
            lines.append("ما انضافت:\n" + "\n".join(f"• {name}" for name in rejected))
        lines.append("\nأرسل قنوات أخرى أو اضغط ✅ تم.")
        await update.message.reply_text("\n\n".join(lines), reply_markup=guard_input_keyboard())
        return states.GUARD_WAIT_CHANNELS

    async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        groups = db.list_groups_by_owner(update.effective_user.id)
        if not groups:
            await query.message.reply_text("ما عندك كروبات مفعّلة.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        buttons = []
        lines = ["📋 القنوات المسموحة في كروباتك", ""]
        for group in groups:
            lines.append(group.get("title") or "كروب")
            rows = db.list_group_channels(group["chat_id"])
            if not rows:
                lines.append("• لا توجد قنوات إضافية")
            for row in rows:
                lines.append(f"• {_channel_label(row)}")
                buttons.append((f"❌ {_channel_label(row)}", f"gg_del_{row['id']}"))
            lines.append("")
        globals_ = db.list_global_channels()
        if globals_:
            lines.append("مسموحة دائماً من أدمن البوت:")
            lines.extend(f"• {_channel_label(row)}" for row in globals_)
        markup = guard_channel_delete_keyboard(buttons) if buttons else guard_home_keyboard()
        await query.message.reply_text("\n".join(lines).strip(), reply_markup=markup)
        return ConversationHandler.END

    async def delete_group_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        row_id = int(query.data.replace("gg_del_", "", 1))
        row = db.get_group_channel(row_id)
        if not row:
            await query.message.reply_text("القناة مو موجودة.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        group = db.get_protected_group(row["chat_id"])
        if not group or group.get("owner_id") != update.effective_user.id:
            await query.message.reply_text("ما تكدر تحذف هالقناة.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        db.remove_group_channel(row_id)
        await query.message.reply_text(f"تم حذف {_channel_label(row)} من المسموحات.", reply_markup=MAIN_MENU)
        return ConversationHandler.END

    async def open_admin_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.username or "", ADMIN_USERNAME):
            await update.message.reply_text("❌ هذا الزر للأدمن فقط.")
            return ConversationHandler.END
        rows = db.list_global_channels()
        lines = [
            "🛡️ قنوات الحماية",
            "",
            "هذه القنوات يبقى التحويل منها وإرسال رابطها مسموحاً في كل الكروبات،",
            "حتى لو الشخص الذي صعّد البوت مشرف ما أضافها ضمن قنوات الكروب.",
            "",
            "أرسل القنوات التي تريد السماح دائماً بتحويل الرسائل والمنشورات منها.",
            "كل قناة بسطر أو رابط أو @يوزر. إذا انتهيت اضغط ✅ تم.",
        ]
        if rows:
            lines.append("")
            lines.append("الحالية:")
            lines.extend(f"• {_channel_label(row)}" for row in rows)
        buttons = [(f"❌ {_channel_label(row)}", f"gga_del_{row['id']}") for row in rows]
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=guard_input_keyboard(),
        )
        if buttons:
            await update.message.reply_text(
                "حذف قناة من القائمة العامة:",
                reply_markup=guard_channel_delete_keyboard(buttons, with_add=False),
            )
        return states.GUARD_ADMIN_CHANNELS

    async def receive_admin_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.username or "", ADMIN_USERNAME):
            return ConversationHandler.END
        text = (update.message.text or "").strip()
        if text in ("🔙 القائمة الرئيسية", "🏠 القائمة الرئيسية"):
            await update.message.reply_text("رجعت للوحة الأدمن.", reply_markup=ADMIN_MENU)
            return ConversationHandler.END
        if text in _DONE:
            rows = db.list_global_channels()
            shown = "\n".join(f"• {_channel_label(row)}" for row in rows) or "• لا توجد قنوات"
            await update.message.reply_text(
                f"القنوات المسموحة دائماً:\n{shown}",
                reply_markup=ADMIN_MENU,
            )
            return ConversationHandler.END
        entries = _parse_entries(text)
        if not entries:
            await update.message.reply_text("أرسل @يوزر القناة أو رابطها، أو اضغط ✅ تم.", reply_markup=guard_input_keyboard())
            return states.GUARD_ADMIN_CHANNELS
        added, rejected = [], []
        for entry in entries:
            resolved, reason = await _resolve_entry(context.bot, entry)
            if not resolved:
                rejected.append(reason or entry["raw"])
                continue
            db.add_global_channel(
                resolved["username"], resolved.get("channel_id"), resolved.get("title") or "",
            )
            added.append(_channel_label(resolved))
        lines = []
        if added:
            lines.append("تمت الإضافة لكل الكروبات:\n" + "\n".join(f"• {name}" for name in added))
        if rejected:
            lines.append("ما انضافت:\n" + "\n".join(f"• {name}" for name in rejected))
        lines.append("\nأرسل قنوات أخرى أو اضغط ✅ تم.")
        await update.message.reply_text("\n\n".join(lines), reply_markup=guard_input_keyboard())
        return states.GUARD_ADMIN_CHANNELS

    async def delete_global_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not is_admin(update.effective_user.username or "", ADMIN_USERNAME):
            await query.message.reply_text("❌ هذا الزر للأدمن فقط.")
            return ConversationHandler.END
        row_id = int(query.data.replace("gga_del_", "", 1))
        row = db.get_global_channel(row_id)
        if not row:
            await query.message.reply_text("القناة مو موجودة.", reply_markup=ADMIN_MENU)
            return states.GUARD_ADMIN_CHANNELS
        db.remove_global_channel(row_id)
        await query.message.reply_text(
            f"تم حذف {_channel_label(row)} من القنوات العامة.",
            reply_markup=ADMIN_MENU,
        )
        return states.GUARD_ADMIN_CHANNELS

    async def on_my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        member = update.my_chat_member
        if member is None or member.chat.type not in ("group", "supergroup"):
            return
        new = member.new_chat_member
        old = member.old_chat_member
        chat = member.chat
        promoter = member.from_user
        owner_id = promoter.id if promoter else 0
        was_ready = (
            old.status == "administrator" and getattr(old, "can_delete_messages", False)
        )
        is_admin_now = new.status == "administrator"
        can_delete = bool(getattr(new, "can_delete_messages", False))
        try:
            if is_admin_now and can_delete:
                db.save_protected_group(chat.id, chat.title or "", owner_id, True)
            elif new.status in ("left", "kicked", "member", "restricted") or (is_admin_now and not can_delete):
                db.save_protected_group(chat.id, chat.title or "", owner_id, False)
        except Exception:
            logger.exception("Failed to save protected group %s", chat.id)
            return
        if is_admin_now and can_delete:
            if not was_ready:
                try:
                    await context.bot.send_message(
                        chat.id,
                        "🛡️ حماية الكروبات شغّالة.\n"
                        "التحويل من القنوات وروابطها ينحذف، إلا القنوات المسموحة.\n"
                        "اللي صعّد البوت يحدد القنوات من الخاص عبر زر 🛡️ حماية الكروبات.",
                    )
                except TelegramError:
                    logger.warning("Could not announce protection in %s", chat.id)
            return
        if is_admin_now and not can_delete:
            try:
                await context.bot.send_message(
                    chat.id,
                    "حتى أحمي الكروب أعطني صلاحية حذف الرسائل، مع إرسال الرسائل.",
                )
            except TelegramError:
                pass
            return
        if new.status == "member" and old.status in ("left", "kicked"):
            try:
                await context.bot.send_message(
                    chat.id,
                    "أُضيف البوت للكروب.\n"
                    "اصعده مشرف وأعطه حذف الرسائل وإرسال الرسائل،\n"
                    "ثم من الخاص اضغط 🛡️ حماية الكروبات لتحديد القنوات المسموحة.",
                )
            except TelegramError:
                pass

    async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if message is None or message.chat.type not in ("group", "supergroup"):
            return
        user = message.from_user
        if user and user.is_bot and user.id == context.bot.id:
            return
        try:
            blocked = message_blocked(db, message, context.bot.username or "")
        except Exception:
            logger.exception("Group protection check failed")
            return
        if not blocked:
            return
        try:
            await message.delete()
        except TelegramError:
            logger.warning("Failed to delete blocked message in %s", message.chat_id)
        raise ApplicationHandlerStop

    user_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🛡️ حماية الكروبات$") & filters.ChatType.PRIVATE,
                open_guard,
            ),
            CallbackQueryHandler(start_add, pattern="^gg_add$"),
            CallbackQueryHandler(pick_group, pattern=r"^gg_pick_-?\d+$"),
            CallbackQueryHandler(show_list, pattern="^gg_list$"),
            CallbackQueryHandler(delete_group_channel, pattern=r"^gg_del_\d+$"),
        ],
        states={
            states.GUARD_PICK_GROUP: [
                CallbackQueryHandler(pick_group, pattern=r"^gg_pick_-?\d+$"),
                CallbackQueryHandler(start_add, pattern="^gg_add$"),
            ],
            states.GUARD_WAIT_CHANNELS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channels),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🔙 القائمة الرئيسية$|^🏠 القائمة الرئيسية$"), back_to_main),
        ],
    )
    admin_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🛡️ قنوات الحماية$") & filters.ChatType.PRIVATE,
                open_admin_channels,
            ),
            CallbackQueryHandler(delete_global_channel, pattern=r"^gga_del_\d+$"),
        ],
        states={
            states.GUARD_ADMIN_CHANNELS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_channels),
                CallbackQueryHandler(delete_global_channel, pattern=r"^gga_del_\d+$"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🔙 القائمة الرئيسية$|^🏠 القائمة الرئيسية$"), back_to_main),
        ],
    )
    group_messages = filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL
    return [
        user_conv,
        admin_conv,
        ChatMemberHandler(on_my_status, ChatMemberHandler.MY_CHAT_MEMBER),
    ], [
        MessageHandler(group_messages, on_group_message),
        MessageHandler(filters.ChatType.GROUPS & filters.UpdateType.EDITED_MESSAGE, on_group_message),
    ]
