"""تحليل أسئلة اختيار من متعدد دفعة واحدة."""
from __future__ import annotations

import re

LETTER_TO_INDEX = {
    "a": 0, "b": 1, "c": 2, "d": 3, "e": 4,
    "f": 5, "g": 6, "h": 7, "i": 8, "j": 9,
    "أ": 0, "ا": 0, "ب": 1, "ج": 2, "د": 3,
    "ه": 4, "هـ": 4, "و": 5, "ز": 6, "ح": 7, "ط": 8, "ي": 9,
}

INDEX_TO_LETTER = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]

_OPTION_RE = re.compile(
    r"^\s*([A-Ja-j]|أ|ا|ب|ج|د|هـ|ه|و|ز|ح|ط|ي)\s*[\)\]\.\:：\-=]\s*(.*)$"
)
_ANSWER_RE = re.compile(
    r"^\s*(?:الجواب|الإجابة|الاجابة|answer|correct)?\s*[:：\-]?\s*"
    r"([A-Ja-j]|أ|ا|ب|ج|د|هـ|ه|و|ز|ح|ط|ي)\s*$",
    re.IGNORECASE,
)
_QNUM_RE = re.compile(
    r"^(?:س(?:ؤال)?\s*)?\d+\s*[\.\)\-:：]\s*",
    re.IGNORECASE,
)


def letter_index(token: str) -> int | None:
    t = (token or "").strip()
    if t.endswith("ـ") and t != "هـ":
        t = t.rstrip("ـ")
    return LETTER_TO_INDEX.get(t) if t in LETTER_TO_INDEX else LETTER_TO_INDEX.get(t.lower())


def option_letter(index: int) -> str:
    if 0 <= index < len(INDEX_TO_LETTER):
        return INDEX_TO_LETTER[index]
    return str(index + 1)


def _is_option(line: str) -> bool:
    return bool(_OPTION_RE.match(line or ""))


def _is_answer(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if _is_option(s):
        return False
    return bool(_ANSWER_RE.match(s))


def parse_mcq_batch(text: str) -> tuple[list[dict], list[str]]:
    """
    صيغة كل سؤال:
        نص السؤال
        A) الخيار
        B) الخيار
        C) الخيار
        D) الخيار
        E) الخيار
        B
    السطر الأخير حرف الجواب فقط.
    """
    questions: list[dict] = []
    errors: list[str] = []
    if not (text or "").strip():
        return questions, ["النص فارغ."]

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    qn = 0
    n = len(lines)

    def skip_blank():
        nonlocal i
        while i < n and not lines[i].strip():
            i += 1

    while i < n:
        skip_blank()
        if i >= n:
            break

        q_lines: list[str] = []
        while i < n:
            raw = lines[i]
            if not raw.strip():
                if q_lines:
                    i += 1
                    continue
                i += 1
                continue
            if _is_option(raw):
                break
            if q_lines and _is_answer(raw):
                break
            q_lines.append(raw.strip())
            i += 1

        if q_lines:
            q_lines[0] = _QNUM_RE.sub("", q_lines[0]).strip() or q_lines[0]
        question = "\n".join(q_lines).strip()

        opts_by_idx: dict[int, str] = {}
        while i < n and (not lines[i].strip() or _is_option(lines[i])):
            if not lines[i].strip():
                i += 1
                continue
            m = _OPTION_RE.match(lines[i])
            i += 1
            if not m:
                continue
            idx = letter_index(m.group(1))
            if idx is None:
                continue
            opts_by_idx[idx] = (m.group(2) or "").strip()

        skip_blank()
        answer_idx = None
        if i < n and _is_answer(lines[i]):
            m = _ANSWER_RE.match(lines[i].strip())
            i += 1
            if m:
                answer_idx = letter_index(m.group(1))

        qn += 1
        if not question:
            errors.append(f"سؤال {qn}: لا يوجد نص.")
            continue
        if len(opts_by_idx) < 2:
            errors.append(f"سؤال {qn}: تحتاج خيارين على الأقل (A/B/...).")
            continue
        max_idx = max(opts_by_idx)
        options = []
        for k in range(max_idx + 1):
            val = opts_by_idx.get(k, "").strip()
            options.append(val or option_letter(k))
        if len(options) > 10:
            options = options[:10]
        if answer_idx is None:
            errors.append(f"سؤال {qn}: ضع حرف الجواب الصحيح وحده في سطر (مثال: B).")
            continue
        if not 0 <= answer_idx < len(options):
            errors.append(f"سؤال {qn}: حرف الجواب لا يطابق الخيارات.")
            continue

        questions.append({
            "question": question,
            "options": options,
            "correct_index": answer_idx,
        })

    if not questions and not errors:
        errors.append("لم يُعثر على أسئلة بالصيغة المطلوبة.")
    return questions, errors


def format_question_block(q: dict, num: int) -> str:
    letter = option_letter(q.get("correct_index", -1))
    lines = [f"{num}. {q.get('question') or '—'}", ""]
    for i, opt in enumerate(q.get("options") or []):
        mark = " ✅" if i == q.get("correct_index") else ""
        lines.append(f"{option_letter(i)}) {opt}{mark}")
    lines.append("")
    lines.append(letter)
    if q.get("image"):
        lines.append("🖼️ مرفقة صورة")
    return "\n".join(lines)


EXAMPLE_MCQ = (
    "ما عاصمة العراق؟\n"
    "A) بغداد\n"
    "B) البصرة\n"
    "C) أربيل\n"
    "D) الموصل\n"
    "E) النجف\n"
    "A\n"
    "\n"
    "2 + 2 = ؟\n"
    "A) 3\n"
    "B) 4\n"
    "C) 5\n"
    "D) 6\n"
    "E) 7\n"
    "B"
)
