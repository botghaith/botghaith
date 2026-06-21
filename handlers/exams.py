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
from utils.helpers import format_percentage, generate_exam_id, get_user_temp_dir, build_exam_link, parse_exam_id_from_start
from utils.keyboards import exam_menu, exam_start_keyboard, exam_creator_results_keyboard, MAIN_MENU
from utils import states

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
        context.user_data["poll_exam"] = {"title": "", "duration": 30, "questions": [], "q": {}}
        await update.message.reply_text("📊 **سؤال الاستطلاع**\n\nأرسل **عنوان الامتحان**:", parse_mode="Markdown")
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
            f"✅ الزمن: **{d} دقيقة** لكل الامتحان\n\n"
            f"**السؤال 1:** أرسل نص السؤال أو صورة 🖼️:",
            parse_mode="Markdown",
        )
        return states.EXAM_Q_TEXT

    async def set_duration_btn(update, context):
        await update.callback_query.answer()
        d = int(update.callback_query.data.replace("pdur_", ""))
        context.user_data["poll_exam"]["duration"] = d
        await update.callback_query.edit_message_text(
            f"✅ الزمن: **{d} دقيقة** لكل الامتحان\n\n"
            f"**السؤال 1:** أرسل نص السؤال أو صورة 🖼️:",
            parse_mode="Markdown",
        )
        return states.EXAM_Q_TEXT

    async def create_q_text(update, context):
        pe = context.user_data["poll_exam"]
        q = pe.setdefault("q", {})

        if update.message.photo:
            q["image"] = update.message.photo[-1].file_id
            if update.message.caption:
                q["question"] = update.message.caption.strip()
        else:
            q["question"] = update.message.text.strip()

        if not q.get("question") and not q.get("image"):
            await update.message.reply_text("❌ أرسل نصاً أو صورة.")
            return states.EXAM_Q_TEXT

        if q.get("image"):
            await update.message.reply_text(
                "**الخيارات:**\n\nأرسل كل خيار في سطر (2 إلى 10):",
                parse_mode="Markdown",
            )
            return states.EXAM_Q_OPTIONS

        await update.message.reply_text(
            "🖼️ أرسل **صورة** للسؤال (اختياري) أو اضغط «التالي»:",
            parse_mode="Markdown",
            reply_markup=_skip_img_kb(),
        )
        return states.EXAM_Q_IMAGE

    async def add_q_image(update, context):
        q = context.user_data["poll_exam"]["q"]
        q["image"] = update.message.photo[-1].file_id
        if update.message.caption and not q.get("question"):
            q["question"] = update.message.caption.strip()
        await update.message.reply_text(
            "✅ تمت إضافة الصورة.\n\n**الخيارات:**\nأرسل كل خيار في سطر:",
            parse_mode="Markdown",
        )
        return states.EXAM_Q_OPTIONS

    async def skip_image(update, context):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "**الخيارات:**\n\nأرسل كل خيار في سطر (2 إلى 10):",
            parse_mode="Markdown",
        )
        return states.EXAM_Q_OPTIONS

    async def create_q_options(update, context):
        opts = [x.strip() for x in update.message.text.strip().split("\n") if x.strip()]
        if len(opts) < 2:
            await update.message.reply_text("❌ خياران على الأقل.")
            return states.EXAM_Q_OPTIONS
        context.user_data["poll_exam"]["q"]["options"] = opts[:10]
        await update.message.reply_text(
            "**الإجابة الصحيحة:**\n\nاضغط الخيار الصحيح:",
            reply_markup=_poll_correct_kb(opts[:10]),
        )
        return states.EXAM_Q_OPTIONS

    async def pick_correct(update, context):
        await update.callback_query.answer()
        i = int(update.callback_query.data.replace("pc_", ""))
        pe = context.user_data["poll_exam"]
        q = pe["q"]
        q["correct_index"] = i
        pe["questions"].append(q)
        pe["q"] = {}

        chat = update.effective_chat.id
        if q.get("image"):
            await context.bot.send_photo(chat, q["image"])
        await context.bot.send_poll(
            chat, q.get("question") or "اختر الإجابة:",
            [_trim(o, 100) for o in q["options"]],
            type=Poll.QUIZ, correct_option_id=i, is_anonymous=False,
        )
        await update.callback_query.edit_message_text("✅ تم")
        await update.callback_query.message.reply_text(
            f"تم حفظ السؤال {len(pe['questions'])}",
            reply_markup=_after_question_kb(),
        )
        return states.EXAM_AFTER_SAVE

    async def more_question(update, context):
        await update.callback_query.answer()
        n = len(context.user_data["poll_exam"]["questions"]) + 1
        await update.callback_query.message.reply_text(
            f"**السؤال {n}:**\n\nأرسل نص السؤال أو صورة 🖼️:",
            parse_mode="Markdown",
        )
        return states.EXAM_Q_TEXT

    async def publish(update, context):
        await update.callback_query.answer()
        pe = context.user_data.get("poll_exam", {})
        if not pe.get("questions"):
            await update.callback_query.answer("❌ أضف سؤالاً!", show_alert=True)
            return ConversationHandler.END
        eid = generate_exam_id()
        uid = update.effective_user.id
        dur = pe.get("duration", 30)
        db.create_exam(eid, pe["title"], pe["questions"], dur, uid, is_published=True)
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
            states.EXAM_Q_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_q_text),
                MessageHandler(filters.PHOTO, create_q_text),
            ],
            states.EXAM_Q_IMAGE: [
                MessageHandler(filters.PHOTO, add_q_image),
                CallbackQueryHandler(skip_image, pattern="^pq_skip_img$"),
            ],
            states.EXAM_Q_OPTIONS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_q_options),
                CallbackQueryHandler(pick_correct, pattern=r"^pc_\d+$"),
            ],
            states.EXAM_AFTER_SAVE: [
                CallbackQueryHandler(more_question, pattern="^pq_more$"),
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
