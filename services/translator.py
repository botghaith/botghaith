import html
import json
import logging
import re
import threading
import urllib.parse
import urllib.request

import config  # noqa: F401 — ARGOS_PACKAGES_DIR قبل argostranslate
from config import use_online_translate, prefer_local_for_files
from services.scientific_glossary import (
    apply_known_terms,
    lookup_preserving,
    repair_copied,
)

logger = logging.getLogger(__name__)

_translator_ready = False
_ar_en = None
_en_ar = None
_init_lock = threading.Lock()
_file_mode = threading.local()


def set_file_translation_mode(enabled: bool) -> None:
    _file_mode.enabled = enabled


def _is_file_translation_mode() -> bool:
    return bool(getattr(_file_mode, "enabled", False))


def _argos_translate(text: str, direction: str) -> str:
    engine = _ar_en if direction == "ar_en" else _en_ar
    chunks = _chunk_text(text)
    return "\n".join(engine.translate(c) for c in chunks).strip()

CHUNK_SIZE = 450
ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟…:;])\s+|\n+")

# خوادم Lingva (مرآة Google Translate) — تعمل جيداً على Render
LINGVA_INSTANCES = [
    "https://lingva.ml",
    "https://lingva.garudalinux.org",
    "https://translate.plausibility.cloud",
]


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
    return "إنجليزي → عربي" if direction == "ar_en" else "عربي → إنجليزي"


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


def _lang_codes(direction: str) -> tuple[str, str]:
    return ("ar", "en") if direction == "ar_en" else ("en", "ar")


def _http_get_json(url: str, timeout: float = 25) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; botghaith/1.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _lingva_translate(text: str, direction: str) -> str:
    src, tgt = _lang_codes(direction)
    encoded = urllib.parse.quote(text[:5000], safe="")
    last_err = None
    for base in LINGVA_INSTANCES:
        url = f"{base.rstrip('/')}/api/v1/{src}/{tgt}/{encoded}"
        try:
            data = _http_get_json(url)
            translated = (data.get("translation") or "").strip()
            if translated:
                return translated
            last_err = f"{base}: empty response"
        except Exception as e:
            last_err = f"{base}: {e}"
    raise RuntimeError(last_err or "Lingva unavailable")


def _google_translate(text: str, direction: str) -> str:
    from deep_translator import GoogleTranslator

    src, tgt = _lang_codes(direction)
    return GoogleTranslator(source=src, target=tgt).translate(text)


def _provider_name(fn) -> str:
    return fn.__name__.removeprefix("_").removesuffix("_translate")


def _online_translate(text: str, direction: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    errors: list[str] = []
    unchanged_results: list[str] = []
    providers = (_lingva_translate, _google_translate, _mymemory_translate)
    for fn in providers:
        name = _provider_name(fn)
        try:
            result = fn(text, direction).strip()
            if not result:
                errors.append(f"{name}: empty result")
                continue
            if result.upper() == text.upper():
                unchanged_results.append(result)
                errors.append(f"{name}: unchanged text")
                continue
            if "MYMEMORY WARNING" in result.upper():
                errors.append(f"{name}: rate limited")
                continue
            logger.info("Online translation via %s", name)
            return result
        except Exception as e:
            errors.append(f"{name}: {e}")
            logger.warning("Translation provider %s failed: %s", name, e)

    # أسماء وأرقام وعلامات — المزودات ترجعها كما هي (ليست فشلاً)
    if unchanged_results:
        logger.debug("Keeping original text (providers returned unchanged): %r", text[:80])
        return text

    raise RuntimeError("فشلت الترجمة: " + (errors[-1] if errors else "لا توجد خدمة متاحة"))


def _mymemory_translate(text: str, direction: str) -> str:
    langpair = "ar|en" if direction == "ar_en" else "en|ar"
    url = (
        "https://api.mymemory.translated.net/get?q="
        + urllib.parse.quote(text[:5000])
        + f"&langpair={langpair}"
    )
    data = _http_get_json(url, timeout=30)
    translated = (data.get("responseData") or {}).get("translatedText", "").strip()
    if not translated:
        raise RuntimeError("MyMemory returned empty")
    return translated


def _online_translate_long(text: str, direction: str) -> str:
    if len(text) <= CHUNK_SIZE:
        return _online_translate(text, direction)
    parts = []
    for chunk in _chunk_text(text):
        try:
            parts.append(_online_translate(chunk, direction))
        except Exception as e:
            logger.warning("Chunk translation failed (%r), keeping original: %s", chunk[:40], e)
            parts.append(chunk)
    return "\n".join(parts).strip()


def _texts_match(left: str, right: str) -> bool:
    def _norm(value: str) -> str:
        return " ".join((value or "").split()).casefold()

    return _norm(left) == _norm(right)


def _is_translatable_source(text: str, direction: str) -> bool:
    """نص فيه حروف حقيقية، وليس رقماً أو رمزاً أو رابطاً."""
    if re.search(r"https?://|www\.|@\w", text or "", re.I):
        return False
    if re.fullmatch(r"[\d\s\W_]+", text or "", flags=re.UNICODE):
        return False
    ar = len(ARABIC_RE.findall(text))
    en = len(re.findall(r"[A-Za-z]", text))
    if direction == "en_ar":
        return en >= 2 and en >= ar
    return ar >= 2 and ar >= en


def _copied_source(result: str, source: str, direction: str) -> bool:
    return _texts_match(result, source) and _is_translatable_source(source, direction)


_LATIN_TOKEN = re.compile(r"[A-Za-z]{2,}(?:-[A-Za-z0-9]+)*")
_AR_TOKEN = re.compile(r"[\u0600-\u06FF]{2,}")
_ROMAN = re.compile(r"^(?:i{1,3}|iv|vi{0,3}|ix|xi{0,2}|x)$", re.I)


def _leftover_words(source: str, translated: str, direction: str) -> list[str]:
    """كلمات لغة المصدر التي بقيت كما هي داخل الناتج."""
    if direction == "en_ar":
        source_words = {w.casefold() for w in _LATIN_TOKEN.findall(source or "")}
        pattern = _LATIN_TOKEN
    else:
        source_words = {w for w in _AR_TOKEN.findall(source or "")}
        pattern = _AR_TOKEN
    found: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(translated or ""):
        word = match.group(0)
        key = word.casefold() if direction == "en_ar" else word
        if key in seen or key not in source_words:
            continue
        if direction == "en_ar" and _ROMAN.fullmatch(word):
            continue
        window = (translated or "")[max(0, match.start() - 24):match.start()].lower()
        if "http" in window or "www." in window:
            continue
        seen.add(key)
        found.append(word)
    found.sort(key=len, reverse=True)
    return found


_ADDED_MARKER = re.compile(
    r"^(?:[\(\[]?\d{1,3}[\)\].:\-]\s*|مصطلح علمي\s*[:：]\s*|scientific term\s*[:：]\s*)+",
    re.IGNORECASE,
)


def _line_has_list_index(line: str) -> bool:
    return bool(re.match(r"^[\(\[]?\d{1,3}[\)\].:\-]", (line or "").strip()))


def _strip_added_markers(source: str, translated: str) -> str:
    """يشيل رقم القائمة أو عبارة «مصطلح علمي» إذا المحرك أضافها والنص الأصلي ما بيها."""
    out = (translated or "").strip()
    if not out:
        return out
    src_lines = (source or "").splitlines() or [""]
    out_lines = out.splitlines()
    if len(src_lines) != len(out_lines):
        if _line_has_list_index(src_lines[0]):
            return out
        cleaned = _ADDED_MARKER.sub("", out).strip()
        return cleaned or out
    cleaned_lines: list[str] = []
    for src_line, line in zip(src_lines, out_lines):
        if _line_has_list_index(src_line):
            cleaned_lines.append(line)
            continue
        stripped = _ADDED_MARKER.sub("", line).strip()
        cleaned_lines.append(stripped if stripped else line)
    return "\n".join(cleaned_lines).strip()


def _batch_translate_words(words: list[str], direction: str) -> dict[str, str]:
    """يستبدل الكلمة المنسوخة فقط إذا كانت في القاموس. الكلمة غير المعروفة لا تُترجم وحدها."""
    resolved: dict[str, str] = {}
    for word in words:
        hit = lookup_preserving(word, direction)
        if hit and not _texts_match(hit, word):
            resolved[word.casefold()] = _strip_added_markers(word, hit)
    return resolved


def fill_copied_words(source: str, translated: str, direction: str) -> str:
    """يستبدل كل كلمة بقيت بلغة المصدر بترجمتها."""
    leftovers = _leftover_words(source, translated, direction)
    if not leftovers:
        return translated
    mapping = _batch_translate_words(leftovers, direction)
    if not mapping:
        return translated
    for word in leftovers:
        replacement = mapping.get(word.casefold())
        if not replacement:
            continue
        pattern = re.compile(rf"(?<![\w\u0600-\u06FF]){re.escape(word)}(?![\w\u0600-\u06FF])", re.I)
        translated = pattern.sub(lambda _match, repl=replacement: repl, translated)
    if translated != source:
        logger.info("Filled %d untranslated words", len(mapping))
    return translated


def _dispatch_translation(prepared: str, original: str, direction: str) -> str:
    """أونلاين أدق للجمل العلمية. Argos يبقى للملف العام، ويُستبدل إذا نسخ النص."""
    prefer_fast_local = (
        _is_file_translation_mode()
        and prefer_local_for_files()
        and prepared == original
    )
    errors: list[str] = []
    local_copy: str | None = None
    online_tried = False

    if prefer_fast_local and _ensure_translator():
        try:
            local = _argos_translate(prepared, direction)
            if not _copied_source(local, original, direction):
                return local
            local_copy = local
            logger.info("Local engine copied the text, trying online")
        except Exception as e:
            errors.append(str(e))
            logger.warning("Local file translation failed, trying online: %s", e)

    # حتى مع وضع أوفلاين: إذا النص رجع كما هو نحاول أونلاين، وإلا تبقى الكلمات بلا ترجمة
    if use_online_translate() or local_copy is not None:
        online_tried = True
        try:
            return _online_translate_long(prepared, direction)
        except Exception as e:
            errors.append(str(e))
            logger.warning("Online translation failed, trying Argos fallback: %s", e)

    if _ensure_translator() and local_copy is None:
        try:
            local = _argos_translate(prepared, direction)
            if not _copied_source(local, original, direction):
                return local
            local_copy = local
            logger.info("Argos copied the text, trying online")
        except Exception as e:
            errors.append(str(e))
            logger.warning("Argos translation failed: %s", e)

    if local_copy is not None and not online_tried:
        try:
            return _online_translate_long(prepared, direction)
        except Exception as e:
            errors.append(str(e))
            logger.warning("Online translation failed after copied text: %s", e)

    if local_copy is not None:
        return local_copy

    detail = errors[-1] if errors else "لا توجد خدمة متاحة"
    raise RuntimeError(f"تعذرت الترجمة: {detail}")


def translate_text(text: str, direction: str = "en_ar") -> str:
    text = (text or "").strip()
    if not text:
        return ""
    direction = resolve_direction(text, direction)

    exact = lookup_preserving(text, direction)
    if exact is not None:
        return exact

    prepared = apply_known_terms(text, direction)
    translated = _dispatch_translation(prepared, text, direction)
    translated = repair_copied(translated, direction)
    translated = fill_copied_words(text, translated, direction)
    return _strip_added_markers(text, translated)


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
