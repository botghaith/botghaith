"""بطاقات محرر الأسئلة التفاعلي"""
from services.exam_ui import option_label


def format_live_question_card(q: dict, q_num: int, allow_multi: bool = False) -> str:
    """بطاقة السؤال أثناء التحرير — مثل Poll"""
    text = q.get("question") or "_اضغط «نص السؤال» للكتابة_"
    images = q.get("images") or []
    options = q.get("options") or []
    correct = set(q.get("correct_indices") or (
        [q["correct_index"]] if q.get("correct_index", -1) >= 0 else []
    ))

    lines = [
        f"╔══ 📋 **سؤال {q_num}** ══╗",
        f"📝 {text[:200]}",
        f"🖼️ صور: {len(images)}",
        "├──────────────────────┤",
        f"الخيارات ({len(options)}/10):",
    ]

    if not options:
        lines.append("  _اضغط «إضافة خيار»_")
    else:
        for i, opt in enumerate(options):
            mark = "☑" if i in correct else "☐"
            tag = " ✅" if i in correct else ""
            lines.append(f"  {mark} {option_label(i)}) {opt}{tag}")

    if allow_multi and options:
        lines.append("🔀 وضع إجابات متعددة مفعّل")
    elif options:
        lines.append("👆 اضغط الخيار لتحديد الإجابة الصحيحة")

    lines.append("╚══════════════════════╝")
    return "\n".join(lines)


def format_editor_toolbar(exam: dict) -> str:
    n = len(exam.get("questions", []))
    return (
        f"📝 **{exam.get('title', '')}**\n"
        f"❓ {n} سؤال │ ⏱ {exam.get('duration', 30)} د │ "
        f"🔀 {'متعدد ✅' if exam.get('allow_multiple') else 'إجابة واحدة'}"
    )
