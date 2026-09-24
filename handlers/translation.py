import asyncio
import logging
import re
from pathlib import Path

from telegram import Update
from telegram.error import TimedOut, NetworkError
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler, CallbackQueryHandler, filters,
)

from services.channel_check import check_channel_for_translation
from services.file_translator import (
    translate_file_two_modes,
    translate_image_two_modes,
    prepare_full_file_translation,
    prepare_full_image_translation,
    build_full_file_overlay,
    build_full_image_overlay,
)
from config import (
    use_online_translate,
    telegram_connect_timeout,
    telegram_read_timeout,
    telegram_write_timeout,
    file_download_max_attempts,
    file_download_retry_wait,
    file_prepare_timeout,
    file_overlay_timeout,
)
from services.translator import (
    translate_text_dual,
    resolve_direction,
    direction_label,
    is_translator_ready,
)
from services.text_shape import (
    set_font_scale,
    set_translation_color,
    FONT_SCALES,
    FONT_LABELS,
    DEFAULT_FONT_INDEX,
    TRANSLATION_COLORS,
    TRANSLATION_COLOR_LABELS,
)
from utils.helpers import get_user_temp_dir, truncate_text
from utils.background_jobs import spawn_background, progress_ticker
from utils.keyboards import (
    translation_menu,
    translation_direction_reply_menu,
    translation_color_keyboard,
    parse_direction_text,
    MAIN_MENU,
)
from utils import states
from utils.activity_log import log_user_activity

logger = logging.getLogger(__name__)

MSG_LIMIT = 3800
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
FONT_MINUS = "➖ تصغير الخط"
FONT_PLUS = "➕ تكبير الخط"
FONT_SIZE_BUTTONS = {
    "🔠 صغير": 0,
    "🔠 متوسط": 1,
    "🔠 كبير": 2,
}
FONT_BUTTONS = set(FONT_SIZE_BUTTONS) | {FONT_MINUS, FONT_PLUS}


def _is_font_button(text: str | None) -> bool:
    return (text or "").strip() in FONT_BUTTONS


def _font_index(context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        idx = int(context.user_data.get("tr_font_index", DEFAULT_FONT_INDEX))
    except (TypeError, ValueError):
        idx = DEFAULT_FONT_INDEX
    return max(0, min(len(FONT_SCALES) - 1, idx))


def _run_with_font(context: ContextTypes.DEFAULT_TYPE, fn, *args):
    scale = FONT_SCALES[_font_index(context)]
    color = context.user_data.get("tr_color", "black")

    def _inner():
        set_font_scale(scale)
        set_translation_color(color)
        try:
            return fn(*args)
        finally:
            set_font_scale(1.0)
            set_translation_color("black")

    return asyncio.to_thread(_inner)


def _stay_in_translation(context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("tr_pending"):
        return states.TR_WAIT_COLOR
    mode = context.user_data.get("tr_mode")
    if context.user_data.get("tr_direction") and mode == "text":
        return states.TR_WAIT_TEXT
    if context.user_data.get("tr_direction") and mode == "file":
        return states.TR_WAIT_FILE
    if context.user_data.get("tr_direction") and mode == "image":
        return states.TR_WAIT_IMAGE
    if mode:
        return states.TR_WAIT_DIRECTION
    return ConversationHandler.END


async def adjust_translation_font(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    idx = _font_index(context)
    if text in FONT_SIZE_BUTTONS:
        idx = FONT_SIZE_BUTTONS[text]
        msg = f"✅ حجم خط الترجمة: {FONT_LABELS[idx]}"
    elif text == FONT_PLUS:
        if idx >= len(FONT_SCALES) - 1:
            msg = f"حجم الخط في الحد الأقصى ({FONT_LABELS[idx]})."
        else:
            idx += 1
            msg = f"✅ حجم خط الترجمة: {FONT_LABELS[idx]}"
    elif text == FONT_MINUS:
        if idx <= 0:
            msg = f"حجم الخط في الحد الأدنى ({FONT_LABELS[idx]})."
        else:
            idx -= 1
            msg = f"✅ حجم خط الترجمة: {FONT_LABELS[idx]}"
    else:
        return _stay_in_translation(context)
    context.user_data["tr_font_index"] = idx
    await update.message.reply_text(
        f"{msg}\nسيُطبَّق على ملفات الترجمة التالية.",
        reply_markup=translation_menu(),
    )
    return _stay_in_translation(context)


async def _download_tg_file(status_msg, tg_file, dest: str):
    """تحميل ملف من تيليجرام مع إعادة المحاولة."""
    attempts = file_download_max_attempts()
    for attempt in range(attempts):
        try:
            await tg_file.download_to_drive(dest)
            return
        except (TimedOut, NetworkError):
            if attempt == attempts - 1:
                raise
            await status_msg.edit_text(
                f"⏳ إعادة تحميل الملف... ({attempt + 2}/{attempts})"
            )
            await asyncio.sleep(file_download_retry_wait())


def _split_message(text: str, limit: int = MSG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for block in text.split("\n\n"):
        if len(current) + len(block) + 2 <= limit:
            current = f"{current}\n\n{block}".strip()
        else:
            if current:
                parts.append(current)
            current = block
    if current:
        parts.append(current)
    return parts or [text[:limit]]


def setup_translation_handlers(db=None, back_to_main=None) -> ConversationHandler:
    if back_to_main is None and callable(db):
        back_to_main = db
        db = None
    async def enter_translation(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_for_translation(update, context):
            return ConversationHandler.END
        ready = "✅ جاهز للترجمة" if use_online_translate() else (
            "✅ المحرك جاهز" if is_translator_ready() else "⏳ جاري تحميل محرك الترجمة..."
        )
        await update.message.reply_text(
            f"📚 **قسم الترجمة**\n{ready}\n\n"
            "اختر نوع الترجمة:\n"
            "• **نص** — ترجمة فورية مع عرض ثنائي اللغة\n"
            "• **ملف** — ترجمة فوق الكلمات (سريع ومحلي)\n"
            "• **صورة** — نفس الترجمة بعد استخراج النص",
            parse_mode="Markdown",
            reply_markup=translation_menu(),
        )
        return ConversationHandler.END

    async def ask_direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_for_translation(update, context):
            return ConversationHandler.END

        text = update.message.text
        if text == "📝 ترجمة نص":
            context.user_data["tr_mode"] = "text"
        elif text == "📁 ترجمة ملف":
            context.user_data["tr_mode"] = "file"
        elif text == "🖼️ ترجمة صورة":
            context.user_data["tr_mode"] = "image"
        else:
            return ConversationHandler.END

        await update.message.reply_text(
            "🌐 اختر اتجاه الترجمة:\n"
            "أو اختر **اكتشاف تلقائي** ليتعرف البوت على اللغة بنفسه.",
            parse_mode="Markdown",
            reply_markup=translation_direction_reply_menu(),
        )
        return states.TR_WAIT_DIRECTION

    async def set_direction_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.text == "🔙 القائمة الرئيسية":
            return await back_to_main(update, context)
        if _is_font_button(update.message.text):
            return await adjust_translation_font(update, context)

        raw = parse_direction_text(update.message.text)
        if not raw:
            await update.message.reply_text(
                "❌ اختر اتجاهاً من الأزرار أدناه.",
                reply_markup=translation_direction_reply_menu(),
            )
            return states.TR_WAIT_DIRECTION

        context.user_data["tr_direction"] = raw
        mode = context.user_data.get("tr_mode", "text")
        dir_label = "🔄 اكتشاف تلقائي" if raw == "auto" else direction_label(raw)

        if mode == "text":
            await update.message.reply_text(
                f"✅ الاتجاه: {dir_label}\n\n"
                "أرسل النص للترجمة (جملة أو فقرة أو أكثر):",
                reply_markup=translation_menu(),
            )
            return states.TR_WAIT_TEXT
        if mode == "image":
            await update.message.reply_text(
                f"✅ الاتجاه: {dir_label}\n\n"
                "أرسل الصورة الآن (JPG / PNG / WEBP):",
                reply_markup=translation_menu(),
            )
            return states.TR_WAIT_IMAGE
        await update.message.reply_text(
            f"✅ الاتجاه: {dir_label}\n\n"
            "أرسل الملف (PDF / TXT / DOCX):",
            reply_markup=translation_menu(),
        )
        return states.TR_WAIT_FILE

    async def set_direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        raw = query.data.replace("tr_dir_", "")
        context.user_data["tr_direction"] = raw
        mode = context.user_data.get("tr_mode", "text")

        if raw == "auto":
            dir_label = "🔄 اكتشاف تلقائي"
        else:
            dir_label = direction_label(raw)

        if mode == "text":
            await query.edit_message_text(
                f"✅ الاتجاه: {dir_label}\n\n"
                "أرسل النص للترجمة (جملة أو فقرة أو أكثر):"
            )
            return states.TR_WAIT_TEXT
        if mode == "image":
            await query.edit_message_text(
                f"✅ الاتجاه: {dir_label}\n\n"
                "أرسل الصورة الآن (JPG / PNG / WEBP):\n"
                "يمكنك إرسالها كصورة أو كملف."
            )
            return states.TR_WAIT_IMAGE
        await query.edit_message_text(
            f"✅ الاتجاه: {dir_label}\n\n"
            "أرسل الملف (PDF / TXT / DOCX):"
        )
        return states.TR_WAIT_FILE

    async def translate_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.text == "🔙 القائمة الرئيسية":
            return await back_to_main(update, context)

        text = (update.message.text or "").strip()
        if _is_font_button(text):
            return await adjust_translation_font(update, context)
        if text in {"📝 ترجمة نص", "📁 ترجمة ملف", "🖼️ ترجمة صورة", "📚 الترجمة"}:
            await update.message.reply_text("📨 أرسل النص المراد ترجمته (مو زر القائمة).")
            return states.TR_WAIT_TEXT
        if len(text) < 2:
            await update.message.reply_text("❌ أرسل نصاً أطول للترجمة.")
            return states.TR_WAIT_TEXT

        direction = context.user_data.get("tr_direction", "auto")
        actual_dir = resolve_direction(text, direction)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        status = await update.message.reply_text("⏳ جاري الترجمة...")

        try:
            interleaved, full_tr = await asyncio.wait_for(
                _run_with_font(context, translate_text_dual, text, actual_dir),
                timeout=90.0,
            )
            header = (
                f"✅ تمت الترجمة ({direction_label(actual_dir)})\n"
                f"📊 {len(text)} حرف → {len(full_tr)} حرف\n\n"
                "📖 العرض الثنائي (أصلي + ترجمة):"
            )
            await update.message.reply_text(header)

            for part in _split_message(interleaved):
                try:
                    await context.bot.send_message(chat_id, part, parse_mode="HTML")
                except Exception:
                    plain = re.sub(r"<[^>]+>", "", part)
                    await context.bot.send_message(chat_id, plain)

            await context.bot.send_message(
                chat_id,
                f"📝 الترجمة الكاملة:\n\n{truncate_text(full_tr, 3500)}",
                reply_markup=translation_menu(),
            )
            if db:
                log_user_activity(
                    db, user_id, "translate_text",
                    f"{direction_label(actual_dir)} — {len(text)} حرف",
                )
        except asyncio.TimeoutError:
            await update.message.reply_text(
                "❌ انتهت مهلة الترجمة — جرّب نصاً أقصر.",
                reply_markup=translation_menu(),
            )
        except Exception as e:
            logger.error(f"Text translation error: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ خطأ في ترجمة النص: {e}",
                reply_markup=translation_menu(),
            )
        finally:
            try:
                await status.delete()
            except Exception:
                pass

        return ConversationHandler.END

    async def _send_translation_outputs(context, chat_id: int, outputs: dict, source_label: str):
        captions = {
            "literal": "1️⃣ حرفي — PDF (كلمة وترجمتها بجانب بعض)",
            "line_pairs": "3️⃣ سطر بسطر — كل سطر وترجمته تحته",
            "overlay": "فوق الكلمات — ترجمة صغيرة فوق كل كلمة",
        }
        send_order = ("literal", "line_pairs", "overlay")
        for key in send_order:
            path = outputs.get(key)
            if not path:
                continue
            with open(path, "rb") as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=path.name,
                    caption=captions.get(key, ""),
                )

        await context.bot.send_message(
            chat_id,
            f"✅ تم إنشاء ترجمة {source_label}!\n\n"
            "فوق الكلمات — ترجمة فوق كل كلمة",
            reply_markup=translation_menu(),
        )

    async def _send_one_output(context, chat_id: int, path: Path, caption: str):
        with open(path, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=path.name,
                caption=caption,
            )

    async def _translate_file_streaming(
        context, chat_id: int, user_id: int, file_path: Path, user_dir: Path,
        direction: str, status_msg_id: int, *, image: bool = False,
    ):
        """ترجمة فوق الكلمات — Argos محلي."""
        captions = {
            "overlay": "فوق كل كلمة — ترجمة صغيرة فوق كل كلمة",
        }
        if image:
            prepare_fn = prepare_full_image_translation
            steps = [
                ("overlay", build_full_image_overlay, "⏳ جاري الترجمة فوق كل كلمة..."),
            ]
        else:
            prepare_fn = prepare_full_file_translation
            steps = [
                ("overlay", build_full_file_overlay, "⏳ جاري الترجمة فوق كل كلمة..."),
            ]
        label = "الصورة" if image else "الملف"

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text="⏳ جاري استخراج النص وتحضير الترجمة...\n"
            "🔒 Argos محلي على Render",
        )
        data = await asyncio.wait_for(
            _run_with_font(context, prepare_fn, file_path, user_dir, direction),
            timeout=file_prepare_timeout(),
        )

        sent = 0
        for key, builder, msg in steps:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg_id, text=msg,
            )
            try:
                path = await asyncio.wait_for(
                    _run_with_font(context, builder, data),
                    timeout=file_overlay_timeout(),
                )
                await _send_one_output(context, chat_id, path, captions[key])
                sent += 1
            except asyncio.TimeoutError:
                logger.warning("Translation step %s timed out for %s", key, file_path.name)
                await context.bot.send_message(
                    chat_id,
                    "⚠️ الترجمة فوق الكلمات استغرقت وقتاً طويلاً.\n"
                    "جرّب ملفاً أصغر أو أعد المحاولة.",
                )
            except Exception as e:
                logger.exception("Translation step %s failed: %s", key, e)
                err = str(e).strip() or e.__class__.__name__
                await context.bot.send_message(
                    chat_id,
                    f"⚠️ فشل إنشاء ملف الترجمة فوق الكلمات.\n{err[:240]}",
                )

        if sent >= 1:
            done_text = (
                f"✅ تم إرسال ترجمة {label}!\n\n"
                "فوق كل كلمة — ترجمة صغيرة فوق كل كلمة"
            )
        else:
            done_text = f"❌ لم يتم إرسال ترجمة {label}."
        await context.bot.send_message(
            chat_id, done_text, reply_markup=translation_menu(),
        )
        if sent >= 1:
            action = "translate_image" if image else "translate_file"
            if db:
                log_user_activity(
                    db, user_id, action,
                    f"{file_path.name} — overlay",
                )
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
        except Exception:
            pass

    async def _translate_media_job(
        context, chat_id: int, user_id: int, media_path: Path,
        user_dir: Path, direction: str, status_msg_id: int,
        *, image: bool = False, activity: str = "translate_file",
    ):
        stop_event = asyncio.Event()
        steps = [
            "⏳ جاري استخراج النص من الصورة..." if image else "⏳ جاري تحليل الملف...",
            "⏳ جاري إنشاء الملف 1 (حرفي PDF)...",
            "⏳ جاري إنشاء الملف 2 (بنفس الترتيب)...",
            "⏳ جاري إنشاء الملف 3 (سطر بسطر)...",
            "⏳ جاري إنشاء الملف 4 (فوق الكلمات)...",
            "⏳ لا يزال جاري الترجمة — الملفات الكاملة تحتاج وقتاً...",
        ]
        job_timeout = 3600.0
        ticker = asyncio.create_task(
            progress_ticker(context.bot, chat_id, status_msg_id, steps, stop_event=stop_event)
        )
        try:
            fn = translate_image_two_modes if image else translate_file_two_modes
            outputs = await asyncio.wait_for(
                _run_with_font(context, fn, media_path, user_dir, direction),
                timeout=job_timeout,
            )
        except Exception:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text="❌ فشلت الترجمة — جاري إرسال التفاصيل...",
                )
            except Exception:
                pass
            raise
        finally:
            stop_event.set()
            ticker.cancel()

        await _send_translation_outputs(
            context, chat_id, outputs,
            "الصورة" if image else "الملف",
        )

        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
        except Exception:
            pass

    async def _translate_file_job(
        context, chat_id: int, user_id: int, file_path: Path,
        user_dir: Path, direction: str, status_msg_id: int,
    ):
        await _translate_file_streaming(
            context, chat_id, user_id, file_path, user_dir, direction, status_msg_id,
        )

    async def _translate_image_job(
        context, chat_id: int, user_id: int, image_path: Path,
        user_dir: Path, direction: str, status_msg_id: int,
    ):
        await _translate_file_streaming(
            context, chat_id, user_id, image_path, user_dir, direction, status_msg_id,
            image=True,
        )

    async def wait_direction_media_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
        mode = context.user_data.get("tr_mode", "text")
        label = {"file": "الملف", "image": "الصورة"}.get(mode, "المرفق")
        await update.message.reply_text(
            f"📨 تم استلام {label}.\n"
            "🌐 اختر اتجاه الترجمة أولاً من الأزرار أدناه:",
            reply_markup=translation_direction_reply_menu(),
        )
        return states.TR_WAIT_DIRECTION

    async def translate_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.text == "🔙 القائمة الرئيسية":
            return await back_to_main(update, context)
        if _is_font_button(update.message.text):
            return await adjust_translation_font(update, context)

        doc = update.message.document
        if not doc:
            await update.message.reply_text("❌ أرسل ملفاً صالحاً (PDF / TXT / DOCX).")
            return states.TR_WAIT_FILE

        safe_name = doc.file_name or f"upload_{doc.file_unique_id}.txt"
        suffix = Path(safe_name).suffix.lower()
        if suffix not in (".pdf", ".txt", ".docx", ".doc"):
            await update.message.reply_text("❌ الملفات المدعومة: PDF, TXT, DOCX")
            return states.TR_WAIT_FILE

        if doc.file_size and doc.file_size > 50 * 1024 * 1024:
            await update.message.reply_text("❌ الملف أكبر من 50 MB. أرسل ملفاً أصغر.")
            return states.TR_WAIT_FILE

        status = await update.message.reply_text(
            "⏳ جاري تحميل الملف...\n"
            "📌 الملفات الكبيرة تُترجم في الخلفية — يمكنك استخدام البوت بحرية."
        )
        user_dir = get_user_temp_dir(update.effective_user.id)
        file_path = user_dir / safe_name
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        direction = context.user_data.get("tr_direction", "auto")

        try:
            tg_file = await doc.get_file()
            await _download_tg_file(status, tg_file, str(file_path))

            await status.edit_text("📄 تم تحميل الملف.")
            context.user_data["tr_pending"] = {
                "path": str(file_path),
                "user_dir": str(user_dir),
                "image": False,
                "status_id": status.message_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "direction": direction,
            }
            await update.message.reply_text(
                "🎨 اختر لون الترجمة قبل الإنشاء:",
                reply_markup=translation_color_keyboard(),
            )
            return states.TR_WAIT_COLOR
        except (TimedOut, NetworkError):
            logger.error("File download timed out")
            await status.edit_text(
                "❌ انتهت مهلة تحميل الملف.\n"
                "حاول مرة أخرى بملف أصغر أو اتصال أسرع."
            )
            return states.TR_WAIT_FILE
        except Exception as e:
            logger.error(f"File translation setup error: {e}", exc_info=True)
            await status.edit_text(f"❌ خطأ: {e}")
            return states.TR_WAIT_FILE

        return ConversationHandler.END

    async def translate_image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.text == "🔙 القائمة الرئيسية":
            return await back_to_main(update, context)
        if _is_font_button(update.message.text):
            return await adjust_translation_font(update, context)

        user_dir = get_user_temp_dir(update.effective_user.id)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        direction = context.user_data.get("tr_direction", "auto")
        image_path: Path | None = None

        if update.message.photo:
            photo = update.message.photo[-1]
            image_path = user_dir / f"tr_img_{photo.file_id}.jpg"
            status = await update.message.reply_text(
                "⏳ جاري تحميل الصورة...\n"
                "📌 تُترجم في الخلفية — يمكنك استخدام البوت بحرية."
            )
            tg_file = await photo.get_file()
            await _download_tg_file(status, tg_file, str(image_path))
        elif update.message.document:
            doc = update.message.document
            suffix = Path(doc.file_name or "").suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                await update.message.reply_text("❌ الصيغ المدعومة: JPG, PNG, WEBP, BMP, TIFF")
                return states.TR_WAIT_IMAGE
            if doc.file_size and doc.file_size > 20 * 1024 * 1024:
                await update.message.reply_text("❌ الصورة أكبر من 20 MB.")
                return states.TR_WAIT_IMAGE
            image_path = user_dir / doc.file_name
            status = await update.message.reply_text("⏳ جاري تحميل الصورة...")
            tg_file = await doc.get_file()
            await _download_tg_file(status, tg_file, str(image_path))
        else:
            await update.message.reply_text(
                "❌ أرسل صورة (JPG / PNG) أو ارفعها كملف.",
                reply_markup=translation_menu(),
            )
            return states.TR_WAIT_IMAGE

        await status.edit_text("🖼️ تم تحميل الصورة.")
        context.user_data["tr_pending"] = {
            "path": str(image_path),
            "user_dir": str(user_dir),
            "image": True,
            "status_id": status.message_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "direction": direction,
        }
        await update.message.reply_text(
            "🎨 اختر لون الترجمة قبل الإنشاء:",
            reply_markup=translation_color_keyboard(),
        )
        return states.TR_WAIT_COLOR

    async def receive_translation_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        name = (query.data or "").removeprefix("tr_color_")
        if name not in TRANSLATION_COLORS:
            return states.TR_WAIT_COLOR
        pending = context.user_data.get("tr_pending")
        if not pending:
            await query.edit_message_text("❌ لا يوجد ملف للترجمة. أرسل الملف أو الصورة من جديد.")
            return ConversationHandler.END

        context.user_data["tr_color"] = name
        label = TRANSLATION_COLOR_LABELS.get(name, name)
        try:
            await query.edit_message_text(f"✅ لون الترجمة: {label}\n⏳ جاري الترجمة...")
        except Exception:
            pass

        file_path = Path(pending["path"])
        user_dir = Path(pending["user_dir"])
        chat_id = pending["chat_id"]
        user_id = pending["user_id"]
        direction = pending["direction"]
        status_id = pending["status_id"]
        is_image = bool(pending.get("image"))
        context.user_data.pop("tr_pending", None)

        async def _job():
            try:
                if is_image:
                    await _translate_image_job(
                        context, chat_id, user_id, file_path,
                        user_dir, direction, status_id,
                    )
                else:
                    await _translate_file_job(
                        context, chat_id, user_id, file_path,
                        user_dir, direction, status_id,
                    )
            except asyncio.TimeoutError:
                await context.bot.send_message(
                    chat_id,
                    "❌ انتهت مهلة ترجمة الصورة (15 دقيقة).\nجرّب صورة أصغر."
                    if is_image else
                    "❌ انتهت مهلة ترجمة الملف.\nجرّب ملفاً أصغر أو TXT.",
                    reply_markup=translation_menu(),
                )
            except (TimedOut, NetworkError):
                await context.bot.send_message(
                    chat_id,
                    "❌ انتهت مهلة تحميل/معالجة الملف.\n"
                    "حاول مرة أخرى بملف أصغر أو اتصال أسرع.",
                    reply_markup=translation_menu(),
                )
            except RuntimeError as e:
                await context.bot.send_message(
                    chat_id, f"❌ {e}", reply_markup=translation_menu(),
                )
            except Exception as e:
                logger.error("Translation after color failed: %s", e, exc_info=True)
                await context.bot.send_message(
                    chat_id,
                    f"❌ خطأ في الترجمة: {e}",
                    reply_markup=translation_menu(),
                )

        spawn_background(
            _job(),
            label=f"translate_{'image' if is_image else 'file'}:{user_id}",
        )
        return ConversationHandler.END

    async def remind_pick_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message and _is_font_button(update.message.text):
            return await adjust_translation_font(update, context)
        await update.message.reply_text(
            "🎨 اختر لون الترجمة من الأزرار أولاً:",
            reply_markup=translation_color_keyboard(),
        )
        return states.TR_WAIT_COLOR

    font_handler = MessageHandler(
        filters.Regex("^🔠 صغير$|^🔠 متوسط$|^🔠 كبير$|^➖ تصغير الخط$|^➕ تكبير الخط$"),
        adjust_translation_font,
    )

    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📚 الترجمة$"), enter_translation),
            MessageHandler(
                filters.Regex("^📝 ترجمة نص$|^📁 ترجمة ملف$|^🖼️ ترجمة صورة$"),
                ask_direction,
            ),
            font_handler,
        ],
        states={
            states.TR_WAIT_DIRECTION: [
                font_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_direction_message),
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    wait_direction_media_hint,
                ),
                CallbackQueryHandler(set_direction, pattern="^tr_dir_"),
            ],
            states.TR_WAIT_TEXT: [
                font_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, translate_text_handler),
            ],
            states.TR_WAIT_FILE: [
                font_handler,
                MessageHandler(filters.Document.ALL, translate_file_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, translate_file_handler),
            ],
            states.TR_WAIT_IMAGE: [
                font_handler,
                MessageHandler(filters.PHOTO | filters.Document.ALL, translate_image_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, translate_image_handler),
            ],
            states.TR_WAIT_COLOR: [
                font_handler,
                CallbackQueryHandler(receive_translation_color, pattern="^tr_color_(black|red|blue|green)$"),
                MessageHandler(
                    filters.PHOTO | filters.Document.ALL | (filters.TEXT & ~filters.COMMAND),
                    remind_pick_color,
                ),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🔙 القائمة الرئيسية$"), back_to_main),
            MessageHandler(filters.Regex("^❌ إلغاء$"), back_to_main),
        ],
        allow_reentry=True,
    )
