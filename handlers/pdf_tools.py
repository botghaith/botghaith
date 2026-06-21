import asyncio
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

from database.db import Database
from services.channel_check import check_channel_subscription
from services.ocr_service import EXTRACT_TIMEOUT, extract_text_smart
from services.pdf_service import (
    images_to_pdf, pdf_to_images, word_to_pdf, pdf_to_word,
    merge_pdfs, split_pdf, compress_pdf, reorder_pdf_pages, create_text_pdf,
)
from utils.helpers import get_user_temp_dir, split_text_chunks, sanitize_text_for_send
from utils.background_jobs import spawn_background
from utils.keyboards import pdf_menu
from utils import states

logger = logging.getLogger(__name__)

BG_HINT = "\n💡 يمكنك استخدام باقي الخدمات — سيصلك الناتج عند الانتهاء."


def setup_pdf_handlers(db: Database, back_to_main) -> ConversationHandler:
    async def enter_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_subscription(update, context, db):
            return ConversationHandler.END
        context.user_data["pdf_files"] = []
        await update.message.reply_text(
            "📄 **أدوات PDF والملفات**\nاختر الأداة:",
            parse_mode="Markdown",
            reply_markup=pdf_menu(),
        )
        return ConversationHandler.END

    async def select_tool(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_subscription(update, context, db):
            return ConversationHandler.END

        tool_map = {
            "🖼️ صور → PDF": (states.PDF_IMG_TO_PDF, "أرسل الصور (يمكن إرسال عدة صور ثم اكتب «تم»)"),
            "📷 PDF → صور": (states.PDF_TO_IMG, "أرسل ملف PDF"),
            "📄 Word → PDF": (states.PDF_WORD_TO_PDF, "أرسل ملف Word (.docx)"),
            "📝 PDF → Word": (states.PDF_TO_WORD, "أرسل ملف PDF"),
            "🔗 دمج PDF": (states.PDF_MERGE, "أرسل ملفات PDF للدمج (عدة ملفات ثم اكتب «تم»)"),
            "✂️ تقسيم PDF": (states.PDF_SPLIT, "أرسل ملف PDF للتقسيم"),
            "🗜️ ضغط PDF": (states.PDF_COMPRESS, "أرسل ملف PDF للضغط"),
            "📖 استخراج نص": (states.PDF_EXTRACT, "أرسل صورة أو PDF — يُرسل النص + ملف PDF بنفس الترتيب"),
            "🔄 إعادة ترتيب": (states.PDF_REORDER, "أرسل ملف PDF ثم أرسل ترتيب الصفحات (مثال: 3,1,2)"),
        }

        text = update.message.text
        if text not in tool_map:
            return ConversationHandler.END

        state, msg = tool_map[text]
        context.user_data["pdf_tool"] = text
        context.user_data["pdf_files"] = []
        await update.message.reply_text(f"📄 {msg}")
        return state

    async def _download_doc(update, user_dir) -> Path | None:
        doc = update.message.document
        if not doc:
            return None
        path = user_dir / doc.file_name
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(str(path))
        return path

    async def handle_images_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)
        text = update.message.text or ""

        if text == "تم" or text == "🔙 القائمة الرئيسية":
            files = context.user_data.get("pdf_files", [])
            if not files:
                if text == "🔙 القائمة الرئيسية":
                    return await back_to_main(update, context)
                await update.message.reply_text("❌ لم تُرسل صور بعد.")
                return states.PDF_IMG_TO_PDF

            status = await update.message.reply_text("⏳ جاري التحويل في الخلفية..." + BG_HINT)
            chat_id = update.effective_chat.id
            user_id = update.effective_user.id
            file_list = list(files)

            async def _job():
                try:
                    out = user_dir / "images_combined.pdf"
                    await asyncio.to_thread(images_to_pdf, [Path(f) for f in file_list], out)
                    with open(out, "rb") as f:
                        await context.bot.send_document(
                            chat_id, document=f, filename="combined.pdf", caption="✅ PDF جاهز"
                        )
                    await context.bot.send_message(chat_id, "✅ تم!", reply_markup=pdf_menu())
                    db.log_activity(user_id, "pdf_img_to_pdf")
                except Exception as e:
                    logger.error(f"images_to_pdf error: {e}", exc_info=True)
                    await context.bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=pdf_menu())
                finally:
                    try:
                        await status.delete()
                    except Exception:
                        pass

            spawn_background(_job(), label=f"pdf_img:{user_id}")
            context.user_data["pdf_files"] = []
            return ConversationHandler.END

        if update.message.photo:
            photo = update.message.photo[-1]
            path = user_dir / f"img_{photo.file_id}.jpg"
            tg_file = await photo.get_file()
            await tg_file.download_to_drive(str(path))
            context.user_data.setdefault("pdf_files", []).append(str(path))
            count = len(context.user_data["pdf_files"])
            await update.message.reply_text(f"✅ صورة {count} — أرسل المزيد أو اكتب «تم»")
            return states.PDF_IMG_TO_PDF

        return states.PDF_IMG_TO_PDF

    async def handle_pdf_to_img(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)
        path = await _download_doc(update, user_dir)
        if not path:
            return states.PDF_TO_IMG

        status = await update.message.reply_text("⏳ جاري التحويل في الخلفية..." + BG_HINT)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        async def _job():
            try:
                images = await asyncio.to_thread(pdf_to_images, path, user_dir)
                for img in images:
                    with open(img, "rb") as f:
                        await context.bot.send_document(chat_id, document=f, filename=img.name)
                await context.bot.send_message(
                    chat_id, f"✅ تم تحويل {len(images)} صفحة!", reply_markup=pdf_menu()
                )
                db.log_activity(user_id, "pdf_to_img")
            except Exception as e:
                logger.error(f"pdf_to_img error: {e}", exc_info=True)
                await context.bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=pdf_menu())
            finally:
                try:
                    await status.delete()
                except Exception:
                    pass

        spawn_background(_job(), label=f"pdf_to_img:{user_id}")
        return ConversationHandler.END

    async def handle_word_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)
        path = await _download_doc(update, user_dir)
        if not path:
            return states.PDF_WORD_TO_PDF

        status = await update.message.reply_text("⏳ جاري التحويل في الخلفية..." + BG_HINT)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        out = user_dir / f"{path.stem}.pdf"

        async def _job():
            try:
                await asyncio.to_thread(word_to_pdf, path, out)
                with open(out, "rb") as f:
                    await context.bot.send_document(
                        chat_id, document=f, filename=out.name, caption="✅ Word → PDF"
                    )
                await context.bot.send_message(chat_id, "✅ تم!", reply_markup=pdf_menu())
                db.log_activity(user_id, "word_to_pdf")
            except Exception as e:
                logger.error(f"word_to_pdf error: {e}", exc_info=True)
                await context.bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=pdf_menu())
            finally:
                try:
                    await status.delete()
                except Exception:
                    pass

        spawn_background(_job(), label=f"word_to_pdf:{user_id}")
        return ConversationHandler.END

    async def handle_pdf_to_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)
        path = await _download_doc(update, user_dir)
        if not path:
            return states.PDF_TO_WORD

        status = await update.message.reply_text("⏳ جاري التحويل في الخلفية..." + BG_HINT)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        out = user_dir / f"{path.stem}.docx"

        async def _job():
            try:
                await asyncio.to_thread(pdf_to_word, path, out)
                with open(out, "rb") as f:
                    await context.bot.send_document(
                        chat_id, document=f, filename=out.name, caption="✅ PDF → Word"
                    )
                await context.bot.send_message(chat_id, "✅ تم!", reply_markup=pdf_menu())
                db.log_activity(user_id, "pdf_to_word")
            except Exception as e:
                logger.error(f"pdf_to_word error: {e}", exc_info=True)
                await context.bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=pdf_menu())
            finally:
                try:
                    await status.delete()
                except Exception:
                    pass

        spawn_background(_job(), label=f"pdf_to_word:{user_id}")
        return ConversationHandler.END

    async def handle_merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)
        text = update.message.text or ""

        if text in ("تم", "🔙 القائمة الرئيسية"):
            files = context.user_data.get("pdf_files", [])
            if len(files) < 2:
                if text == "🔙 القائمة الرئيسية":
                    return await back_to_main(update, context)
                await update.message.reply_text("❌ أرسل ملفين PDF على الأقل ثم اكتب «تم»")
                return states.PDF_MERGE

            status = await update.message.reply_text("⏳ جاري الدمج في الخلفية..." + BG_HINT)
            chat_id = update.effective_chat.id
            user_id = update.effective_user.id
            file_list = list(files)

            async def _job():
                try:
                    out = user_dir / "merged.pdf"
                    await asyncio.to_thread(merge_pdfs, [Path(f) for f in file_list], out)
                    with open(out, "rb") as f:
                        await context.bot.send_document(
                            chat_id, document=f, filename="merged.pdf", caption="✅ PDF مدمج"
                        )
                    await context.bot.send_message(chat_id, "✅ تم!", reply_markup=pdf_menu())
                    db.log_activity(user_id, "pdf_merge")
                except Exception as e:
                    logger.error(f"pdf_merge error: {e}", exc_info=True)
                    await context.bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=pdf_menu())
                finally:
                    try:
                        await status.delete()
                    except Exception:
                        pass

            spawn_background(_job(), label=f"pdf_merge:{user_id}")
            context.user_data["pdf_files"] = []
            return ConversationHandler.END

        path = await _download_doc(update, user_dir)
        if path:
            context.user_data.setdefault("pdf_files", []).append(str(path))
            count = len(context.user_data["pdf_files"])
            await update.message.reply_text(f"✅ ملف {count} — أرسل المزيد أو اكتب «تم»")
        return states.PDF_MERGE

    async def handle_split(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)
        path = await _download_doc(update, user_dir)
        if not path:
            return states.PDF_SPLIT

        status = await update.message.reply_text("⏳ جاري التقسيم في الخلفية..." + BG_HINT)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        async def _job():
            try:
                parts = await asyncio.to_thread(split_pdf, path, user_dir)
                for p in parts:
                    with open(p, "rb") as f:
                        await context.bot.send_document(chat_id, document=f, filename=p.name)
                await context.bot.send_message(
                    chat_id, f"✅ تم تقسيمه إلى {len(parts)} ملف!", reply_markup=pdf_menu()
                )
                db.log_activity(user_id, "pdf_split")
            except Exception as e:
                logger.error(f"pdf_split error: {e}", exc_info=True)
                await context.bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=pdf_menu())
            finally:
                try:
                    await status.delete()
                except Exception:
                    pass

        spawn_background(_job(), label=f"pdf_split:{user_id}")
        return ConversationHandler.END

    async def handle_compress(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)
        path = await _download_doc(update, user_dir)
        if not path:
            return states.PDF_COMPRESS

        status = await update.message.reply_text("⏳ جاري الضغط في الخلفية..." + BG_HINT)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        out = user_dir / f"compressed_{path.name}"
        orig_size = path.stat().st_size / 1024

        async def _job():
            try:
                await asyncio.to_thread(compress_pdf, path, out)
                new_size = out.stat().st_size / 1024
                with open(out, "rb") as f:
                    await context.bot.send_document(
                        chat_id,
                        document=f,
                        filename=out.name,
                        caption=f"✅ {orig_size:.0f}KB → {new_size:.0f}KB",
                    )
                await context.bot.send_message(chat_id, "✅ تم!", reply_markup=pdf_menu())
                db.log_activity(user_id, "pdf_compress")
            except Exception as e:
                logger.error(f"pdf_compress error: {e}", exc_info=True)
                await context.bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=pdf_menu())
            finally:
                try:
                    await status.delete()
                except Exception:
                    pass

        spawn_background(_job(), label=f"pdf_compress:{user_id}")
        return ConversationHandler.END

    async def _download_extract_input(update: Update, user_dir: Path) -> Path | None:
        if update.message.photo:
            photo = update.message.photo[-1]
            path = user_dir / f"ocr_{photo.file_id}.jpg"
            tg_file = await photo.get_file()
            await tg_file.download_to_drive(str(path))
            return path

        doc = update.message.document
        if not doc:
            return None

        name = doc.file_name or "file"
        ext = Path(name).suffix.lower()
        allowed = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
        if ext not in allowed:
            await update.message.reply_text("❌ المدعوم: PDF أو صورة (JPG, PNG, WEBP...)")
            return None

        path = user_dir / name
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(str(path))
        return path

    async def handle_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)
        status = await update.message.reply_text("⏳ جاري تحميل الملف..." + BG_HINT)

        try:
            path = await _download_extract_input(update, user_dir)
            if not path:
                await status.edit_text("❌ أرسل صورة أو ملف PDF.")
                return states.PDF_EXTRACT
        except Exception as e:
            await status.edit_text(f"❌ {e}")
            return states.PDF_EXTRACT

        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        await status.edit_text("⏳ جاري استخراج النص في الخلفية..." + BG_HINT)

        async def _job():
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(extract_text_smart, path, user_dir),
                    timeout=EXTRACT_TIMEOUT,
                )
                if not text.strip():
                    await context.bot.send_message(chat_id, "❌ لم يتم العثور على نص في الملف.")
                    return

                text = sanitize_text_for_send(text)
                if not text:
                    await context.bot.send_message(chat_id, "❌ لم يتم العثور على نص صالح بعد التنظيف.")
                    return

                txt_file = user_dir / f"extracted_{path.stem}.txt"
                pdf_file = user_dir / f"extracted_{path.stem}.pdf"
                txt_file.write_text(text, encoding="utf-8-sig")
                await asyncio.to_thread(create_text_pdf, text, pdf_file, "النص المستخرج")

                chunks = split_text_chunks(text, 4000)
                for i, chunk in enumerate(chunks):
                    header = (
                        f"📖 النص المستخرج ({i + 1}/{len(chunks)}):\n\n"
                        if len(chunks) > 1
                        else "📖 النص المستخرج:\n\n"
                    )
                    try:
                        await context.bot.send_message(chat_id, f"{header}{chunk}")
                    except Exception as e:
                        logger.warning(f"Could not send text chunk {i + 1}: {e}")
                        if i == 0:
                            await context.bot.send_message(
                                chat_id,
                                "📖 النص طويل — راجع الملفات المرفقة (كاملة بدون اقتصاص).",
                            )
                        break

                with open(pdf_file, "rb") as f:
                    await context.bot.send_document(
                        chat_id, document=f, filename=pdf_file.name,
                        caption="📄 PDF كامل — بدون اقتصاص",
                    )
                with open(txt_file, "rb") as f:
                    await context.bot.send_document(
                        chat_id, document=f, filename=txt_file.name,
                        caption="📝 TXT كامل — بدون اقتصاص",
                    )
                await context.bot.send_message(chat_id, "✅ تم!", reply_markup=pdf_menu())
                db.log_activity(user_id, "pdf_extract")
            except asyncio.TimeoutError:
                await context.bot.send_message(
                    chat_id,
                    "❌ استغرق الاستخراج وقتاً طويلاً.\nحاول بصورة أوضح أو ملف أصغر.",
                    reply_markup=pdf_menu(),
                )
            except RuntimeError as e:
                await context.bot.send_message(chat_id, f"❌ {e}", reply_markup=pdf_menu())
            except Exception as e:
                logger.error(f"Text extraction error: {e}", exc_info=True)
                await context.bot.send_message(chat_id, f"❌ خطأ في الاستخراج: {e}", reply_markup=pdf_menu())
            finally:
                try:
                    await status.delete()
                except Exception:
                    pass

        spawn_background(_job(), label=f"pdf_extract:{user_id}")
        return ConversationHandler.END

    async def handle_reorder(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_dir = get_user_temp_dir(update.effective_user.id)

        if "reorder_pdf" not in context.user_data:
            path = await _download_doc(update, user_dir)
            if not path:
                return states.PDF_REORDER
            context.user_data["reorder_pdf"] = str(path)
            await update.message.reply_text(
                "✅ تم استلام PDF.\nأرسل ترتيب الصفحات (مثال: 3,1,2,4)\n"
                "ملاحظة: الأرقام تبدأ من 1"
            )
            return states.PDF_REORDER

        text = update.message.text or ""
        try:
            order = [int(x.strip()) - 1 for x in text.split(",")]
            pdf_path = Path(context.user_data["reorder_pdf"])
            out = user_dir / f"reordered_{pdf_path.name}"
            status = await update.message.reply_text("⏳ جاري إعادة الترتيب في الخلفية..." + BG_HINT)
            chat_id = update.effective_chat.id
            user_id = update.effective_user.id

            async def _job():
                try:
                    await asyncio.to_thread(reorder_pdf_pages, pdf_path, order, out)
                    with open(out, "rb") as f:
                        await context.bot.send_document(
                            chat_id, document=f, filename=out.name, caption="✅ PDF معاد الترتيب"
                        )
                    await context.bot.send_message(chat_id, "✅ تم!", reply_markup=pdf_menu())
                    db.log_activity(user_id, "pdf_reorder")
                except Exception as e:
                    await context.bot.send_message(chat_id, f"❌ خطأ في الترتيب: {e}", reply_markup=pdf_menu())
                finally:
                    try:
                        await status.delete()
                    except Exception:
                        pass

            spawn_background(_job(), label=f"pdf_reorder:{user_id}")
            del context.user_data["reorder_pdf"]
            return ConversationHandler.END
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ في الترتيب: {e}\nحاول مرة أخرى (مثال: 2,1,3)")
            return states.PDF_REORDER

    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📄 أدوات PDF$"), enter_pdf),
            MessageHandler(
                filters.Regex("^🖼️ صور → PDF$|^📷 PDF → صور$|^📄 Word → PDF$|^📝 PDF → Word$|^🔗 دمج PDF$|^✂️ تقسيم PDF$|^🗜️ ضغط PDF$|^📖 استخراج نص$|^🔄 إعادة ترتيب$"),
                select_tool,
            ),
        ],
        states={
            states.PDF_IMG_TO_PDF: [
                MessageHandler(filters.PHOTO | filters.TEXT, handle_images_to_pdf),
            ],
            states.PDF_TO_IMG: [
                MessageHandler(filters.Document.ALL, handle_pdf_to_img),
            ],
            states.PDF_WORD_TO_PDF: [
                MessageHandler(filters.Document.ALL, handle_word_to_pdf),
            ],
            states.PDF_TO_WORD: [
                MessageHandler(filters.Document.ALL, handle_pdf_to_word),
            ],
            states.PDF_MERGE: [
                MessageHandler(filters.Document.ALL | filters.TEXT, handle_merge),
            ],
            states.PDF_SPLIT: [
                MessageHandler(filters.Document.ALL, handle_split),
            ],
            states.PDF_COMPRESS: [
                MessageHandler(filters.Document.ALL, handle_compress),
            ],
            states.PDF_EXTRACT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_extract),
            ],
            states.PDF_REORDER: [
                MessageHandler(filters.Document.ALL | filters.TEXT, handle_reorder),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🔙 القائمة الرئيسية$"), back_to_main),
        ],
        allow_reentry=True,
    )
