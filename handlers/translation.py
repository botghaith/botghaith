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
from services.file_translator import translate_file_two_modes, translate_image_two_modes
from config import use_online_translate
from services.translator import (
    translate_text_dual,
    resolve_direction,
    direction_label,
    is_translator_ready,
)
from utils.helpers import get_user_temp_dir, truncate_text
from utils.background_jobs import spawn_background, progress_ticker
from utils.keyboards import (
    translation_menu,
    translation_direction_reply_menu,
    parse_direction_text,
    MAIN_MENU,
)
from utils import states

logger = logging.getLogger(__name__)

MSG_LIMIT = 3800
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


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


def setup_translation_handlers(back_to_main) -> ConversationHandler:
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
            "• **ملف** — 4 ملفات: حرفي PDF / بنفس الترتيب / سطر بسطر / فوق الكلمات\n"
            "• **صورة** — نفس 4 صيغ بعد استخراج النص بـ OCR",
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
                asyncio.to_thread(translate_text_dual, text, actual_dir),
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
            "structured": "2️⃣ نفس ترتيب الصورة/الملف — مترجم بالكامل",
            "line_pairs": "3️⃣ سطر بسطر — كل سطر وترجمته تحته",
            "overlay": "4️⃣ فوق الكلمات — ترجمة صغيرة فوق كل كلمة",
        }
        send_order = ("literal", "structured", "line_pairs", "overlay")
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
            f"✅ تم إنشاء ملفات ترجمة {source_label}!\n\n"
            "1️⃣ حرفي — PDF\n"
            "2️⃣ بنفس الترتيب — مترجم بالكامل\n"
            "3️⃣ سطر بسطر — أصل وترجمة\n"
            "4️⃣ فوق الكلمات — ترجمة فوق كل كلمة",
            reply_markup=translation_menu(),
        )

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
                asyncio.to_thread(fn, media_path, user_dir, direction),
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
        await _translate_media_job(
            context, chat_id, user_id, file_path, user_dir, direction, status_msg_id,
            image=False, activity="translate_file",
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

        doc = update.message.document
        if not doc:
            await update.message.reply_text("❌ أرسل ملفاً صالحاً (PDF / TXT / DOCX).")
            return states.TR_WAIT_FILE

        suffix = Path(doc.file_name).suffix.lower()
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
        file_path = user_dir / doc.file_name
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        direction = context.user_data.get("tr_direction", "auto")

        try:
            tg_file = await doc.get_file()
            for attempt in range(5):
                try:
                    await tg_file.download_to_drive(str(file_path))
                    break
                except (TimedOut, NetworkError):
                    if attempt == 4:
                        raise
                    await status.edit_text(f"⏳ إعادة تحميل الملف... ({attempt + 2}/5)")
                    await asyncio.sleep(5)

            await status.edit_text(
                "📄 تم التحميل — جاري الترجمة في الخلفية...\n"
                "💡 يمكنك الانتقال للقائمة الرئيسية أو استخدام خدمة أخرى."
            )

            async def _job():
                try:
                    await _translate_file_job(
                        context, chat_id, user_id, file_path,
                        user_dir, direction, status.message_id,
                    )
                except asyncio.TimeoutError:
                    await context.bot.send_message(
                        chat_id,
                        "❌ انتهت مهلة ترجمة الملف (60 دقيقة).\n"
                        "جرّب ملفاً أصغر.",
                        reply_markup=translation_menu(),
                    )
                except (TimedOut, NetworkError):
                    await context.bot.send_message(
                        chat_id,
                        "❌ انتهت مهلة تحميل/معالجة الملف.\n"
                        "حاول مرة أخرى بملف أصغر أو اتصال أسرع.",
                        reply_markup=translation_menu(),
                    )
                except Exception as e:
                    logger.error(f"File translation error: {e}", exc_info=True)
                    await context.bot.send_message(
                        chat_id,
                        f"❌ خطأ في الترجمة: {e}",
                        reply_markup=translation_menu(),
                    )

            spawn_background(_job(), label=f"translate_file:{user_id}")
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
                "📌 تُترجم في الخلفية بـ 4 صيغ — يمكنك استخدام البوت بحرية."
            )
            tg_file = await photo.get_file()
            await tg_file.download_to_drive(str(image_path))
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
            await tg_file.download_to_drive(str(image_path))
        else:
            await update.message.reply_text(
                "❌ أرسل صورة (JPG / PNG) أو ارفعها كملف.",
                reply_markup=translation_menu(),
            )
            return states.TR_WAIT_IMAGE

        await status.edit_text(
            "🖼️ تم التحميل — جاري استخراج النص والترجمة في الخلفية...\n"
            "💡 يمكنك الانتقال للقائمة الرئيسية أو استخدام خدمة أخرى."
        )

        async def _job():
            try:
                await _translate_media_job(
                    context, chat_id, user_id, image_path,
                    user_dir, direction, status.message_id,
                    image=True, activity="translate_image",
                )
            except asyncio.TimeoutError:
                await context.bot.send_message(
                    chat_id,
                    "❌ انتهت مهلة ترجمة الصورة (60 دقيقة).\n"
                    "جرّب صورة أصغر.",
                    reply_markup=translation_menu(),
                )
            except RuntimeError as e:
                await context.bot.send_message(
                    chat_id,
                    f"❌ {e}",
                    reply_markup=translation_menu(),
                )
            except Exception as e:
                logger.error(f"Image translation error: {e}", exc_info=True)
                await context.bot.send_message(
                    chat_id,
                    f"❌ خطأ في ترجمة الصورة: {e}",
                    reply_markup=translation_menu(),
                )

        spawn_background(_job(), label=f"translate_image:{user_id}")
        return ConversationHandler.END

    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📚 الترجمة$"), enter_translation),
            MessageHandler(
                filters.Regex("^📝 ترجمة نص$|^📁 ترجمة ملف$|^🖼️ ترجمة صورة$"),
                ask_direction,
            ),
        ],
        states={
            states.TR_WAIT_DIRECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_direction_message),
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    wait_direction_media_hint,
                ),
                CallbackQueryHandler(set_direction, pattern="^tr_dir_"),
            ],
            states.TR_WAIT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, translate_text_handler),
            ],
            states.TR_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, translate_file_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, translate_file_handler),
            ],
            states.TR_WAIT_IMAGE: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, translate_image_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, translate_image_handler),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🔙 القائمة الرئيسية$"), back_to_main),
            MessageHandler(filters.Regex("^❌ إلغاء$"), back_to_main),
        ],
        allow_reentry=True,
    )
