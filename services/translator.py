import html
import json
import logging
import re
import threading
import urllib.parse
import urllib.request

import config  # noqa: F401 — ARGOS_PACKAGES_DIR قبل argostranslate
from services.field_context import apply_field_terms, use_detected_field
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


_online_cache: dict[tuple[str, str, bool], str] = {}
_gap_cache: dict[tuple[str, str], str] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 400
# أدوات التعريف تُحذف إذا التصقت بالعربي؛ ترجمتها وحدها تكسر ترتيب الجملة.
_GAP_SKIP_EN = frozenset({"of", "the", "a", "an"})


def _content_token_count(text: str, direction: str) -> int:
    pattern = _LATIN_TOKEN if direction == "en_ar" else _AR_TOKEN
    return len(pattern.findall(text or ""))


def _leftover_count(source: str, translated: str, direction: str) -> int:
    """أقل رقم يعني تغطية أفضل. النص المنسوخ أو الفارغ يُستبعد."""
    if not (translated or "").strip():
        return 10**6
    if _copied_source(translated, source, direction):
        return 10**6
    return len(_leftover_words(source, translated, direction))


def _choose_translation(
    source: str,
    direction: str,
    current: str | None,
    candidate: str | None,
    *,
    replace_on_tie: bool = False,
) -> str | None:
    """يبقي الجملة الأغطى. عند التعادل تُفضَّل خدمة أدق إذا طُلب ذلك."""
    if not (candidate or "").strip():
        return current
    if _leftover_count(source, candidate, direction) >= 10**6:
        return current
    if current is None:
        return candidate
    new_left = _leftover_count(source, candidate, direction)
    old_left = _leftover_count(source, current, direction)
    if new_left < old_left or (replace_on_tie and new_left == old_left):
        return candidate
    return current


def _skip_gap_word(word: str, direction: str) -> bool:
    return direction == "en_ar" and word.casefold() in _GAP_SKIP_EN


def _online_translate(text: str, direction: str, *, deepen: bool = True) -> str:
    """ترجمة مجانية.

    الجملة الكاملة تُترجم أولاً حتى يبقى السياق والترتيب.
    إذا بقيت كلمات، تُجرَّب خدمة أخرى مرة واحدة ثم نختار الأغطى.
    deepen=False يكتفي بأول خدمة نجحت — لترقيع كلمة بدون ضغط على الخدمات.
    """
    text = (text or "").strip()
    if not text:
        return ""
    key = (direction, text, deepen)
    with _cache_lock:
        cached = _online_cache.get(key)
        if cached is None and not deepen:
            cached = _online_cache.get((direction, text, True))
    if cached is not None:
        return cached

    result = _online_translate_fetch(text, direction, deepen=deepen)
    with _cache_lock:
        if len(_online_cache) >= _CACHE_MAX:
            _online_cache.clear()
        _online_cache[key] = result
    return result


def _online_translate_fetch(text: str, direction: str, *, deepen: bool) -> str:
    errors: list[str] = []
    unchanged = False
    best: str | None = None
    best_left = 10**6
    google_done = False

    def consider(name: str, result: str) -> int | None:
        nonlocal best, best_left, unchanged
        result = (result or "").strip()
        if not result:
            errors.append(f"{name}: empty result")
            return None
        if "MYMEMORY WARNING" in result.upper():
            errors.append(f"{name}: rate limited")
            return None
        if result.upper() == text.upper() or _texts_match(result, text):
            unchanged = True
            errors.append(f"{name}: unchanged text")
            return None
        left = len(_leftover_words(text, result, direction))
        if left < best_left:
            logger.info("Online translation via %s (leftover %s)", name, left)
            best, best_left = result, left
        return left

    def run(name: str, fn) -> int | None:
        try:
            return consider(name, fn(text, direction))
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            logger.warning("Translation provider %s failed: %s", name, exc)
            return None

    left = run("lingva", _lingva_translate)
    google_done = left is not None
    if left == 0:
        return best or text

    # جوجل المباشر نفس محرك Lingva. يُستدعى فقط إذا المرايا فشلت، حتى ما نضغط الخدمة مرتين.
    if not google_done:
        left = run("google", _google_translate)
        if left == 0:
            return best or text

    # محرك ثانٍ مختلف، ثم MyMemory، فقط إذا الجملة ناقصة أو ما عندنا نتيجة.
    if best is None or (deepen and best_left > 0):
        left = run("libre", _libre_translate)
        if left == 0:
            return best or text
    if best is None or (deepen and best_left > 0):
        left = run("mymemory", _mymemory_translate)
        if left == 0:
            return best or text

    if best is not None:
        return best
    if unchanged:
        logger.debug("Keeping original text (providers returned unchanged): %r", text[:80])
        return text
    raise RuntimeError("فشلت الترجمة: " + (errors[-1] if errors else "لا توجد خدمة متاحة"))


_LIBRE_URLS = (
    "https://libretranslate.com/translate",
    "https://translate.fedilab.app/translate",
    "https://libretranslate.de/translate",
)
_libre_off = False


def _libre_translate(text: str, direction: str) -> str:
    """محرك مجاني مختلف عن جوجل. يُستدعى فقط إذا الجملة ناقصة، ويتوقف إذا الخوادم واقفة."""
    global _libre_off
    if _libre_off:
        raise RuntimeError("LibreTranslate unavailable")
    src, tgt = _lang_codes(direction)
    payload = json.dumps({
        "q": text[:4500],
        "source": src,
        "target": tgt,
        "format": "text",
    }).encode()
    last_err = "LibreTranslate unavailable"
    for url in _LIBRE_URLS:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; botghaith/1.0)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            translated = (data.get("translatedText") or "").strip()
            if translated:
                return translated
            last_err = f"{url}: empty response"
        except Exception as exc:
            last_err = f"{url}: {exc}"
    _libre_off = True
    raise RuntimeError(last_err)


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


def _online_translate_long(text: str, direction: str, *, deepen: bool = True) -> str:
    if len(text) <= CHUNK_SIZE:
        return _online_translate(text, direction, deepen=deepen)
    parts = []
    for chunk in _chunk_text(text):
        try:
            parts.append(_online_translate(chunk, direction, deepen=deepen))
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
    """جملة كاملة من أدق محرك مجاني.

    الأونلاين أولاً لأنه أدق. إذا بقيت كلمات تُقارن محركات إضافية داخل الطلب.
    Argos المحلي يُستخدم إذا الأونلاين فشل أو غطى كلمات أقل.
    """
    errors: list[str] = []
    best: str | None = None
    echoed = False

    def consider(candidate: str, *, replace_on_tie: bool = False) -> int:
        nonlocal best, echoed
        if _copied_source(candidate, original, direction):
            echoed = True
            return 10**6
        chosen = _choose_translation(
            original, direction, best, candidate, replace_on_tie=replace_on_tie,
        )
        if chosen and chosen != best:
            best = chosen
        elif best is None and chosen:
            best = chosen
        return _leftover_count(original, candidate, direction)

    try:
        left = consider(
            _online_translate_long(prepared, direction, deepen=True),
            replace_on_tie=True,
        )
        if left == 0 and best is not None:
            return best
    except Exception as exc:
        errors.append(str(exc))
        logger.warning("Online translation failed, trying Argos fallback: %s", exc)

    if best is None or _leftover_count(original, best, direction) > 0:
        if _ensure_translator():
            try:
                consider(_argos_translate(prepared, direction))
            except Exception as exc:
                errors.append(str(exc))
                logger.warning("Argos translation failed: %s", exc)

    if best is not None:
        return best
    if echoed:
        return original
    detail = errors[-1] if errors else "لا توجد خدمة متاحة"
    raise RuntimeError(f"تعذرت الترجمة: {detail}")


_EN_PREP_AR = {
    "of": "من",
    "in": "في",
    "on": "على",
    "at": "في",
    "to": "إلى",
    "for": "ل",
    "from": "من",
    "with": "مع",
    "by": "ب",
    "into": "إلى",
    "over": "فوق",
    "under": "تحت",
    "about": "عن",
    "between": "بين",
    "through": "خلال",
    "during": "خلال",
    "without": "بدون",
    "within": "ضمن",
    "upon": "على",
    "per": "لكل",
}
_CLOSED_PHRASE_RE = re.compile(
    r"^(?:(?P<prep>of|in|on|at|to|for|from|with|by|into|over|under|about|between|through|during|without|within|upon|per)\s+)?"
    r"(?:(?P<article>the|a|an)\s+)?"
    r"(?P<head>[A-Za-z][\w'-]*)$",
    re.IGNORECASE,
)


def _arabic_definite(phrase: str) -> str:
    parts = (phrase or "").split()
    if not parts:
        return phrase
    head = parts[0]
    if head.startswith(("ال", "لل", "بال")):
        return phrase
    parts[0] = "ال" + head
    return " ".join(parts)


def _bind_arabic_prep(prep: str, phrase: str) -> str:
    # لِ + ال = لل (تسقط الألف). بِ + ال = بال (تبقى الألف).
    if prep == "ل" and phrase.startswith("ال"):
        return "ل" + phrase[1:]
    if prep == "ب" and phrase.startswith("ال"):
        return "ب" + phrase
    return f"{prep} {phrase}".strip()


_OF_SKIP_HEAD = frozenset({
    "of", "in", "on", "at", "to", "for", "from", "with", "by", "as",
    "into", "onto", "over", "under", "about", "between", "through",
    "during", "without", "within", "upon", "per",
    "the", "a", "an", "is", "are", "was", "were", "and", "or", "but",
})
_OF_FOLLOW = re.compile(
    r"\bof\s+(?:(?P<article>the|a|an)\s+)?"
    r"(?P<head>(?!(?:of|in|on|at|to|for|from|with|by|as|into|onto|over|under|"
    r"about|between|through|during|without|within|upon|per|the|a|an|is|are|was|were|and|or|but)\b)"
    r"[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,2})",
    re.IGNORECASE,
)


def _compose_closed_phrase(text: str, direction: str) -> str | None:
    """the flower → الزهرة. of the flower → الزهرة، مو of لحالها."""
    if direction != "en_ar":
        return None
    raw = re.sub(r"\s+", " ", (text or "")).strip(" .،!?؟")
    match = _CLOSED_PHRASE_RE.fullmatch(raw)
    if not match or not match.group("head"):
        return None
    prep = (match.group("prep") or "").lower()
    article = (match.group("article") or "").lower()
    head = match.group("head")
    if not prep and not article:
        return None
    if head.casefold() in _EN_PREP_AR or head.casefold() in {"the", "a", "an"}:
        return None
    noun = translate_text(head, direction).strip()
    if not ARABIC_RE.search(noun):
        return None
    # of ما تنكتب. ترتبط بالكلمة اللي بعدها: of the flower = الزهرة
    definite = article == "the" or (prep == "of" and article not in {"a", "an"})
    if definite:
        noun = _arabic_definite(noun)
    if prep == "of":
        return noun
    if prep:
        noun = _bind_arabic_prep(_EN_PREP_AR[prep], noun)
    return noun


def _fold_of_phrases(text: str, direction: str) -> str:
    """of + الكلمة اللي بعدها تصير عبارة عربية واحدة، وof ما تبقى بالنص."""
    if direction != "en_ar" or not text or not _OF_FOLLOW.search(text):
        return text

    def repl(match: re.Match) -> str:
        article = (match.group("article") or "").lower()
        words = (match.group("head") or "").split()
        kept: list[str] = []
        for word in words:
            if word.casefold() in _OF_SKIP_HEAD:
                break
            kept.append(word)
        if not kept:
            return match.group(0)
        noun = translate_text(" ".join(kept), direction).strip()
        if not ARABIC_RE.search(noun):
            return match.group(0)
        if article not in {"a", "an"}:
            noun = _arabic_definite(noun)
        rest = words[len(kept):]
        tail = f" {' '.join(rest)}" if rest else ""
        return f"{noun}{tail}"

    previous = None
    while previous != text:
        previous = text
        updated = _OF_FOLLOW.sub(repl, text)
        if updated == text:
            break
        text = updated
    return text


def _drop_copied_of(text: str) -> str:
    """إذا of رجعت كما هي، أو انكتبت مرتين، تنحذف وما تنعاد."""
    cleaned = text or ""
    cleaned = re.sub(r"\bof(?:\s+of)+\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bof\s+(?=[\u0600-\u06FF])", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?<=[\u0600-\u06FF])\s+of\b", "", cleaned, flags=re.IGNORECASE)
    if re.fullmatch(r"of", cleaned.strip(), flags=re.IGNORECASE):
        return ""
    cleaned = re.sub(r"\b(?:the|a|an)\s+(?=[\u0600-\u06FF])", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?<=[\u0600-\u06FF])\s+(?:the|a|an)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([،.!?؟:;])", r"\1", cleaned)
    return cleaned.strip()


def _substitute_word(translated: str, word: str, replacement: str) -> str:
    pattern = re.compile(
        rf"(?<![\w\u0600-\u06FF]){re.escape(word)}(?![\w\u0600-\u06FF])",
        re.IGNORECASE,
    )
    return pattern.sub(lambda _match, repl=replacement: repl, translated)


def _span_around(source: str, word: str, direction: str, radius: int = 1):
    """الكلمة مع جارتها حتى تترجم حسب السياق لا وحدها."""
    pattern = _LATIN_TOKEN if direction == "en_ar" else _AR_TOKEN
    matches = list(pattern.finditer(source or ""))
    index = None
    needle = word.casefold() if direction == "en_ar" else word
    for i, match in enumerate(matches):
        key = match.group(0).casefold() if direction == "en_ar" else match.group(0)
        if key == needle:
            index = i
            break
    if index is None:
        return None
    start = max(0, index - radius)
    end = min(len(matches), index + radius + 1)
    span = source[matches[start].start():matches[end - 1].end()]
    return span, index - start


def _replacement_from_window(source: str, word: str, direction: str) -> str | None:
    found = _span_around(source, word, direction)
    if not found:
        return None
    span, index = found
    if _texts_match(span, word):
        return None
    try:
        span_tr = _online_translate(span, direction, deepen=False)
    except Exception:
        return None
    src_tokens = _LATIN_TOKEN.findall(span) if direction == "en_ar" else _AR_TOKEN.findall(span)
    dst_tokens = _AR_TOKEN.findall(span_tr) if direction == "en_ar" else _LATIN_TOKEN.findall(span_tr)
    if index >= len(src_tokens) or len(dst_tokens) != len(src_tokens):
        return None
    candidate = _strip_added_markers(word, dst_tokens[index])
    if not candidate or _texts_match(candidate, word):
        return None
    if direction == "en_ar" and not ARABIC_RE.search(candidate):
        return None
    if direction == "ar_en" and not re.search(r"[A-Za-z]", candidate):
        return None
    return candidate


def _fill_from_batch(translated: str, words: list[str], direction: str, *, thorough: bool) -> str:
    """طلب واحد للكلمات التي رفضتها الجملة، حتى ما نضرب الخدمة لكل كلمة."""
    unique: list[str] = []
    seen: set[str] = set()
    for word in words:
        if _skip_gap_word(word, direction):
            continue
        key = word.casefold() if direction == "en_ar" else word
        if key in seen:
            continue
        seen.add(key)
        unique.append(word)
    if not unique:
        return translated
    payload = "\n".join(f"({index}) {word}" for index, word in enumerate(unique, 1))
    try:
        if thorough:
            result = _online_translate(payload, direction, deepen=True)
        else:
            gap_key = (direction, payload)
            with _cache_lock:
                cached_gap = _gap_cache.get(gap_key)
            if cached_gap is not None:
                result = cached_gap
            else:
                result = _mymemory_translate(payload, direction)
                if "MYMEMORY WARNING" in result.upper():
                    return translated
                with _cache_lock:
                    if len(_gap_cache) >= _CACHE_MAX:
                        _gap_cache.clear()
                    _gap_cache[gap_key] = result
    except Exception as exc:
        logger.warning("Batch gap translation failed: %s", exc)
        return translated
    lines = [ln.strip() for ln in (result or "").splitlines() if ln.strip()]
    if len(lines) != len(unique):
        return translated
    for word, line in zip(unique, lines):
        line = _strip_added_markers(word, line)
        if not line or _texts_match(line, word):
            continue
        if direction == "en_ar" and (_LATIN_TOKEN.search(line) or not ARABIC_RE.search(line)):
            continue
        if direction == "ar_en" and (_AR_TOKEN.search(line) or not re.search(r"[A-Za-z]", line)):
            continue
        translated = _substitute_word(translated, word, line)
    return translated


def _fill_context_gaps(source: str, translated: str, direction: str, *, thorough: bool) -> str:
    """الكلمة التي بقيت تُترجم من مقطعها. إذا فشل المحاذاة، طلب واحد لبقية الكلمات."""
    if _content_token_count(source, direction) <= 1:
        return translated
    leftovers = [w for w in _leftover_words(source, translated, direction) if not _skip_gap_word(w, direction)]
    if not leftovers:
        return translated
    if thorough and len(leftovers) <= 2:
        for word in leftovers:
            replacement = _replacement_from_window(source, word, direction)
            if replacement:
                translated = _substitute_word(translated, word, replacement)
        leftovers = [
            w for w in _leftover_words(source, translated, direction) if not _skip_gap_word(w, direction)
        ]
    if leftovers:
        translated = _fill_from_batch(translated, leftovers[:40], direction, thorough=thorough)
    return translated


def _polish_translation(source: str, translated: str, direction: str, *, thorough: bool) -> str:
    translated = repair_copied(translated, direction)
    translated = apply_field_terms(translated, direction)
    translated = fill_copied_words(source, translated, direction)
    translated = _fill_context_gaps(source, translated, direction, thorough=thorough)
    translated = _strip_added_markers(source, translated)
    if direction == "en_ar":
        translated = _drop_copied_of(translated)
    return translated.strip()


def translate_text(text: str, direction: str = "en_ar") -> str:
    text = (text or "").strip()
    if not text:
        return ""
    direction = resolve_direction(text, direction)

    exact = lookup_preserving(text, direction)
    if exact is not None:
        return exact

    composed = _compose_closed_phrase(text, direction)
    if composed:
        return composed

    prepared = apply_known_terms(text, direction)
    prepared = apply_field_terms(prepared, direction)
    prepared = _fold_of_phrases(prepared, direction)
    translated = _dispatch_translation(prepared, text, direction)
    thorough = not _is_file_translation_mode()
    return _polish_translation(text, translated, direction, thorough=thorough)


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
    with use_detected_field(text):
        return _dual_from_units(text, direction)


def _dual_from_units(text: str, direction: str) -> tuple[str, str]:
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
