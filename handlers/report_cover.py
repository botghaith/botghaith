"""مولّد واجهة التقرير — غلاف أكاديمي PDF / Word."""
import html
import logging
import re
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from services.channel_check import check_channel_subscription
from services.report_cover_service import (
    ALL_TEMPLATES,
    ReportCoverData,
    TEMPLATE_NAMES,
    generate_cover_docx,
    generate_cover_pdf,
)
from utils.activity_log import log_user_activity
from utils.helpers import get_user_temp_dir
from utils.keyboards import MAIN_MENU, cancel_keyboard
from utils import states

logger = logging.getLogger(__name__)

RC = states

CANCEL_TEXTS = frozenset({
    "❌ إلغاء", "إلغاء", "Cancel", "cancel",
    "🔙 القائمة الرئيسية", "🏠 القائمة الرئيسية",
})


def _is_cancel_text(text: str) -> bool:
    return (text or "").strip() in CANCEL_TEXTS


def _cancel_handler(cancel_flow):
    return MessageHandler(filters.Text(list(CANCEL_TEXTS)), cancel_flow)


LANGUAGE_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🇸🇦 العربية", callback_data="rc_lang_ar"),
        InlineKeyboardButton("🇬🇧 English", callback_data="rc_lang_en"),
    ],
])
DATE_KEYBOARD = InlineKeyboardMarkup.from_row([
    InlineKeyboardButton("📅 تاريخ اليوم", callback_data="rc_date_today"),
])
LOGO_KEYBOARD = ReplyKeyboardMarkup(
    [["⏭️ بدون شعار", "⏭️ No logo"], ["❌ إلغاء"]],
    resize_keyboard=True,
)

PROMPTS = {
    "ar": {
        "start": (
            "📋 **مولّد واجهة التقرير**\n\n"
            "اختر **لغة الغلاف** أولاً:\n"
            "🇸🇦 العربية  |  🇬🇧 English"
        ),
        "details": (
            "📋 أرسل <b>كل البيانات برسالة واحدة</b>\n"
            "كل معلومة في <b>سطر مستقل</b> وبنفس الترتيب:\n\n"
            "1. الاسم الكامل — إذا أكثر من طالب اكتبهم في <b>نفس السطر</b> وافصل بـ <code>،</code>\n"
            "2. الجامعة\n"
            "3. القسم / التخصص\n"
            "4. المادة\n"
            "5. عنوان التقرير\n"
            "6. اسم الدكتور المشرف\n"
            "7. التاريخ — اكتب <b>اليوم</b> أو مثل <code>13/09/2026</code>\n\n"
            "مثال:\n"
            "<pre>"
            "غيث اسعد\n"
            "جامعة الأنبار\n"
            "كلية الهندسة /القسم المدني\n"
            "تربة\n"
            "فحوصات التربة\n"
            "د. محمد علي\n"
            "اليوم"
            "</pre>\n\n"
            "عدة طلاب في سطر الاسم مثل: <code>غيث اسعد، أحمد علي، محمد حسن</code>"
        ),
        "err_batch": (
            "❌ ما اكتملت البيانات.\n"
            "المفقود: <b>{missing}</b>\n\n"
            "أرسل 7 أسطر بهذا الترتيب:\n"
            "الاسم، الجامعة، القسم، المادة، العنوان، المشرف، التاريخ"
        ),
        "logo": (
            "🖼️ أخيراً — <b>شعار الجامعة</b>\n\n"
            "ارفع <b>PNG</b> أو <b>JPG</b>، أو اضغط <b>⏭️ بدون شعار</b>."
        ),
        "format": (
            "✅ تم جمع البيانات.\n"
            "أرسل الغلاف أو اضغط <b>تعديل</b>."
        ),
        "format_more": (
            "✅ تم الإرسال.\n"
            "عدّل ثم أرسل من جديد إن تريد."
        ),
        "generating": "⏳ جاري توليد النماذج الثلاثة (مرتب + رسمي + جامعي)...",
        "sent_pdf": "✅ تم إرسال 3 أغلفة PDF (مرتب + رسمي + جامعي)!",
        "sent_docx": "✅ تم إرسال 3 أغلفة Word (مرتب + رسمي + جامعي)!",
        "finished": "✅ تم! يمكنك بدء تقرير جديد من القائمة.",
        "err_empty": "❌ الحقل فارغ — أعد الإرسال.",
        "err_logo": "❌ أرسل صورة PNG/JPG أو اضغط <b>⏭️ بدون شعار</b>.",
        "err_logo_type": "❌ الصيغ المدعومة: PNG, JPG",
        "skip_logo": "⏭️ بدون شعار",
        "edit_saved": "✅ تم حفظ التعديل.",
        "back_review": "↩️ عاد للملخص.",
        "edit_intro": (
            "✏️ أرسل <b>كل البيانات المعدّلة</b> برسالة واحدة (7 أسطر)، ثم الشعار."
        ),
        "fields": {
            "lang": "اللغة",
            "full_name": "الاسم",
            "university": "الجامعة",
            "department": "القسم",
            "subject": "المادة",
            "title": "العنوان",
            "supervisor": "المشرف",
            "date": "التاريخ",
            "logo": "الشعار",
        },
        "logo_yes": "مرفق",
        "logo_no": "بدون شعار",
        "lang_ar": "العربية",
        "lang_en": "English",
        "send_pdf": "📄 إرسال PDF",
        "send_docx": "📝 إرسال Word",
        "resend_pdf": "🔄 PDF من جديد",
        "resend_docx": "🔄 Word من جديد",
        "reenter_all": "✏️ تعديل",
        "done": "✅ إنهاء",
        "cancel_edit": "إلغاء التعديل",
    },
    "en": {
        "start": (
            "📋 **Report Cover Generator**\n\n"
            "Choose **cover language** first:\n"
            "🇸🇦 Arabic  |  🇬🇧 English"
        ),
        "details": (
            "📋 Send <b>all details in one message</b>\n"
            "One item per <b>line</b>, in this order:\n\n"
            "1. Full name — if several students, put them on <b>the same line</b> separated by commas\n"
            "2. University\n"
            "3. Department / Major\n"
            "4. Course\n"
            "5. Report title\n"
            "6. Supervisor\n"
            "7. Date — type <b>today</b> or e.g. <code>13/09/2026</code>\n\n"
            "Example:\n"
            "<pre>"
            "Ghaith Asaad\n"
            "University of Anbar\n"
            "College of Engineering / Civil Department\n"
            "Soil\n"
            "Soil Tests\n"
            "Dr. Mohammed Ali\n"
            "today"
            "</pre>\n\n"
            "Several students on the name line: <code>Ghaith Asaad, Ahmed Ali, Mohammed Hassan</code>"
        ),
        "err_batch": (
            "❌ Missing details.\n"
            "Needed: <b>{missing}</b>\n\n"
            "Send 7 lines in this order:\n"
            "name, university, department, course, title, supervisor, date"
        ),
        "logo": (
            "🖼️ Last step — <b>University logo</b>\n\n"
            "Upload <b>PNG</b> or <b>JPG</b>, or tap <b>⏭️ No logo</b>."
        ),
        "format": (
            "✅ All data collected.\n"
            "Send the cover, or tap <b>Edit</b>."
        ),
        "format_more": (
            "✅ Sent.\n"
            "Edit then send again if you want."
        ),
        "generating": "⏳ Generating 3 covers (Modern + Formal + Academic)...",
        "sent_pdf": "✅ 3 PDF covers sent (Modern + Formal + Academic)!",
        "sent_docx": "✅ 3 Word covers sent (Modern + Formal + Academic)!",
        "finished": "✅ Done! Start a new report from the menu.",
        "err_empty": "❌ Empty field — please send a value.",
        "err_logo": "❌ Send a PNG/JPG image or tap <b>⏭️ No logo</b>.",
        "err_logo_type": "❌ Supported: PNG, JPG",
        "skip_logo": "⏭️ No logo",
        "edit_saved": "✅ Change saved.",
        "back_review": "↩️ Back to summary.",
        "edit_intro": (
            "✏️ Send <b>all updated details</b> in one message (7 lines), then the logo."
        ),
        "fields": {
            "lang": "Language",
            "full_name": "Name",
            "university": "University",
            "department": "Department",
            "subject": "Course",
            "title": "Title",
            "supervisor": "Supervisor",
            "date": "Date",
            "logo": "Logo",
        },
        "logo_yes": "Attached",
        "logo_no": "No logo",
        "lang_ar": "Arabic",
        "lang_en": "English",
        "send_pdf": "📄 Send PDF",
        "send_docx": "📝 Send Word",
        "resend_pdf": "🔄 PDF again",
        "resend_docx": "🔄 Word again",
        "reenter_all": "✏️ Edit",
        "done": "✅ Done",
        "cancel_edit": "Cancel edit",
    },
}


REVIEW_FIELDS = (
    "lang",
    "full_name",
    "university",
    "department",
    "subject",
    "title",
    "supervisor",
    "date",
    "logo",
)

BATCH_FIELDS = (
    "full_name",
    "university",
    "department",
    "subject",
    "title",
    "supervisor",
    "date",
)

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "full_name": ("الاسم الكامل", "اسمك الكامل", "أسماء الطلاب", "الاسم", "name", "full name", "students"),
    "university": ("اسم الجامعة", "الجامعة", "university"),
    "department": ("القسم / التخصص", "التخصص", "القسم", "department", "major"),
    "subject": ("اسم المادة", "المادة", "subject", "course"),
    "title": ("عنوان التقرير", "العنوان", "title", "report title"),
    "supervisor": ("اسم الدكتور المشرف", "الدكتور المشرف", "المشرف", "supervisor"),
    "date": ("التاريخ", "date"),
}

_NUM_PREFIX = re.compile(
    r"^\s*(?:(?:\d{1,2}|[١-٩])[\.\)\-:]|[*\-•])\s+"
)


def _norm_label(text: str) -> str:
    text = (text or "").strip().lower()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه")
    return re.sub(r"\s+", "", text)


_ALIAS_LOOKUP = {
    _norm_label(alias): field
    for field, aliases in _FIELD_ALIASES.items()
    for alias in sorted(aliases, key=len, reverse=True)
}


def _normalize_date(text: str) -> str:
    value = (text or "").strip()
    if value.lower() in {
        "اليوم", "today", "تاريخ اليوم", "📅 تاريخ اليوم", "📅 today",
    }:
        return datetime.now().strftime("%d/%m/%Y")
    return value


def _strip_line_prefix(line: str) -> str:
    return _NUM_PREFIX.sub("", line).strip()


def _labeled_field(line: str) -> tuple[str, str] | None:
    raw = _strip_line_prefix(line)
    if not raw:
        return None
    match = re.match(r"^(.+?)\s*[:：=\-–]\s*(.+)$", raw)
    if not match:
        return None
    field = _ALIAS_LOOKUP.get(_norm_label(match.group(1)))
    value = match.group(2).strip()
    if field and value:
        return field, value
    return None


def _parse_batch(text: str) -> tuple[dict[str, str], list[str]]:
    lines = [_strip_line_prefix(part) for part in (text or "").splitlines()]
    lines = [line for line in lines if line]
    parsed: dict[str, str] = {}
    unlabeled: list[str] = []
    for line in lines:
        labeled = _labeled_field(line)
        if labeled:
            field, value = labeled
            parsed[field] = value
        else:
            unlabeled.append(line)

    if len(parsed) < 3:
        parsed = {}
        for field, line in zip(BATCH_FIELDS, lines):
            parsed[field] = line
    else:
        missing_now = [field for field in BATCH_FIELDS if not parsed.get(field)]
        for field, line in zip(missing_now, unlabeled):
            parsed[field] = line

    if parsed.get("date"):
        parsed["date"] = _normalize_date(parsed["date"])

    missing = [field for field in BATCH_FIELDS if not (parsed.get(field) or "").strip()]
    return parsed, missing


def _lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return _report_data(context).get("lang", "ar")


def _p(context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    block = PROMPTS.get(_lang(context), PROMPTS["ar"])
    val = block.get(key, "")
    return val if isinstance(val, str) else ""


def _field_label(context: ContextTypes.DEFAULT_TYPE, field: str) -> str:
    block = PROMPTS.get(_lang(context), PROMPTS["ar"])
    return (block.get("fields") or {}).get(field, field)


def _report_data(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("report_cover", {})


def _generated_formats(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    data = _report_data(context)
    raw = data.get("generated_formats", [])
    if isinstance(raw, set):
        raw = list(raw)
        data["generated_formats"] = raw
    return raw


def _field_display(context: ContextTypes.DEFAULT_TYPE, field: str) -> str:
    data = _report_data(context)
    if field == "lang":
        return _p(context, "lang_en" if data.get("lang") == "en" else "lang_ar")
    if field == "logo":
        return _p(context, "logo_yes" if data.get("logo_path") else "logo_no")
    return (data.get(field) or "").strip() or "—"


def _details_pre(context: ContextTypes.DEFAULT_TYPE) -> str:
    data = _report_data(context)
    lines = [
        html.escape((data.get(field) or "").strip() or "—")
        for field in BATCH_FIELDS
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"


def _review_body(context: ContextTypes.DEFAULT_TYPE) -> str:
    data = _report_data(context)
    logo_label = html.escape(_field_label(context, "logo"))
    logo_value = html.escape(_p(context, "logo_yes" if data.get("logo_path") else "logo_no"))
    return f"{_details_pre(context)}\n\n{logo_label}: {logo_value}"


def _review_keyboard(context: ContextTypes.DEFAULT_TYPE) -> ReplyKeyboardMarkup:
    done = _generated_formats(context)
    pdf_key = "resend_pdf" if "pdf" in done else "send_pdf"
    docx_key = "resend_docx" if "docx" in done else "send_docx"
    return ReplyKeyboardMarkup(
        [
            [_p(context, pdf_key), _p(context, docx_key)],
            [_p(context, "reenter_all")],
            ["🏠 القائمة الرئيسية"],
        ],
        resize_keyboard=True,
    )


def _clear_report(context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.pop("report_cover", None)
    if not data:
        return
    logo = data.get("logo_path")
    if logo:
        try:
            Path(logo).unlink(missing_ok=True)
        except OSError:
            pass


def setup_report_cover_handlers(db, back_to_main) -> ConversationHandler:
    async def _show_review(message, context: ContextTypes.DEFAULT_TYPE, header_key: str):
        data = _report_data(context)
        data["from_review"] = True
        data["in_edit"] = False
        data.pop("editing_field", None)
        header = _p(context, header_key)
        body = _review_body(context)
        await message.reply_text(
            f"{header}\n\n{body}",
            parse_mode="HTML",
            reply_markup=_review_keyboard(context),
        )
        return RC.RC_FORMAT

    async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data.get("report_cover") or {}
        msg = update.effective_message
        if data.get("in_edit"):
            data["in_edit"] = False
            data.pop("editing_field", None)
            if msg:
                await msg.reply_text(_p(context, "back_review"), reply_markup=cancel_keyboard())
                return await _show_review(msg, context, "format")
        _clear_report(context)
        chat_id = update.effective_chat.id
        await context.bot.send_message(
            chat_id,
            "❌ تم إلغاء واجهة التقرير.",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END

    async def exit_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
        _clear_report(context)
        return await back_to_main(update, context)

    async def start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_channel_subscription(update, context, db):
            return ConversationHandler.END
        _clear_report(context)
        context.user_data["report_cover"] = {}
        await update.message.reply_text(
            PROMPTS["ar"]["start"],
            parse_mode="Markdown",
            reply_markup=LANGUAGE_KEYBOARD,
        )
        return RC.RC_LANGUAGE

    async def receive_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        lang = "en" if query.data == "rc_lang_en" else "ar"
        _report_data(context)["lang"] = lang
        await query.edit_message_text(f"✅ {'English' if lang == 'en' else 'العربية'}")
        if _report_data(context).get("from_review"):
            return await _show_review(query.message, context, "edit_saved")
        await query.message.reply_text(
            _p(context, "details"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return RC.RC_FULL_NAME

    async def receive_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if _is_cancel_text(text):
            return await cancel_flow(update, context)
        parsed, missing = _parse_batch(text)
        if missing:
            labels = "، ".join(_field_label(context, field) for field in missing)
            await update.message.reply_text(
                f"{_p(context, 'err_batch').format(missing=html.escape(labels))}\n\n{_p(context, 'details')}",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
            return RC.RC_FULL_NAME
        data = _report_data(context)
        data.update(parsed)
        data.pop("editing_field", None)
        return await _go_logo_step(update.message, context)

    async def start_reenter_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query:
            await query.answer()
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            message = query.message
        else:
            message = update.message
        data = _report_data(context)
        data["in_edit"] = True
        data["from_review"] = False
        await message.reply_text(
            f"{_p(context, 'edit_intro')}\n{_details_pre(context)}\n\n{_p(context, 'details')}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return RC.RC_FULL_NAME

    async def _go_logo_step(message, context: ContextTypes.DEFAULT_TYPE):
        await message.reply_text(
            _p(context, "logo"),
            parse_mode="HTML",
            reply_markup=LOGO_KEYBOARD,
        )
        return RC.RC_LOGO

    async def receive_date_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if _is_cancel_text(text):
            return await cancel_flow(update, context)
        if text.lower() in ("اليوم", "today", "📅 تاريخ اليوم", "📅 today"):
            text = datetime.now().strftime("%d/%m/%Y")
        if not text:
            await update.message.reply_text(_p(context, "err_empty"))
            return RC.RC_DATE
        _report_data(context)["date"] = text
        if _report_data(context).get("from_review"):
            return await _show_review(update.message, context, "edit_saved")
        return await _go_logo_step(update.message, context)

    async def receive_date_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _report_data(context)["date"] = datetime.now().strftime("%d/%m/%Y")
        await update.callback_query.edit_message_text(
            f"✅ {_report_data(context)['date']}"
        )
        if _report_data(context).get("from_review"):
            return await _show_review(update.callback_query.message, context, "edit_saved")
        return await _go_logo_step(update.callback_query.message, context)

    async def _go_format_step(message, context: ContextTypes.DEFAULT_TYPE):
        if _report_data(context).get("from_review"):
            return await _show_review(message, context, "edit_saved")
        header = "format_more" if _generated_formats(context) else "format"
        return await _show_review(message, context, header)

    async def finish_format_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(_p(context, "finished"))
        _clear_report(context)
        await context.bot.send_message(
            update.effective_chat.id,
            "📋 واجهة تقرير",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END

    async def receive_logo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if _is_cancel_text(text):
            return await cancel_flow(update, context)
        skip_words = {"⏭️ بدون شعار", "بدون شعار", "skip", "⏭️ no logo", "no logo"}
        if text.lower() in {w.lower() for w in skip_words} or text in skip_words:
            _report_data(context)["logo_path"] = None
            return await _go_format_step(update.message, context)

        user_dir = get_user_temp_dir(update.effective_user.id)
        logo_path = user_dir / "report_logo.jpg"

        if update.message.photo:
            tg_file = await update.message.photo[-1].get_file()
            await tg_file.download_to_drive(str(logo_path))
            _report_data(context)["logo_path"] = str(logo_path)
            return await _go_format_step(update.message, context)

        doc = update.message.document
        if doc:
            suffix = Path(doc.file_name or "").suffix.lower()
            if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                await update.message.reply_text(
                    _p(context, "err_logo_type"),
                    reply_markup=LOGO_KEYBOARD,
                )
                return RC.RC_LOGO
            logo_path = user_dir / f"report_logo{suffix}"
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(str(logo_path))
            _report_data(context)["logo_path"] = str(logo_path)
            return await _go_format_step(update.message, context)

        await update.message.reply_text(_p(context, "err_logo"), parse_mode="HTML", reply_markup=LOGO_KEYBOARD)
        return RC.RC_LOGO

    async def start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        field = (query.data or "").removeprefix("rc_edit_")
        if field not in REVIEW_FIELDS:
            return RC.RC_FORMAT
        data = _report_data(context)
        data["in_edit"] = True
        data["editing_field"] = field
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        label = html.escape(_field_label(context, field))
        current = html.escape(_field_display(context, field))
        intro = (
            f"✏️ <b>{label}</b>\n"
            f"{_p(context, 'edit_value')}\n\n"
            f"<i>{current}</i>"
        )
        if field == "lang":
            await query.message.reply_text(
                intro, parse_mode="HTML", reply_markup=LANGUAGE_KEYBOARD,
            )
            return RC.RC_LANGUAGE
        if field == "date":
            btn_label = "📅 Today" if _lang(context) == "en" else "📅 تاريخ اليوم"
            await query.message.reply_text(
                intro, parse_mode="HTML", reply_markup=cancel_keyboard(),
            )
            await query.message.reply_text(
                btn_label,
                reply_markup=InlineKeyboardMarkup.from_row([
                    InlineKeyboardButton(btn_label, callback_data="rc_date_today"),
                ]),
            )
            return RC.RC_DATE
        if field == "logo":
            await query.message.reply_text(
                intro, parse_mode="HTML", reply_markup=LOGO_KEYBOARD,
            )
            return RC.RC_LOGO
        await query.message.reply_text(
            intro, parse_mode="HTML", reply_markup=cancel_keyboard(),
        )
        return RC.RC_EDIT_VALUE

    async def receive_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if _is_cancel_text(text):
            return await cancel_flow(update, context)
        if not text:
            await update.message.reply_text(_p(context, "err_empty"))
            return RC.RC_EDIT_VALUE
        field = _report_data(context).get("editing_field")
        if field not in {"full_name", "university", "department", "subject", "title", "supervisor"}:
            return await _show_review(update.message, context, "format")
        _report_data(context)[field] = text
        return await _show_review(update.message, context, "edit_saved")

    async def generate_output(update: Update, context: ContextTypes.DEFAULT_TYPE, *, fmt: str | None = None):
        query = update.callback_query
        if query:
            await query.answer()
            if query.data not in ("rc_fmt_pdf", "rc_fmt_docx"):
                return RC.RC_FORMAT
            is_pdf = query.data == "rc_fmt_pdf"
            status = await query.edit_message_text(_p(context, "generating"))
            review_message = query.message
        else:
            is_pdf = fmt == "pdf"
            status = await update.message.reply_text(_p(context, "generating"))
            review_message = update.message

        user_id = update.effective_user.id
        cover = ReportCoverData.from_dict(_report_data(context))
        user_dir = get_user_temp_dir(user_id)
        ext = "pdf" if is_pdf else "docx"
        fmt_key = "pdf" if is_pdf else "docx"
        lang = cover.lang
        names = TEMPLATE_NAMES.get(lang, TEMPLATE_NAMES["ar"])

        outputs: list[Path] = []
        sent = 0
        errors: list[str] = []

        try:
            for template in ALL_TEMPLATES:
                safe = re.sub(r"[^\w\-]", "_", cover.title[:20], flags=re.ASCII) or "cover"
                safe = re.sub(r"_+", "_", safe).strip("_") or "cover"
                label = names.get(template, template)
                filename = f"cover_{template}_{safe}.{ext}"
                path = user_dir / filename
                try:
                    if is_pdf:
                        generate_cover_pdf(cover, path, template=template)
                    else:
                        generate_cover_docx(cover, path, template=template)
                    outputs.append(path)

                    icon = "📄" if is_pdf else "📝"
                    with open(path, "rb") as f:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=f,
                            filename=filename,
                            caption=f"{icon} {label} — {'PDF' if is_pdf else 'Word'}",
                        )
                    sent += 1
                except Exception as e:
                    logger.exception("Report cover %s failed: %s", template, e)
                    errors.append(f"{label}: {e}")

            generated = _generated_formats(context)
            if sent and fmt_key not in generated:
                generated.append(fmt_key)

            if sent:
                log_user_activity(
                    db, user_id,
                    "report_cover_pdf" if is_pdf else "report_cover_docx",
                    f"{cover.title[:80]} ({lang}, {sent}/{len(ALL_TEMPLATES)})",
                )

            if errors and not sent:
                await status.edit_text("❌ " + " | ".join(errors[:2]))
            elif errors:
                await status.edit_text(
                    f"{_p(context, 'sent_pdf' if is_pdf else 'sent_docx')}\n⚠️ {sent}/3 — {errors[0]}"
                )
            else:
                await status.edit_text(_p(context, "sent_pdf" if is_pdf else "sent_docx"))
            return await _show_review(
                review_message, context, "format_more" if sent else "format",
            )
        except Exception as e:
            logger.exception("Report cover generation failed: %s", e)
            await status.edit_text(f"❌ {e}")
            return await _show_review(review_message, context, "format")
        finally:
            for path in outputs:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

        return RC.RC_FORMAT

    async def receive_review_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if _is_cancel_text(text):
            return await cancel_flow(update, context)
        if text in {"🏠 القائمة الرئيسية", "🔙 القائمة الرئيسية"}:
            return await exit_to_main(update, context)
        pdf_labels = {
            PROMPTS["ar"]["send_pdf"], PROMPTS["ar"]["resend_pdf"],
            PROMPTS["en"]["send_pdf"], PROMPTS["en"]["resend_pdf"],
        }
        docx_labels = {
            PROMPTS["ar"]["send_docx"], PROMPTS["ar"]["resend_docx"],
            PROMPTS["en"]["send_docx"], PROMPTS["en"]["resend_docx"],
        }
        edit_labels = {
            PROMPTS["ar"]["reenter_all"], PROMPTS["en"]["reenter_all"],
        }
        if text in pdf_labels:
            return await generate_output(update, context, fmt="pdf")
        if text in docx_labels:
            return await generate_output(update, context, fmt="docx")
        if text in edit_labels:
            return await start_reenter_all(update, context)
        await update.message.reply_text(
            _p(context, "format"),
            parse_mode="HTML",
            reply_markup=_review_keyboard(context),
        )
        return RC.RC_FORMAT

    cancel_handler = _cancel_handler(cancel_flow)

    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📋 واجهة تقرير$"), start_flow)],
        states={
            RC.RC_LANGUAGE: [
                cancel_handler,
                CallbackQueryHandler(receive_language, pattern="^rc_lang_(ar|en)$"),
            ],
            RC.RC_FULL_NAME: [
                cancel_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_details),
            ],
            RC.RC_DATE: [
                cancel_handler,
                CallbackQueryHandler(receive_date_btn, pattern="^rc_date_today$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_date_text),
            ],
            RC.RC_LOGO: [
                cancel_handler,
                MessageHandler(
                    filters.PHOTO | filters.Document.ALL | filters.TEXT & ~filters.COMMAND,
                    receive_logo,
                ),
            ],
            RC.RC_FORMAT: [
                cancel_handler,
                CallbackQueryHandler(finish_format_flow, pattern="^rc_fmt_done$"),
                CallbackQueryHandler(generate_output, pattern="^rc_fmt_(pdf|docx)$"),
                CallbackQueryHandler(start_reenter_all, pattern="^rc_edit_all$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_review_action),
            ],
            RC.RC_EDIT_VALUE: [
                cancel_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_value),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_flow),
            MessageHandler(filters.Text(list(CANCEL_TEXTS)), cancel_flow),
            MessageHandler(filters.Regex("^🔙 القائمة الرئيسية$|^🏠 القائمة الرئيسية$"), exit_to_main),
        ],
        allow_reentry=True,
    )
