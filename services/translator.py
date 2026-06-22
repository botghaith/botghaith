import html
import logging
import re
import threading

logger = logging.getLogger(__name__)

_translator_ready = False
_ar_en = None
_en_ar = None
_init_lock = threading.Lock()

CHUNK_SIZE = 450
ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟…:;])\s+|\n+")


def _ensure_translator():
    global _translator_ready, _ar_en, _en_ar
    if _translator_ready:
        return True
    with _init_lock:
        if _translator_ready:
            return True
        try:
            import argostranslate.package
            import argostranslate.translate

            installed = argostranslate.package.get_installed_packages()
            has_ar_en = any(p.from_code == "ar" and p.to_code == "en" for p in installed)
            has_en_ar = any(p.from_code == "en" and p.to_code == "ar" for p in installed)

            if not has_ar_en or not has_en_ar:
                logger.info("Downloading Argos translation packages...")
                argostranslate.package.update_package_index()
                available = argostranslate.package.get_available_packages()
                for from_code, to_code in [("ar", "en"), ("en", "ar")]:
                    if any(p.from_code == from_code and p.to_code == to_code for p in installed):
                        continue
                    pkg = next(
                        (p for p in available if p.from_code == from_code and p.to_code == to_code),
                        None,
                    )
                    if pkg:
                        argostranslate.package.install_from_path(pkg.download())

            _ar_en = argostranslate.translate.get_translation_from_codes("ar", "en")
            _en_ar = argostranslate.translate.get_translation_from_codes("en", "ar")
            if not _ar_en or not _en_ar:
                raise RuntimeError("حزم الترجمة غير متوفرة")
            _translator_ready = True
            logger.info("Argos Translate initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to init translator: {e}")
            _translator_ready = False
            return False


def is_translator_ready() -> bool:
    return _ensure_translator()


def detect_direction(text: str) -> str:
    """ar_en أو en_ar حسب اللغة الغالبة"""
    sample = (text or "")[:3000]
    ar = len(ARABIC_RE.findall(sample))
    en = len(re.findall(r"[A-Za-z]", sample))
    return "ar_en" if ar > en else "en_ar"


def resolve_direction(text: str, direction: str) -> str:
    if direction == "auto":
        return detect_direction(text)
    return direction


def direction_label(direction: str) -> str:
    return "عربي → إنجليزي" if direction == "ar_en" else "إنجليزي → عربي"


def _chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    parts = SENTENCE_SPLIT_RE.split(text)
    chunks, current = [], ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > size:
            if current:
                chunks.append(current.strip())
                current = ""
            for i in range(0, len(part), size):
                chunks.append(part[i:i + size].strip())
            continue
        if len(current) + len(part) + 1 <= size:
            current = f"{current} {part}".strip()
        else:
            if current:
                chunks.append(current.strip())
            current = part
    if current:
        chunks.append(current.strip())
    return chunks or [text[:size]]


def split_units(text: str) -> list[str]:
    """تقسيم النص إلى جمل/فقرات للترجمة الدقيقة"""
    text = (text or "").strip()
    if not text:
        return []
    units = [u.strip() for u in SENTENCE_SPLIT_RE.split(text) if u.strip()]
    if not units:
        return [text]
    result = []
    for unit in units:
        if len(unit) > CHUNK_SIZE:
            result.extend(_chunk_text(unit))
        else:
            result.append(unit)
    return result


def translate_text(text: str, direction: str = "en_ar") -> str:
    text = (text or "").strip()
    if not text:
        return ""
    direction = resolve_direction(text, direction)
    if not _ensure_translator():
        return _fallback_translate(text, direction)

    try:
        engine = _ar_en if direction == "ar_en" else _en_ar
        chunks = _chunk_text(text)
        return "\n".join(engine.translate(c) for c in chunks).strip()
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return _fallback_translate(text, direction)


def translate_units(text: str, direction: str = "en_ar") -> list[tuple[str, str]]:
    """ترجمة وحدة بوحدة: [(أصلي, مترجم), ...]"""
    direction = resolve_direction(text, direction)
    pairs = []
    for unit in split_units(text):
        pairs.append((unit, translate_text(unit, direction)))
    return pairs


def _esc(text: str) -> str:
    return html.escape(text or "")


def format_interleaved_html(text: str, direction: str = "en_ar") -> str:
    """عرض مميز: كل جملة وفوقها/تحتها ترجمتها"""
    pairs = translate_units(text, direction)
    blocks = []
    for src, tr in pairs:
        blocks.append(
            f"<b>{_esc(src)}</b>\n<i>{_esc(tr)}</i>"
        )
    return "\n\n".join(blocks)


def format_full_translation(text: str, direction: str = "en_ar") -> str:
    pairs = translate_units(text, direction)
    return "\n\n".join(tr for _, tr in pairs)


def format_bilingual_plain(text: str, direction: str = "en_ar") -> str:
    """تنسيق ثنائي اللغة للملفات"""
    pairs = translate_units(text, direction)
    lines = []
    for i, (src, tr) in enumerate(pairs, 1):
        lines.append(f"── [{i}] ──")
        lines.append(src)
        lines.append(f"↳ {tr}")
        lines.append("")
    return "\n".join(lines).strip()


def translate_interleaved(text: str, direction: str = "en_ar") -> str:
    return format_interleaved_html(text, direction)


def translate_text_dual(text: str, direction: str = "auto") -> tuple[str, str]:
    """ترجمة واحدة للنص — عرض ثنائي + نص كامل."""
    direction = resolve_direction(text, direction)
    if not _ensure_translator():
        raise RuntimeError("محرك الترجمة غير جاهز على السيرفر")
    pairs = [(unit, translate_text(unit, direction)) for unit in split_units(text)]
    interleaved = "\n\n".join(
        f"<b>{_esc(src)}</b>\n<i>{_esc(tr)}</i>" for src, tr in pairs
    )
    full = "\n\n".join(tr for _, tr in pairs)
    return interleaved, full


def translate_file_content(text: str, direction: str = "en_ar") -> str:
    return format_bilingual_plain(text, direction)


def _fallback_translate(text: str, direction: str) -> str:
    basic = {
        "hello": "مرحباً", "world": "عالم", "student": "طالب",
        "university": "جامعة", "exam": "امتحان", "study": "دراسة",
        "book": "كتاب", "chapter": "فصل", "lesson": "درس",
        "مرحباً": "hello", "طالب": "student", "جامعة": "university",
        "امتحان": "exam", "دراسة": "study", "كتاب": "book",
    }
    words = text.split()
    result = []
    for w in words:
        clean = w.strip(".,!?؟")
        result.append(basic.get(clean, clean))
    return " ".join(result)
