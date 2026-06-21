"""تنسيق واجهات الامتحان (Card UI)"""
from datetime import datetime

LABELS = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"]


def option_label(i: int) -> str:
    return LABELS[i] if i < len(LABELS) else str(i + 1)


def format_question_card(question: dict, index: int, editing: bool = False) -> str:
    """صندوق السؤال — يشبه واجهة Poll"""
    q_text = question.get("question") or "📷 سؤال بالصورة"
    images = question.get("images") or []
    if question.get("image") and question["image"] not in images:
        images = [question["image"]] + images

    header = f"{'✏️' if editing else '📋'} **السؤال {index + 1}**"
    lines = [header, "┌" + "─" * 28 + "┐", f"│ {q_text[:200]}", "│"]

    options = question.get("options", [])
    correct = question.get("correct_index", -1)

    for i, opt in enumerate(options):
        mark = "☑" if i == correct else "☐"
        suffix = " ← **الإجابة الصحيحة**" if i == correct else ""
        lines.append(f"│ {mark} {option_label(i)}) {opt}{suffix}")

    if not options:
        lines.append("│ _لم تُضف خيارات بعد_")

    if images:
        lines.append(f"│ 🖼️ {len(images)} صورة مرفقة")

    if question.get("notes"):
        lines.append(f"│ 📝 ملاحظة: {question['notes'][:80]}")

    lines.append("└" + "─" * 28 + "┘")
    return "\n".join(lines)


def format_exam_setup_card(exam: dict) -> str:
    """بطاقة إعداد الامتحان"""
    expiry = exam.get("expires_at") or "غير محدد"
    desc = exam.get("description") or "—"
    multi = "نعم ✅" if exam.get("allow_multiple") else "لا"
    q_count = len(exam.get("questions", []))

    return (
        "╔══════════════════════════╗\n"
        "║   📝 **إنشاء امتحان**    ║\n"
        "╠══════════════════════════╣\n"
        f"║ 📌 العنوان: {exam.get('title', '—')[:25]}\n"
        f"║ 📄 الوصف: {desc[:25]}\n"
        f"║ ⏱ المدة: {exam.get('duration', 30)} دقيقة\n"
        f"║ 📅 الانتهاء: {str(expiry)[:16]}\n"
        f"║ 🔀 متعدد الإجابات: {multi}\n"
        f"║ ❓ الأسئلة: {q_count}\n"
        "╚══════════════════════════╝"
    )


def format_exam_preview_for_student(exam: dict) -> str:
    """بطاقة دخول الطالب للامتحان"""
    desc = exam.get("description") or ""
    expiry = ""
    if exam.get("expires_at"):
        expiry = f"\n📅 آخر موعد: {exam['expires_at']}"

    return (
        "╔════════════════════════════╗\n"
        f"║  🎯 **{exam['title']}**\n"
        "╠════════════════════════════╣\n"
        + (f"║ {desc[:40]}\n" if desc else "")
        + f"║ ❓ عدد الأسئلة: {len(exam['questions'])}\n"
        + f"║ ⏱ المدة: {exam['duration_minutes']} دقيقة\n"
        + (f"║{expiry}\n" if expiry else "")
        + "╠════════════════════════════╣\n"
        "║ اضغط **بدء الامتحان** للبدء\n"
        "╚════════════════════════════╝"
    )


def format_student_result(exam_title: str, correct: int, wrong: int, total: int,
                          percentage: float, finished_at: str) -> str:
    grade = (
        "ممتاز! 🏆" if percentage >= 90 else
        "جيد جداً! 👍" if percentage >= 70 else
        "جيد 📚" if percentage >= 50 else "تحتاج مراجعة 📖"
    )
    return (
        "╔════════════════════════════╗\n"
        "║     📊 **نتيجة الامتحان**    ║\n"
        "╠════════════════════════════╣\n"
        f"║ 📝 {exam_title[:30]}\n"
        f"║ ✅ صحيحة: {correct}\n"
        f"║ ❌ خاطئة: {wrong}\n"
        f"║ 📊 المجموع: {total}\n"
        f"║ 📈 النسبة: {percentage:.1f}%\n"
        f"║ 🎯 التقييم: {grade}\n"
        f"║ 🕐 الانتهاء: {finished_at}\n"
        "╚════════════════════════════╝"
    )


def format_creator_stats(exam: dict, stats: dict, results: list) -> str:
    text = (
        f"📊 **نتائج: {exam['title']}**\n\n"
        f"👥 المشاركون: {stats['participants']}\n"
        f"🏆 أعلى درجة: {stats['max_score']:.0f}%\n"
        f"📈 المتوسط: {stats['avg_score']:.1f}%\n"
        f"📉 أدنى درجة: {stats['min_score']:.0f}%\n\n"
        "**ترتيب الطلاب:**\n"
    )
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(results[:15]):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = r.get("full_name") or r.get("username") or f"ID:{r['user_id']}"
        text += f"{medal} {name} — {r['score']}/{r['total']} ({r['percentage']:.0f}%)\n"
    return text


def parse_expiry(text: str) -> str | None:
    """تحويل تاريخ من صيغة: 2026-06-20 14:30"""
    text = text.strip()
    if not text or text in ("تخطي", "-", "لا"):
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return None


def is_exam_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M")
        return datetime.now() > exp
    except ValueError:
        return False
