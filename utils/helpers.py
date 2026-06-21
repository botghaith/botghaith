import re
import uuid
from pathlib import Path

from config import TEMP_DIR


def clean_username(username: str) -> str:
    return username.lstrip("@").lower() if username else ""


def generate_exam_id() -> str:
    return uuid.uuid4().hex[:8]


def build_exam_link(bot_username: str, exam_id: str) -> str:
    """رابط يفتح البوت ويبدأ الامتحان — بدون _ في المعامل لتفادي كسر Markdown"""
    return f"https://t.me/{bot_username}?start=e{exam_id}"


def parse_exam_id_from_start(args: list) -> str | None:
    if not args:
        return None
    payload = args[0].strip().lower()
    if payload.startswith("exam_"):
        return payload[5:]
    if payload.startswith("e") and len(payload) == 9:
        return payload[1:]
    if len(payload) == 8 and all(c in "0123456789abcdef" for c in payload):
        return payload
    return None


def get_user_temp_dir(user_id: int) -> Path:
    d = TEMP_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def truncate_text(text: str, max_len: int = 4000) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n\n... (تم الاقتصاص)"


def split_text_chunks(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + max_len])
        start += max_len
    return chunks


def sanitize_text_for_send(text: str) -> str:
    """حذف الرموز التي تسبب مشاكل في الإرسال"""
    if not text:
        return ""
    text = text.replace("\x00", "")
    text = text.encode("utf-8", errors="ignore").decode("utf-8")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    text = re.sub(r"[\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]", "", text)

    cleaned: list[str] = []
    allowed = set(" .,;:!?؟/-–—()[]{}\"'«»+-=_@#$%&*+=<>|\\~`")
    for ch in text:
        if ch in ("\n", "\r", "\t"):
            cleaned.append(ch)
        elif ch.isalnum() or ch in allowed:
            cleaned.append(ch)
        elif "\u0600" <= ch <= "\u06FF" or "\u0750" <= ch <= "\u077F" or "\u08A0" <= ch <= "\u08FF":
            cleaned.append(ch)
        elif ch.isspace():
            cleaned.append(" ")

    result = "".join(cleaned)
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def is_admin(username: str, admin_username: str) -> bool:
    return clean_username(username) == clean_username(admin_username)


def split_sentences(text: str) -> list[str]:
    sentences = re.split(r'(?<=[.!?؟。])\s+', text.strip())
    return [s.strip() for s in sentences if len(s.strip()) > 20]


def extract_keywords(text: str, top_n: int = 10) -> list[str]:
    words = re.findall(r'[\w\u0600-\u06FF]{4,}', text.lower())
    stop_words = {
        "this", "that", "with", "from", "have", "been", "were", "which",
        "their", "there", "about", "would", "could", "should", "these",
        "those", "through", "during", "before", "after", "above", "below",
        "between", "under", "again", "further", "then", "once", "here",
        "when", "where", "why", "how", "all", "each", "every", "both",
        "few", "more", "most", "other", "some", "such", "only", "own",
        "same", "than", "too", "very", "just", "because", "also", "into",
        "over", "after", "being", "the", "and", "for", "are", "but", "not",
        "you", "all", "can", "had", "her", "was", "one", "our", "out",
        "في", "من", "على", "إلى", "أن", "هذا", "هذه", "التي", "الذي",
        "كان", "كانت", "هو", "هي", "مع", "عن", "لم", "قد", "ما", "لا",
    }
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop_words:
            freq[w] = freq.get(w, 0) + 1
    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in sorted_words[:top_n]]


def format_percentage(value: float) -> str:
    return f"{value:.1f}%"
