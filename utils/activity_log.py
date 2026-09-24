import logging
from typing import Any

logger = logging.getLogger(__name__)

ACTIVITY_LABELS = {
    "start": "بدء البوت",
    "translate_text": "ترجمة نص",
    "translate_file": "ترجمة ملف",
    "translate_image": "ترجمة صورة",
    "exam_start": "بدء امتحان",
    "exam_finish": "إنهاء امتحان",
    "exam_create": "إنشاء امتحان",
    "view_profile": "عرض الحساب",
    "pdf_img_to_pdf": "صورة → PDF",
    "pdf_to_img": "PDF → صورة",
    "word_to_pdf": "Word → PDF",
    "pdf_to_word": "PDF → Word",
    "pdf_merge": "دمج PDF",
    "pdf_split": "تقسيم PDF",
    "pdf_compress": "ضغط PDF",
    "pdf_extract": "استخراج نص",
    "pdf_reorder": "إعادة ترتيب PDF",
    "pdf_slides_nup": "دمج سلايدات",
    "report_cover_pdf": "غلاف تقرير PDF",
    "report_cover_docx": "غلاف تقرير Word",
}


def log_user_activity(db: Any, user_id: int, action: str, details: str = "") -> None:
    try:
        db.log_activity(user_id, action, (details or "")[:500])
    except Exception as e:
        logger.warning("Activity log failed (%s): %s", action, e)


def format_activity_line(record: dict) -> str:
    name = (record.get("full_name") or "").strip() or "—"
    uname = (record.get("username") or "").strip().lstrip("@")
    handle = f" @{uname}" if uname else ""
    uid = record.get("user_id")
    identity = f"{name}{handle}"
    if uid:
        identity = f"{identity} ({uid})"
    action = ACTIVITY_LABELS.get(record.get("action", ""), record.get("action", ""))
    detail = f" — {record['details']}" if record.get("details") else ""
    ts = str(record.get("created_at") or "")[:16].replace("T", " ")
    when = f"[{ts}] " if ts else ""
    return f"• {when}{identity}: {action}{detail}"
