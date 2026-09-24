"""
امتحانات — نفس أسلوب استطلاع تيليجرام (بسيط)
"""
import logging
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Poll, Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, PollAnswerHandler, filters,
)

from database.db import Database
from services.channel_check import check_channel_subscription
from services.exam_ui import format_exam_preview_for_student, format_student_result, format_creator_stats
from services.exam_export import export_results_csv, export_results_pdf
from services.exam_parser import (
    parse_mcq_batch, format_question_block, EXAMPLE_MCQ, option_letter,
)
from utils.helpers import format_percentage, generate_exam_id, get_user_temp_dir, build_exam_link, parse_exam_id_from_start, split_text_chunks
from utils.keyboards import exam_menu, exam_start_keyboard, exam_creator_results_keyboard, MAIN_MENU
from utils import states
from utils.activity_log import log_user_activity

logger = logging.getLogger(__name__)


def _trim(s, n):
    s = (s or "").strip()
    return s[:n-3] + "..." if len(s) > n else s or "—"


def _poll_correct_kb(options):
    """أزرار الإجابة الصحيحة — مثل الاستطلاع"""
    labels = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{labels[i] if i < 10 else i+1}) {o[:30]}", callback_data=f"pc_{i}"
        )] for i, o in enumerate(options)
    ])


def _duration_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏱ 10 د", callback_data="pdur_10"),
            InlineKeyboardButton("⏱ 15 د", callback_data="pdur_15"),
            InlineKeyboardButton("⏱ 30 د", callback_data="pdur_30"),
        ],
        [
            InlineKeyboardButton("⏱ 45 د", callback_data="pdur_45"),
            InlineKeyboardButton("⏱ 60 د", callback_data="pdur_60"),
            InlineKeyboardButton("⏱ 90 د", callback_data="pdur_90"),
        ],
    ])


def _format_timer(sec: float) -> str:
    m, s = int(sec // 60), int(sec % 60)
    return f"{m:02d}:{s:02d}"


def _after_question_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ سؤال آخر", callback_data="pq_more"),
            InlineKeyboardButton("✅ نشر", callback_data="pq_done"),
        ],
    ])


def _review_keyboard(n: int, page: int = 0, page_size: int = 20) -> InlineKeyboardMarkup:
    start = page * page_size
    end = min(n, start + page_size)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i in range(start, end):
        row.append(InlineKeyboardButton(str(i + 1), callback_data=f"eqb_v_{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"eqb_p_{page - 1}"))
    if end < n:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"eqb_p_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("➕ المزيد", callback_data="eqb_more"),
        InlineKeyboardButton("✅ نشر", callback_data="pq_done"),
    ])
    return InlineKeyboardMarkup(rows)


def _question_edit_kb(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ تعديل", callback_data=f"eqb_e_{index}"),
            InlineKeyboardButton("🖼️ صورة", callback_data=f"eqb_i_{index}"),
            InlineKeyboardButton("🗑️ حذف", callback_data=f"eqb_d_{index}"),
        ],
        [InlineKeyboardButton("🔙 الأسئلة", callback_data="eqb_review")],
    ])


def _review_text(pe: dict) -> str:
    qs = pe.get("questions") or []
    lines = [
        f"📝 {pe.get('title') or 'امتحان'}",
        f"⏱ {pe.get('duration', 30)} دقيقة | ❓ {len(qs)} سؤال",
        "",
        "اضغط رقم السؤال للتعديل أو إضافة صورة.",
        "",
    ]
    for i, q in enumerate(qs, 1):
        letter = option_letter(q.get("correct_index", -1))
        img = " 🖼️" if q.get("image") else ""
        preview = (q.get("question") or "—").replace("\n", " ")
        if len(preview) > 70:
            preview = preview[:67] + "..."
        lines.append(f"{i}. {preview}{img}")
        lines.append(f"   الجواب: {letter}")
    return "\n".join(lines)


BULK_PROMPT = (
    "📋 أرسل الأسئلة دفعة واحدة بهذا الشكل:\n\n"
    "<pre>"
    "السؤال\n"
    "A) الخيار الأول\n"
    "B) الخيار الثاني\n"
    "C) الخيار الثالث\n"
    "D) الخيار الرابع\n"
    "E) الخيار الخامس\n"
    "B"
    "</pre>\n\n"
    "السطر الأخير <b>حرف الجواب فقط</b> (A أو B أو C أو D أو E).\n"
    "يمكنك لصق عشرات الأسئلة في رسالة واحدة، أو عدة رسائل ثم اكتب <b>تم</b>.\n"
    "أو أرسل ملف TXT."
)


# ─── دخول / حل ───

def _skip_img_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ التالي (بدون صورة)", callback_data="pq_skip_img")],
    ])


def _publish_kb(link: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 فتح الامتحان", url=link)],
    ])


def _init_active_exam(context, exam, exam_id, user_id):
    dur = exam["duration_minutes"]
    context.user_data["active_exam"] = {
        "exam_id": exam_id, "title": exam["title"], "questions": exam["questions"],
        "duration_sec": dur * 60,
        "current": 0, "answers": [], "start_time": time.time(),
        "taker_id": user_id,
    }
    return dur


async def start_exam_directly(context, db, exam_id, chat_id, user_id):
    """يبدأ الامتحان فوراً — عند فتح الرابط"""
    exam_id = (exam_id or "").strip().lower()
    exam = db.get_exam(exam_id)
    if not exam:
        logger.warning("Exam not found for id=%r", exam_id)
        await context.bot.send_message(
            chat_id,
            "❌ الامتحان غير موجود.\n"
            "تأكد أنك تستخدم آخر رابط من رسالة «تم النشر».",
        )
        return False
    if db.has_taken_exam(exam_id, user_id):
        await context.bot.send_message(chat_id, "❌ أديت هذا الامتحان مسبقاً.")
        return False
    dur = _init_active_exam(context, exam, exam_id, user_id)
    await context.bot.send_message(
        chat_id,
        f"▶️ **{exam['title']}**\n"
        f"⏱ **{dur} دقيقة** لكل الامتحان ({len(exam['questions'])} سؤال)\n"
        f"العداد يبدأ الآن — يُطبّق على جميع الأسئلة ⬇️",
        parse_mode="Markdown",
    )
    await _send_poll_question(context, chat_id, context.user_data["active_exam"])
    log_user_activity(db, user_id, "exam_start", f"{exam_id} — {exam['title']}")
    return True


async def open_exam_for_user(context, db, exam_id, chat_id, user_id, direct=False):
    """فتح الامتحان — direct=True يبدأ الأسئلة مباشرة (من الرابط)"""
    if direct:
        return await start_exam_directly(context, db, exam_id, chat_id, user_id)
    exam = db.get_exam(exam_id)
    if not exam:
        await context.bot.send_message(chat_id, "❌ الامتحان غير موجود.")
        return False
    if db.has_taken_exam(exam_id, user_id):
        await context.bot.send_message(chat_id, "❌ أديت هذا الامتحان مسبقاً.")
        return False
    await context.bot.send_message(
        chat_id,
        format_exam_preview_for_student(exam),
        parse_mode="Markdown",
        reply_markup=exam_start_keyboard(exam_id),
    )
    return True


async def show_exam_entry(update, context, db, exam_id):
    return await open_exam_for_user(
        context, db, exam_id,
        update.effective_chat.id, update.effective_user.id,
    )


async def _send_poll_question(context, chat_id, ed):
    idx = ed["current"]
    qs = ed["questions"]
    if idx >= len(qs):
        return False
    q = qs[idx]
    total = len(qs)
    left = max(0, ed["duration_sec"] - (time.time() - ed["start_time"]))

    if q.get("image"):
        await context.bot.send_photo(chat_id, q["image"], caption=f"📷 سؤال {idx+1}/{total}")

    q_text = q.get("question") or "اختر الإجابة:"
    msg = await context.bot.send_poll(
        chat_id,
        _trim(f"{idx+1}/{total}  ⏱ {_format_timer(left)}\n{q_text}", 300),
        [_trim(o, 100) for o in q["options"]],
        type=Poll.QUIZ,
        correct_option_id=q["correct_index"],
        is_anonymous=False,
        explanation=f"✅ {q['options'][q['correct_index']]}",
    )
    context.bot_data[msg.poll.id] = {"k": "ex", "uid": ed["taker_id"], "chat": chat_id, "idx": idx}
    return True


async def _finish(context, db, chat_id, ed):
    qs, ans = ed["questions"], ed["answers"]
    ok = sum(1 for i, q in enumerate(qs) if i < len(ans) and ans[i] == q["correct_index"])
    total, wrong = len(qs), len(qs) - ok
    pct = ok / total * 100 if total else 0
    db.save_exam_result(ed["exam_id"], ed["taker_id"], ok, total, pct, ans)
    log_user_activity(
        db, ed["taker_id"], "exam_finish",
        f"{ed['exam_id']} — {ok}/{total} ({pct:.0f}%)",
    )
    await context.bot.send_message(
        chat_id,
        format_student_result(ed["title"], ok, wrong, total, pct,
                              datetime.now().strftime("%H:%M %d/%m/%Y")),
        parse_mode="Markdown", reply_markup=MAIN_MENU,
    )


def setup_exam_handlers(db, back_to_main):

    # ══ إنشاء — مثل Poll ══
    async def create_start(update, context):
        if not await check_channel_subscription(update, context, db):
            return ConversationHandler.END
        context.user_data["poll_exam"] = {"title": "", "duration": 30, "questions": [], "page": 0}
        await update.message.reply_text("📊 إنشاء امتحان\n\nأرسل **عنوان الامتحان**:", parse_mode="Markdown")
        return states.EXAM_CREATE_TITLE

    async def create_title(update, context):
        if update.message.text == "🔙 القائمة الرئيسية":
            return await back_to_main(update, context)
        context.user_data["poll_exam"]["title"] = update.message.text.strip()
        await update.message.reply_text(
            "⏱ **زمن الامتحان**\n\n"
            "يُحدد مرة واحدة ويُطبّق على كل الأسئلة.\n"
            "اختر من الأزرار أو أرسل عدد الدقائق:",
            parse_mode="Markdown",
            reply_markup=_duration_kb(),
        )
        return states.EXAM_CREATE_DURATION

    async def set_duration_text(update, context):
        try:
            d = int(update.message.text.strip())
            if not 1 <= d <= 180:
                raise ValueError
            context.user_data["poll_exam"]["duration"] = d
        except ValueError:
            await update.message.reply_text("❌ أرسل رقماً بين 1 و 180.")
            return states.EXAM_CREATE_DURATION
        await update.message.reply_text(
            f"✅ الزمن: <b>{d} دقيقة</b>\n\n{BULK_PROMPT}",
            parse_mode="HTML",
        )
        return states.EXAM_BULK_INPUT

    async def set_duration_btn(update, context):
        await update.callback_query.answer()
        d = int(update.callback_query.data.replace("pdur_", ""))
        context.user_data["poll_exam"]["duration"] = d
        await update.callback_query.edit_message_text(
            f"✅ الزمن: <b>{d} دقيقة</b>\n\n{BULK_PROMPT}",
            parse_mode="HTML",
        )
        return states.EXAM_BULK_INPUT

    async def _send_review(target, context, *, edit=False):
        pe = context.user_data.get("poll_exam") or {}
        qs = pe.get("questions") or []
        page = int(pe.get("page") or 0)
        if qs:
            text = _review_text(pe)
            kb = _review_keyboard(len(qs), page)
            parse_mode = None
        else:
            text = "لا توجد أسئلة بعد.\n\n" + BULK_PROMPT
            kb = None
            parse_mode = "HTML"
        chunks = split_text_chunks(text, 3500)

        def _chat_id():
            if hasattr(target, "effective_chat") and target.effective_chat:
                return target.effective_chat.id
            if hasattr(target, "message") and target.message:
                return target.message.chat_id
            if hasattr(target, "chat"):
                return target.chat.id
            return None

        if edit:
            try:
                await target.edit_message_text(
                    chunks[0], reply_markup=kb, parse_mode=parse_mode,
                )
                return
            except Exception:
                pass
        chat_id = _chat_id()
        await context.bot.send_message(
            chat_id, chunks[0], reply_markup=kb if len(chunks) == 1 else None,
            parse_mode=parse_mode,
        )
        for i, chunk in enumerate(chunks[1:], 1):
            last = i == len(chunks) - 1
            await context.bot.send_message(
                chat_id, chunk, reply_markup=kb if last else None,
            )

    async def _extract_bulk_text(update, user_id) -> str:
        if update.message.document:
            doc = update.message.document
            name = (doc.file_name or "").lower()
            mime = (doc.mime_type or "").lower()
            if not (name.endswith(".txt") or mime.startswith("text/")):
                return ""
            path = get_user_temp_dir(user_id) / (doc.file_name or "questions.txt")
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(str(path))
            return path.read_text(encoding="utf-8", errors="ignore")
        return update.message.text or ""

    async def receive_bulk(update, context):
        pe = context.user_data.setdefault(
            "poll_exam", {"title": "", "duration": 30, "questions": [], "page": 0},
        )
        raw = (update.message.text or "").strip()
        if raw in ("تم", "تم.", "✅ تم"):
            if not pe.get("questions"):
                await update.message.reply_text("❌ لم تُرسل أسئلة بعد.")
                return states.EXAM_BULK_INPUT
            await _send_review(update, context)
            return states.EXAM_AFTER_SAVE

        text = await _extract_bulk_text(update, update.effective_user.id)
        if not text.strip():
            await update.message.reply_text(
                "❌ أرسل النص أو ملف TXT بالصيغة المطلوبة.\n\n" + BULK_PROMPT,
                parse_mode="HTML",
            )
            return states.EXAM_BULK_INPUT

        qs, errs = parse_mcq_batch(text)
        if qs:
            pe.setdefault("questions", []).extend(qs)
        parts = []
        if qs:
            parts.append(f"✅ أُضيف {len(qs)} سؤال — الإجمالي {len(pe['questions'])}")
        if errs:
            parts.append("⚠️ " + "\n".join(errs[:10]))
        if not qs:
            parts.append("لم يُستخرج أي سؤال. استخدم الصيغة:\n" + EXAMPLE_MCQ)
            await update.message.reply_text("\n\n".join(parts))
            return states.EXAM_BULK_INPUT
        await update.message.reply_text("\n\n".join(parts))
        await _send_review(update, context)
        return states.EXAM_AFTER_SAVE

    async def review_cb(update, context):
        query = update.callback_query
        await query.answer()
        pe = context.user_data.get("poll_exam") or {}
        qs = pe.get("questions") or []
        data = query.data

        if data == "eqb_more":
            await query.message.reply_text(BULK_PROMPT, parse_mode="HTML")
            return states.EXAM_BULK_INPUT
        if data == "eqb_review":
            pe["page"] = 0
            await _send_review(query, context, edit=True)
            return states.EXAM_AFTER_SAVE
        if data.startswith("eqb_p_"):
            pe["page"] = int(data.replace("eqb_p_", "") or 0)
            await _send_review(query, context, edit=True)
            return states.EXAM_AFTER_SAVE
        if data.startswith("eqb_v_"):
            i = int(data.replace("eqb_v_", ""))
            if not 0 <= i < len(qs):
                return states.EXAM_AFTER_SAVE
            q = qs[i]
            if q.get("image"):
                await query.message.reply_photo(q["image"], caption=f"🖼️ سؤال {i + 1}")
            await query.message.reply_text(
                format_question_block(q, i + 1),
                reply_markup=_question_edit_kb(i),
            )
            return states.EXAM_AFTER_SAVE
        if data.startswith("eqb_e_"):
            i = int(data.replace("eqb_e_", ""))
            pe["edit_index"] = i
            await query.message.reply_text(
                f"✏️ تعديل السؤال {i + 1}\n\nأرسل السؤال من جديد بالصيغة:\n\n"
                f"<pre>السؤال\nA) ...\nB) ...\nC) ...\nD) ...\nE) ...\nB</pre>",
                parse_mode="HTML",
            )
            return states.EXAM_EDIT_ONE
        if data.startswith("eqb_i_"):
            i = int(data.replace("eqb_i_", ""))
            pe["image_index"] = i
            await query.message.reply_text(f"🖼️ أرسل صورة السؤال {i + 1}:")
            return states.EXAM_Q_IMAGE
        if data.startswith("eqb_d_"):
            i = int(data.replace("eqb_d_", ""))
            if 0 <= i < len(qs):
                qs.pop(i)
            await query.message.reply_text(f"🗑️ حُذف السؤال {i + 1}")
            await _send_review(query.message, context)
            return states.EXAM_AFTER_SAVE
        return states.EXAM_AFTER_SAVE

    async def edit_one_question(update, context):
        pe = context.user_data.get("poll_exam") or {}
        i = pe.get("edit_index")
        qs, errs = parse_mcq_batch(update.message.text or "")
        if len(qs) != 1:
            msg = "❌ أرسل سؤالاً واحداً بالصيغة المطلوبة."
            if errs:
                msg += "\n" + "\n".join(errs[:5])
            await update.message.reply_text(msg)
            return states.EXAM_EDIT_ONE
        if i is None or not 0 <= i < len(pe.get("questions") or []):
            pe.setdefault("questions", []).append(qs[0])
        else:
            old_img = pe["questions"][i].get("image")
            pe["questions"][i] = qs[0]
            if old_img:
                pe["questions"][i]["image"] = old_img
        pe.pop("edit_index", None)
        await update.message.reply_text("✅ تم تعديل السؤال.")
        await _send_review(update, context)
        return states.EXAM_AFTER_SAVE

    async def add_q_image(update, context):
        pe = context.user_data.get("poll_exam") or {}
        i = pe.get("image_index")
        qs = pe.get("questions") or []
        if i is None or not 0 <= i < len(qs):
            await update.message.reply_text("❌ اختر السؤال أولاً من القائمة.")
            return states.EXAM_AFTER_SAVE
        qs[i]["image"] = update.message.photo[-1].file_id
        pe.pop("image_index", None)
        await update.message.reply_text(f"✅ أُضيفت الصورة للسؤال {i + 1}")
        await _send_review(update, context)
        return states.EXAM_AFTER_SAVE

    async def publish(update, context):
        query = update.callback_query
        pe = context.user_data.get("poll_exam", {})
        if not pe.get("questions"):
            await query.answer("❌ أضف سؤالاً!", show_alert=True)
            return states.EXAM_AFTER_SAVE
        await query.answer()
        eid = generate_exam_id()
        uid = update.effective_user.id
        dur = pe.get("duration", 30)
        db.create_exam(eid, pe["title"], pe["questions"], dur, uid, is_published=True)
        log_user_activity(db, uid, "exam_create", f"{eid} — {pe['title']}")
        bot = (await context.bot.get_me()).username
        link = build_exam_link(bot, eid)
        await update.callback_query.message.reply_text(
            f"✅ <b>تم النشر!</b>\n\n"
            f"⏱ الزمن: <b>{dur} دقيقة</b> (لكل الامتحان)\n"
            f"❓ الأسئلة: {len(pe['questions'])}\n\n"
            f'🔗 <a href="{link}">اضغط هنا لفتح الامتحان</a>\n\n'
            f"<code>{link}</code>\n\n"
            f"شارك الرابط مع الطلاب — عند الضغط يفتح البوت ويبدأ الامتحان مباشرة.",
            parse_mode="HTML",
            reply_markup=_publish_kb(link),
            disable_web_page_preview=False,
        )
        context.user_data.pop("poll_exam", None)
        await update.callback_query.message.reply_text("🏠", reply_markup=exam_menu())
        return ConversationHandler.END

    # ══ حل ══
    async def exam_start(update, context):
        await update.callback_query.answer()
        eid = update.callback_query.data.replace("exam_start_", "")
        ex = db.get_exam(eid)
        if not ex:
            return
        if db.has_taken_exam(eid, update.effective_user.id):
            await update.callback_query.message.reply_text("❌ أديت هذا الامتحان مسبقاً.")
            return
        dur = _init_active_exam(context, ex, eid, update.effective_user.id)
        await update.callback_query.edit_message_text(
            f"▶️ **{ex['title']}**\n"
            f"⏱ **{dur} دقيقة** لكل الامتحان ({len(ex['questions'])} سؤال)\n"
            f"العداد يبدأ الآن — يُطبّق على جميع الأسئلة ⬇️",
            parse_mode="Markdown",
        )
        await _send_poll_question(context, update.effective_chat.id, context.user_data["active_exam"])
        log_user_activity(
            db, update.effective_user.id, "exam_start",
            f"{eid} — {ex['title']}",
        )

    async def on_poll_answer(update, context):
        pa = update.poll_answer
        if not pa or not pa.option_ids:
            return
        info = context.bot_data.get(pa.poll_id)
        if not info or info.get("k") != "ex" or info["uid"] != pa.user.id:
            return
        ed = context.user_data.get("active_exam")
        if not ed or info["idx"] != ed["current"]:
            return

        left = ed["duration_sec"] - (time.time() - ed["start_time"])
        if left <= 0:
            await context.bot.send_message(info["chat"], "⏱ انتهى وقت الامتحان!")
            await _finish(context, db, info["chat"], ed)
            context.user_data.pop("active_exam", None)
            context.bot_data.pop(pa.poll_id, None)
            return

        ed["answers"].append(pa.option_ids[0])
        ed["current"] += 1
        context.bot_data.pop(pa.poll_id, None)
        if ed["current"] >= len(ed["questions"]):
            await _finish(context, db, info["chat"], ed)
            context.user_data.pop("active_exam", None)
        else:
            remaining = ed["duration_sec"] - (time.time() - ed["start_time"])
            await context.bot.send_message(
                info["chat"],
                f"✅ {ed['current']}/{len(ed['questions'])} — ⏱ متبقي {_format_timer(remaining)}",
            )
            await _send_poll_question(context, info["chat"], ed)

    async def enter(update, context):
        if not await check_channel_subscription(update, context, db):
            return
        await update.message.reply_text("📝 الامتحانات", reply_markup=exam_menu())

    async def solve_prompt(update, context):
        if not await check_channel_subscription(update, context, db):
            return ConversationHandler.END
        await update.message.reply_text("أرسل رمز الامتحان:")
        return states.EXAM_WAIT_CODE

    async def solve_code(update, context):
        text = update.message.text.strip()
        if text.startswith("/exam"):
            text = text[5:].strip()
        eid = parse_exam_id_from_start([text]) if text else None
        if eid:
            await show_exam_entry(update, context, db, eid)
        else:
            await update.message.reply_text("❌ رمز الامتحان غير صالح.")
        return ConversationHandler.END

    async def my_exams(update, context):
        if not await check_channel_subscription(update, context, db):
            return
        exams = db.get_exams_by_creator(update.effective_user.id)
        if not exams:
            await update.message.reply_text("لا امتحانات.")
            return
        rows = [[InlineKeyboardButton(e["title"][:25], callback_data=f"er:view:{e['exam_id']}")]
                for e in exams[:15]]
        await update.message.reply_text("نتائج:", reply_markup=InlineKeyboardMarkup(rows))

    async def results_cb(update, context):
        await update.callback_query.answer()
        p = update.callback_query.data.split(":")
        act, eid = p[1], p[2]
        ex = db.get_exam(eid)
        res = db.get_exam_results(eid)
        st = db.get_exam_stats(eid)
        if act == "pdf" and res:
            path = export_results_pdf(ex, res, st, get_user_temp_dir(update.effective_user.id) / f"{eid}.pdf")
            with open(path, "rb") as f:
                await update.callback_query.message.reply_document(f)
        elif act == "xls" and res:
            path = export_results_csv(ex, res, get_user_temp_dir(update.effective_user.id) / f"{eid}.csv")
            with open(path, "rb") as f:
                await update.callback_query.message.reply_document(f)
        elif ex:
            await update.callback_query.edit_message_text(
                format_creator_stats(ex, st, res), parse_mode="Markdown",
                reply_markup=exam_creator_results_keyboard(eid),
            )

    async def leaderboard(update, context):
        if not await check_channel_subscription(update, context, db):
            return
        b = db.get_leaderboard(10)
        t = "🏆\n" + "\n".join(
            f"{i+1}. {s.get('full_name') or s.get('username')} — {s['points']}"
            for i, s in enumerate(b)
        ) if b else "لا بيانات"
        await update.message.reply_text(t)

    async def my_scores(update, context):
        if not await check_channel_subscription(update, context, db):
            return
        rs = db.get_user_results(update.effective_user.id)
        t = "\n".join(f"{r['title']}: {r['score']}/{r['total']}" for r in rs) if rs else "لا نتائج"
        await update.message.reply_text(t)

    create_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ إنشاء امتحان$"), create_start)],
        states={
            states.EXAM_CREATE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_title)],
            states.EXAM_CREATE_DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_duration_text),
                CallbackQueryHandler(set_duration_btn, pattern=r"^pdur_\d+$"),
            ],
            states.EXAM_BULK_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bulk),
                MessageHandler(filters.Document.ALL, receive_bulk),
                CallbackQueryHandler(review_cb, pattern=r"^eqb_"),
                CallbackQueryHandler(publish, pattern="^pq_done$"),
            ],
            states.EXAM_EDIT_ONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_one_question),
                CallbackQueryHandler(review_cb, pattern=r"^eqb_"),
            ],
            states.EXAM_Q_IMAGE: [
                MessageHandler(filters.PHOTO, add_q_image),
                CallbackQueryHandler(review_cb, pattern=r"^eqb_"),
            ],
            states.EXAM_AFTER_SAVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bulk),
                MessageHandler(filters.Document.ALL, receive_bulk),
                CallbackQueryHandler(review_cb, pattern=r"^eqb_"),
                CallbackQueryHandler(publish, pattern="^pq_done$"),
            ],
        },
        fallbacks=[MessageHandler(filters.Regex("^🔙 القائمة الرئيسية$"), back_to_main)],
        allow_reentry=True,
    )

    solve_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^▶️ حل امتحان$"), solve_prompt),
            MessageHandler(filters.Regex(r"^/exam\s+\w+"), solve_code),
        ],
        states={states.EXAM_WAIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, solve_code)]},
        fallbacks=[MessageHandler(filters.Regex("^🔙 القائمة الرئيسية$"), back_to_main)],
    )

    return [
        create_conv, solve_conv,
        PollAnswerHandler(on_poll_answer),
        CallbackQueryHandler(exam_start, pattern=r"^exam_start_\w+$"),
        CallbackQueryHandler(results_cb, pattern=r"^er:"),
        MessageHandler(filters.Regex("^📝 الامتحانات$"), enter),
        MessageHandler(filters.Regex("^📊 نتائج امتحاناتي$"), my_exams),
        MessageHandler(filters.Regex("^🏆 ترتيب الطلاب$"), leaderboard),
        MessageHandler(filters.Regex("^📋 نتائجي$"), my_scores),
    ]


async def begin_exam_from_id(update, context, db, exam_id):
    return await show_exam_entry(update, context, db, exam_id)
