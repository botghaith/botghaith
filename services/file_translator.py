"""
ترجمة الملفات بأربعة أنماط:
1) حرفي — كل كلمة بسطر وحدها مع ترجمتها
2) بنفس الترتيب — نفس هيكل الملف مترجماً بالكامل
3) سطر بسطر — كل فقرة وترجمتها تحتها
4) فوق الكلمات — نفس الملف مع ترجمة صغيرة فوق كل كلمة
"""
import logging
import os
import re
import shutil
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.text.run import Run

from services.file_extractor import extract_text_from_file
from services.pdf_service import create_bilingual_pdf, create_pairs_pdf, create_literal_pdf
from services.field_context import use_detected_field
from services.translator import (
    translate_text,
    resolve_direction,
    set_file_translation_mode,
    is_translator_ready,
)
from config import use_fast_file_translation, prefer_local_for_files, is_render_host, file_max_paragraphs, use_dual_file_translation, use_full_file_translation
from services.text_shape import (
    is_mostly_arabic,
    find_arabic_font,
    shape_for_pdf,
    set_run_font,
    set_paragraph_direction,
    style_paragraph,
    scaled_pt,
    font_scale,
    translation_color_rgb,
)

logger = logging.getLogger(__name__)

MAX_CHARS = 500_000
MAX_FILE_MB = 50
WORD_TOKEN_RE = re.compile(r"\S+")
WORD_CHAR_RE = re.compile(r"[\w\u0600-\u06FF]", re.UNICODE)
_word_cache: dict[tuple[str, str], str] = {}
_WORD_BATCH_SIZE = 40


def _clear_word_cache():
    _word_cache.clear()
    _align_cache.clear()

def _token_core(token: str) -> str:
    return re.sub(r"^[^\w\u0600-\u06FF]+|[^\w\u0600-\u06FF]+$", "", (token or "").strip())


def _same_token(left: str, right: str) -> bool:
    a = _token_core(left).casefold()
    b = _token_core(right).casefold()
    return bool(a) and a == b


# أدوات تلتصق بالكلمة اللي بعدها وتترجم وياها كعبارة واحدة.
_EN_GLUE = {
    "the", "a", "an", "this", "that", "these", "those",
    "my", "your", "his", "her", "its", "our", "their",
    "some", "any", "each", "every", "no",
    "of", "in", "on", "at", "to", "for", "from", "with", "by",
    "as", "into", "onto", "over", "under", "about", "between",
    "through", "during", "without", "within", "upon", "per",
}
_EN_STOP = {
    "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "can", "could", "may", "might", "shall", "should", "must",
    "and", "or", "but", "if", "then", "than", "not", "so", "because", "while",
    "when", "where", "who", "which", "what", "how", "why",
}
_AR_GLUE = {
    "في", "من", "على", "إلى", "الى", "عن", "مع",
    "هذا", "هذه", "ذلك", "تلك", "هؤلاء",
}


def _glue_key(token: str) -> str:
    return _token_core(token).casefold()


def group_tokens(tokens: list[str], direction: str) -> list[list[str]]:
    """the flower تبقى عبارة واحدة، مو كلمتين منفصلتين."""
    glue = _EN_GLUE if direction != "ar_en" else _AR_GLUE
    stop = _EN_STOP if direction != "ar_en" else set()
    groups: list[list[str]] = []
    i = 0
    n = len(tokens)
    while i < n:
        key = _glue_key(tokens[i])
        if key in glue:
            j = i
            while j < n and _glue_key(tokens[j]) in glue:
                j += 1
            end = j
            content = 0
            while end < n and content < 3:
                nxt = _glue_key(tokens[end])
                if not nxt or nxt in glue or nxt in stop:
                    break
                content += 1
                end += 1
            if content:
                groups.append(tokens[i:end])
                i = end
                continue
        if not key or key in stop or not WORD_CHAR_RE.search(tokens[i]):
            groups.append([tokens[i]])
            i += 1
            continue
        end = i + 1
        content = 1
        while end < n and content < 3:
            nxt = _glue_key(tokens[end])
            if not nxt or nxt in glue or nxt in stop or not WORD_CHAR_RE.search(tokens[end]):
                break
            content += 1
            end += 1
        groups.append(tokens[i:end])
        i = end
    return groups


def _echoes_source(translation: str, tokens: list[str]) -> bool:
    src = " ".join(_token_core(token) for token in tokens if _token_core(token))
    return _same_token(translation, src)


_align_cache: dict[tuple[str, str], list[str] | None] = {}
_AR_WORD = re.compile(r"[\u0600-\u06FF]{2,}")
_EN_WORD = re.compile(r"[A-Za-z]{2,}(?:-[A-Za-z0-9]+)*")


def _target_tokens(text: str, direction: str) -> list[str]:
    pattern = _AR_WORD if direction == "en_ar" else _EN_WORD
    return pattern.findall(text or "")


def _aligned_cores(cores: list[str], direction: str) -> list[str] | None:
    """ترجمة السطر مرة واحدة. إذا عدد الكلمات طابق، كل كلمة تاخذ معناها من السياق."""
    cleaned = [core for core in cores if core]
    if len(cleaned) < 2:
        return None
    key = (direction, " ".join(cleaned))
    if key in _align_cache:
        return _align_cache[key]
    mapped: list[str] | None = None
    try:
        translated = translate_text(key[1], direction)
        targets = _target_tokens(translated, direction)
        if len(targets) == len(cleaned):
            mapped = targets
    except Exception as exc:
        logger.warning("Line alignment failed: %s", exc)
    _align_cache[key] = mapped
    return mapped


def _translations_for_groups(tokens: list[str], direction: str) -> list[str]:
    groups = group_tokens(tokens, direction)
    cores = [_token_core(token) for token in tokens if WORD_CHAR_RE.search(token)]
    aligned = _aligned_cores(cores, direction)
    cursor = 0
    translations: list[str] = []
    for group in groups:
        words = [token for token in group if WORD_CHAR_RE.search(token)]
        if not words:
            translations.append("")
            continue
        piece = ""
        if aligned is not None and cursor + len(words) <= len(aligned):
            piece = " ".join(aligned[cursor:cursor + len(words)]).strip()
        if not piece:
            piece = translate_token_group(words, direction)
        translations.append(piece)
        cursor += len(words)
    return translations


def translate_token_group(tokens: list[str], direction: str) -> str:
    words = [token for token in tokens if WORD_CHAR_RE.search(token)]
    if not words:
        return ""
    if len(words) == 1:
        translated = translate_word(words[0], direction)
    else:
        phrase = " ".join(_token_core(token) for token in words if _token_core(token))
        translated = translate_text(phrase, direction)
    if _echoes_source(translated, words):
        return ""
    return translated


def _prewarm_word_cache_for_text(text: str, direction: str) -> None:
    """ترجمة الكلمات الفريدة دفعة واحدة — أسرع بكثير من كلمة/طلب."""
    direction = resolve_direction(text, direction)
    unique: list[str] = []
    seen: set[str] = set()
    for unit in _extract_logical_units(text):
        for token in WORD_TOKEN_RE.findall(unit):
            core = _token_core(token)
            key = core.lower()
            if key and key not in seen and WORD_CHAR_RE.search(core):
                seen.add(key)
                unique.append(core)
    if not unique:
        return

    logger.info("Prewarming word cache: %d unique tokens", len(unique))
    for i in range(0, len(unique), _WORD_BATCH_SIZE):
        chunk = unique[i:i + _WORD_BATCH_SIZE]
        payload = "\n".join(chunk)
        try:
            translated = translate_text(payload, direction)
            lines = [ln.strip() for ln in translated.splitlines() if ln.strip()]
            if len(lines) == len(chunk):
                for w, tw in zip(chunk, lines):
                    tw = re.sub(r"^[\(\[]?\d{1,3}[\)\].:\-]\s*", "", tw).strip()
                    _word_cache[(w.lower(), direction)] = tw or w
                continue
        except Exception as e:
            logger.warning("Batch word prewarm failed: %s", e)
        for w in chunk:
            key = (w.lower(), direction)
            if key not in _word_cache:
                try:
                    _word_cache[key] = translate_text(w, direction)
                except Exception:
                    _word_cache[key] = w


def translate_word(word: str, direction: str) -> str:
    raw = word.strip()
    if not raw or not WORD_CHAR_RE.search(raw):
        return word
    core = _token_core(raw)
    if not core:
        return word
    prefix = raw[: raw.index(core)] if core in raw else ""
    suffix = raw[raw.index(core) + len(core) :] if core in raw else ""
    key = (core.lower(), direction)
    if key not in _word_cache:
        _word_cache[key] = translate_text(core, direction)
    return f"{prefix}{_word_cache[key]}{suffix}"


def _check_file_limits(source_path: Path, content: str):
    size_mb = source_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        raise ValueError(f"الملف كبير ({size_mb:.1f} MB). الحد الأقصى {MAX_FILE_MB} MB")
    if len(content) > MAX_CHARS:
        raise ValueError(f"النص طويل جداً ({len(content)} حرف). الحد الأقصى {MAX_CHARS} حرف")


def _extract_words(text: str) -> list[str]:
    return [t for t in WORD_TOKEN_RE.findall(text) if WORD_CHAR_RE.search(t)]


def _apply_affixes(original: str, translated_core: str) -> str:
    core = re.sub(r"^[^\w\u0600-\u06FF]+|[^\w\u0600-\u06FF]+$", "", original)
    if not core or core not in original:
        return translated_core
    prefix = original[: original.index(core)]
    suffix = original[original.index(core) + len(core) :]
    return f"{prefix}{translated_core}{suffix}"


def translate_word_in_context(token: str, line: str, direction: str) -> str:
    """ترجمة الكلمة. إذا رجعت كما هي نستخدم سياق الجملة بدل قبول النسخة."""
    direct = translate_word(token, direction)
    if not _same_token(direct, token):
        return direct

    words = [w for w in WORD_TOKEN_RE.findall(line) if WORD_CHAR_RE.search(w)]
    if token not in words:
        return direct

    idx = words.index(token)
    for window in (2, 3):
        start = max(0, idx - window + 1)
        end = min(len(words), idx + window)
        chunk_words = words[start:end]
        if len(chunk_words) < 2:
            continue
        chunk_tr = translate_text(" ".join(chunk_words), direction).strip()
        tr_tokens = [w for w in WORD_TOKEN_RE.findall(chunk_tr) if WORD_CHAR_RE.search(w)]
        if len(tr_tokens) != len(chunk_words):
            continue
        candidate = tr_tokens[idx - start]
        if not _same_token(candidate, chunk_words[idx - start]):
            return _apply_affixes(token, candidate)
    return direct


def _extract_logical_units(text: str) -> list[str]:
    """دمج الأسطر المتقطعة في فقرات/جمل كاملة لترجمة أدق"""
    units: list[str] = []
    buffer: list[str] = []

    for line in text.splitlines():
        s = line.strip()
        if not s:
            if buffer:
                units.append(" ".join(buffer))
                buffer = []
            continue
        buffer.append(s)
        if re.search(r"[.!?؟…:;]$", s) or len(" ".join(buffer)) > 280:
            units.append(" ".join(buffer))
            buffer = []

    if buffer:
        units.append(" ".join(buffer))
    return units if units else [text.strip()]


def _format_word_pair(token: str, translation: str) -> str:
    return f"{token}  —  {translation}"


def build_literal_sections(text: str, direction: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """ترجمة حرفية مرتبة حسب الفقرات — الملف كاملاً"""
    direction = resolve_direction(text, direction)
    _prewarm_word_cache_for_text(text, direction)
    sections: list[tuple[str, list[tuple[str, str]]]] = []

    for idx, unit in enumerate(_extract_logical_units(text), 1):
        pairs: list[tuple[str, str]] = []
        words = [token for token in WORD_TOKEN_RE.findall(unit) if WORD_CHAR_RE.search(token)]
        for group in group_tokens(words, direction):
            pairs.append((" ".join(group), translate_token_group(group, direction)))
        if pairs:
            sections.append((f"الفقرة {idx}", pairs))

    return sections


def build_literal_sections_from_pairs(
    pairs: list[tuple[str, str]],
) -> list[tuple[str, list[tuple[str, str]]]]:
    """حرفي من فقرات مترجمة — بديل سريع عند غياب Argos."""
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for idx, (unit, tr) in enumerate(pairs, 1):
        src_words = [t for t in WORD_TOKEN_RE.findall(unit) if WORD_CHAR_RE.search(t)]
        tr_words = [t for t in WORD_TOKEN_RE.findall(tr) if WORD_CHAR_RE.search(t)]
        if src_words and len(src_words) == len(tr_words):
            word_pairs = list(zip(src_words, tr_words))
        elif src_words:
            word_pairs = [(w, tr) for w in src_words]
        else:
            word_pairs = [(unit, tr)]
        sections.append((f"الفقرة {idx}", word_pairs))
    return sections


def _build_literal_files_from_pairs(
    pairs: list[tuple[str, str]], direction: str, out_dir: Path, stem: str,
) -> dict[str, Path]:
    sections = build_literal_sections_from_pairs(pairs)
    literal_pdf = out_dir / f"{stem}_1_حرفي.pdf"
    create_literal_pdf(sections, literal_pdf, title="ترجمة حرفية — كلمة بكلمة", direction=direction)
    return {"literal": literal_pdf}


def build_literal_text(text: str, direction: str) -> str:
    """كل كلمة مع ترجمتها في سطر واحد — مرتب حسب الفقرات"""
    lines: list[str] = []
    for title, pairs in build_literal_sections(text, direction):
        lines.append(f"{'═' * 12} {title} {'═' * 12}")
        for word, tr in pairs:
            lines.append(_format_word_pair(word, tr))
        lines.append("")
    return "\n".join(lines).strip()


def _build_literal_files(content: str, direction: str, out_dir: Path, stem: str) -> dict[str, Path]:
    direction = resolve_direction(content, direction)
    sections = build_literal_sections(content, direction)

    literal_pdf = out_dir / f"{stem}_1_حرفي.pdf"
    create_literal_pdf(sections, literal_pdf, title="ترجمة حرفية — كلمة بكلمة", direction=direction)

    return {"literal": literal_pdf}


def build_line_pairs(text: str, direction: str) -> list[tuple[str, str]]:
    """ترجمة دقيقة: فقرة كاملة ثم ترجمتها"""
    direction = resolve_direction(text, direction)
    pairs: list[tuple[str, str]] = []

    for unit in _extract_logical_units(text):
        unit = unit.strip()
        if not unit:
            continue
        # ترجمة الجملة/الفقرة كاملة لدقة أعلى
        translated = translate_text(unit, direction)
        pairs.append((unit, translated))

    return pairs


def _build_line_pairs_file(
    content: str,
    direction: str,
    out_dir: Path,
    stem: str,
    pairs: list[tuple[str, str]] | None = None,
) -> dict[str, Path]:
    direction = resolve_direction(content, direction)
    if pairs is None:
        pairs = build_line_pairs(content, direction)
    path = out_dir / f"{stem}_3_سطر_بسطر.pdf"
    create_pairs_pdf(pairs, path, title="ترجمة سطر بسطر — أصل وترجمة", direction=direction)
    return {"line_pairs": path}


def _write_structured_online(
    source_path: Path,
    pairs: list[tuple[str, str]],
    out_dir: Path,
    stem: str,
    direction: str,
) -> Path:
    suffix = source_path.suffix.lower()
    full_text = "\n\n".join(tr for _, tr in pairs)

    if suffix in (".docx", ".doc"):
        path = out_dir / f"{stem}_2_بنفس_الترتيب.docx"
        doc = Document()
        for _, tr in pairs:
            para = doc.add_paragraph(tr)
            style_paragraph(para, tr, direction)
        doc.save(path)
        return path

    if suffix == ".pdf":
        path = out_dir / f"{stem}_2_بنفس_الترتيب.pdf"
        create_pairs_pdf(
            pairs, path,
            title="ترجمة بنفس الترتيب — أصل وترجمة",
            direction=direction,
        )
        return path

    path = out_dir / f"{stem}_2_بنفس_الترتيب.txt"
    path.write_text(full_text, encoding="utf-8-sig")
    return path


def _build_overlay_online(
    pairs: list[tuple[str, str]], out_dir: Path, stem: str, direction: str,
) -> dict[str, Path]:
    """ملف 4 — كلمة وترجمتها (من محاذاة الفقرات)."""
    sections = build_literal_sections_from_pairs(pairs)
    flat_pairs: list[tuple[str, str]] = []
    for _, word_pairs in sections:
        flat_pairs.extend(word_pairs)
    path = out_dir / f"{stem}_4_فوق_الكلمات.pdf"
    create_pairs_pdf(
        [(_format_word_pair(w, t), "") for w, t in flat_pairs],
        path,
        title="ترجمة فوق الكلمات",
        direction=direction,
    )
    return {"overlay": path}


def _paragraph_pairs(content: str, direction: str) -> list[tuple[str, str]]:
    direction = resolve_direction(content, direction)
    pairs: list[tuple[str, str]] = []
    units = _extract_logical_units(content)
    total = len(units)
    for i, unit in enumerate(units, 1):
        unit = unit.strip()
        if not unit:
            continue
        if i % 5 == 0 or i == total:
            logger.info("Translating paragraph %d/%d", i, total)
        pairs.append((unit, translate_text(unit, direction)))
    return pairs


def prepare_online_file_translation(source_path: Path, direction: str = "auto") -> dict:
    """استخراج + ترجمة فقرات — الخطوة البطيئة."""
    content = extract_text_from_file(source_path)
    if not content.strip():
        raise ValueError("لم يتم العثور على نص في الملف")
    _check_file_limits(source_path, content)
    direction = resolve_direction(content, direction)
    pairs = _paragraph_pairs(content, direction)
    if not pairs:
        raise ValueError("لم يتم العثور على فقرات للترجمة")

    truncated = False
    limit = file_max_paragraphs()
    if len(pairs) > limit:
        pairs = pairs[:limit]
        truncated = True
        logger.warning("Truncated to %d paragraphs", limit)

    return {
        "content": content,
        "direction": direction,
        "pairs": pairs,
        "stem": source_path.stem,
        "source_path": source_path,
        "truncated": truncated,
    }


def prepare_online_image_translation(image_path: Path, direction: str = "auto") -> dict:
    from services.ocr_service import ocr_image

    content = ocr_image(image_path)
    if not content.strip():
        raise ValueError("لم يتم العثور على نص في الصورة")
    _check_file_limits(image_path, content)
    direction = resolve_direction(content, direction)
    pairs = _paragraph_pairs(content, direction)
    if not pairs:
        raise ValueError("لم يتم العثور على نص للترجمة في الصورة")

    truncated = False
    limit = file_max_paragraphs()
    if len(pairs) > limit:
        pairs = pairs[:limit]
        truncated = True

    return {
        "content": content,
        "direction": direction,
        "pairs": pairs,
        "stem": image_path.stem,
        "source_path": image_path,
        "truncated": truncated,
        "is_image": True,
    }


def build_online_literal(data: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return _build_literal_files_from_pairs(
        data["pairs"], data["direction"], output_dir, data["stem"],
    )["literal"]


def build_online_structured(data: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if data.get("is_image"):
        path = output_dir / f"{data['stem']}_2_بنفس_الترتيب.pdf"
        create_pairs_pdf(
            data["pairs"], path,
            title="ترجمة الصورة — أصل وترجمة",
            direction=data["direction"],
        )
        return path
    return _write_structured_online(
        data["source_path"], data["pairs"], output_dir, data["stem"], data["direction"],
    )


def build_online_line_pairs(data: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return _build_line_pairs_file(
        data["content"], data["direction"], output_dir, data["stem"], pairs=data["pairs"],
    )["line_pairs"]


def build_online_overlay(data: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return _build_overlay_online(
        data["pairs"], output_dir, data["stem"], data["direction"],
    )["overlay"]


def _argos_available() -> bool:
    return prefer_local_for_files() and is_translator_ready()


def _translate_file_online_full(
    source_path: Path, output_dir: Path, direction: str = "auto",
) -> dict[str, Path]:
    """4 ملفات عبر الإنternet — فقرة بفقرة (لا تعليق)."""
    _clear_word_cache()
    stem = source_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    content = extract_text_from_file(source_path)
    if not content.strip():
        raise ValueError("لم يتم العثور على نص في الملف")
    _check_file_limits(source_path, content)
    direction = resolve_direction(content, direction)

    logger.info("Online 4-file translation: %s (%d chars)", source_path.name, len(content))
    pairs = _paragraph_pairs(content, direction)
    if not pairs:
        raise ValueError("لم يتم العثور على فقرات للترجمة")

    result = _build_literal_files_from_pairs(pairs, direction, output_dir, stem)
    result["structured"] = _write_structured_online(source_path, pairs, output_dir, stem, direction)
    result.update(_build_line_pairs_file(content, direction, output_dir, stem, pairs=pairs))
    result.update(_build_overlay_online(pairs, output_dir, stem, direction))
    logger.info("Online 4-file translation done")
    return result


def _translate_image_online_full(
    image_path: Path, output_dir: Path, direction: str = "auto",
) -> dict[str, Path]:
    from services.ocr_service import ocr_image

    _clear_word_cache()
    stem = image_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    content = ocr_image(image_path)
    if not content.strip():
        raise ValueError("لم يتم العثور على نص في الصورة")
    _check_file_limits(image_path, content)
    direction = resolve_direction(content, direction)

    logger.info("Online 4-file image translation: %s", image_path.name)
    pairs = _paragraph_pairs(content, direction)
    if not pairs:
        raise ValueError("لم يتم العثور على نص للترجمة في الصورة")

    result = _build_literal_files_from_pairs(pairs, direction, output_dir, stem)
    structured_pdf = output_dir / f"{stem}_2_بنفس_الترتيب.pdf"
    create_pairs_pdf(pairs, structured_pdf, title="ترجمة الصورة — أصل وترجمة", direction=direction)
    result["structured"] = structured_pdf
    result.update(_build_line_pairs_file(content, direction, output_dir, stem, pairs=pairs))
    result.update(_build_overlay_online(pairs, output_dir, stem, direction))
    return result


OVERLAY_TR_SIZE = 7.5
OVERLAY_TR_SIZE_PDF = 8.5
OVERLAY_WORD_SIZE = 11
OVERLAY_LINE_SPACING = 0.68


def _nudge_run_down(run, points: float):
    """ينزل الترجمة شوي باتجاه الكلمة. القيمة بنصف نقطة."""
    half_points = -int(round(points * 2))
    if half_points == 0:
        return
    r_pr = run._element.get_or_add_rPr()
    pos = r_pr.find(qn("w:position"))
    if pos is None:
        pos = OxmlElement("w:position")
        r_pr.append(pos)
    pos.set(qn("w:val"), str(half_points))


def _colorize_overlay_run(run):
    r, g, b = translation_color_rgb()
    run.font.color.rgb = RGBColor(
        int(round(r * 255)), int(round(g * 255)), int(round(b * 255)),
    )


def _paragraph_has_image(para) -> bool:
    for run in para.runs:
        if run._element.xpath(".//w:drawing") or run._element.xpath(".//w:pict"):
            return True
    return False


def _remove_table_borders(table):
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl.insert(0, tbl_pr)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tbl_pr.append(borders)


def _set_cell_tight_margin(cell):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = OxmlElement("w:tcMar")
    for margin in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{margin}")
        el.set(qn("w:w"), "0")
        el.set(qn("w:type"), "dxa")
        tc_mar.append(el)
    tc_pr.append(tc_mar)


def _set_cell_valign(cell, align: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    v_align = OxmlElement("w:vAlign")
    v_align.set(qn("w:val"), align)
    tc_pr.append(v_align)


def _set_table_tight_spacing(table):
    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        table._tbl.insert(0, tbl_pr)
    spacing = OxmlElement("w:tblCellSpacing")
    spacing.set(qn("w:w"), "0")
    spacing.set(qn("w:type"), "dxa")
    tbl_pr.append(spacing)


def _iter_pdf_word_boxes(page):
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_text = "".join(s.get("text", "") for s in line.get("spans", []))
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                if not span_text.strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                span_w = max(x1 - x0, 1)
                total = max(len(span_text), 1)
                for match in WORD_TOKEN_RE.finditer(span_text):
                    token = match.group()
                    if not WORD_CHAR_RE.search(token):
                        continue
                    start, end = match.start(), match.end()
                    wx0 = x0 + (start / total) * span_w
                    wx1 = x0 + (end / total) * span_w
                    yield token, wx0, y0, wx1, y1, line_text


def _pdf_insert_translation_above(page, x0, y0, x1, y1, text: str, fontfile: str | None, rtl: bool):
    import fitz
    from services.text_shape import has_arabic

    display = shape_for_pdf(text) if has_arabic(text) else text
    word_w = max(x1 - x0, 3)
    word_h = max(y1 - y0, 3)
    scale = font_scale()
    requested = scaled_pt(OVERLAY_TR_SIZE_PDF)
    fs = min(requested, word_h * 0.52 * scale)
    fs = max(fs, scaled_pt(6.0, 4.2))

    font_kwargs = {}
    if fontfile:
        font_kwargs["fontfile"] = fontfile
        font_kwargs["fontname"] = "TahomaAr"

    def _text_width(size: float) -> float:
        try:
            return fitz.get_text_length(display, fontsize=size, **font_kwargs)
        except Exception:
            return len(display) * size * 0.45

    tw = _text_width(fs)
    max_w = word_w * (1.22 + 0.22 * max(0.0, scale - 1.0))
    min_fs = scaled_pt(4.8, 3.6)
    while tw > max_w and fs > min_fs:
        fs -= 0.15
        tw = _text_width(fs)

    x = max(x0, x1 - tw) if rtl else x0
    # فوق الكتابة بمسافة صغيرة، مو نازلة على جسم الكلمة
    y = y0 + min(fs * 0.28, word_h * 0.08) - 2.4

    try:
        page.insert_text((x, y), display, fontsize=fs, color=translation_color_rgb(), **font_kwargs)
    except Exception:
        page.insert_text((x, y), display, fontsize=fs, color=translation_color_rgb())


def _run_has_image(run) -> bool:
    return bool(run._element.xpath(".//w:drawing") or run._element.xpath(".//w:pict"))


def _add_overlay_runs_at_index(
    para, parent, insert_idx: int, text: str, direction: str
) -> int:
    direction = resolve_direction(text, direction)
    tokens = WORD_TOKEN_RE.findall(text)
    groups = group_tokens(tokens, direction)
    translations = _translations_for_groups(tokens, direction)
    for group, tr in zip(groups, translations):
        words = [token for token in group if WORD_CHAR_RE.search(token)]
        if not words:
            tr = ""
        if tr.strip():
            r = OxmlElement("w:r")
            parent.insert(insert_idx, r)
            insert_idx += 1
            tr_run = Run(r, para)
            tr_run.text = tr
            tr_sz = scaled_pt(OVERLAY_TR_SIZE)
            set_run_font(tr_run, "Tahoma", max(5, int(round(tr_sz))))
            tr_run.font.superscript = True
            tr_run.font.size = Pt(tr_sz)
            _nudge_run_down(tr_run, 0)
            _colorize_overlay_run(tr_run)
        for token in group:
            if not WORD_CHAR_RE.search(token):
                r = OxmlElement("w:r")
                parent.insert(insert_idx, r)
                insert_idx += 1
                Run(r, para).text = token
                continue
            r = OxmlElement("w:r")
            parent.insert(insert_idx, r)
            insert_idx += 1
            w_run = Run(r, para)
            w_run.text = token
            set_run_font(w_run, "Tahoma", OVERLAY_WORD_SIZE)
            if not token.endswith((" ", "\t")):
                sp = OxmlElement("w:r")
                parent.insert(insert_idx, sp)
                insert_idx += 1
                Run(sp, para).text = " "
    return insert_idx


def _transform_text_run(para, run, direction: str):
    if _run_has_image(run):
        return
    segment = run.text
    if not segment.strip():
        return
    elem = run._element
    parent = elem.getparent()
    idx = parent.index(elem)
    parent.remove(elem)
    _add_overlay_runs_at_index(para, parent, idx, segment, direction)


def _set_row_exact_height(row, height_pt: float):
    tr_pr = row._tr.get_or_add_trPr()
    tr_height = OxmlElement("w:trHeight")
    tr_height.set(qn("w:val"), str(int(height_pt * 20)))
    tr_height.set(qn("w:hRule"), "exact")
    tr_pr.append(tr_height)


def _replace_paragraph_with_overlay_table(doc: Document, para, direction: str):
    text = para.text.strip()
    tokens = WORD_TOKEN_RE.findall(text)
    if not tokens:
        return
    groups = group_tokens(tokens, direction)
    translations = _translations_for_groups(tokens, direction)

    table = doc.add_table(rows=1, cols=len(groups))
    _remove_table_borders(table)
    _set_table_tight_spacing(table)

    for col, group in enumerate(groups):
        cell = table.rows[0].cells[col]
        _set_cell_tight_margin(cell)
        _set_cell_valign(cell, "center")

        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = OVERLAY_LINE_SPACING
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        words = [token for token in group if WORD_CHAR_RE.search(token)]
        tr = translations[col] if words else ""
        if tr.strip():
            tr_run = p.add_run(tr)
            tr_sz = scaled_pt(OVERLAY_TR_SIZE)
            set_run_font(tr_run, "Tahoma", max(5, int(round(tr_sz))))
            _nudge_run_down(tr_run, 0.4)
            _colorize_overlay_run(tr_run)
            br_run = p.add_run()
            br_run.add_break()

        w_run = p.add_run(" ".join(group))
        set_run_font(w_run, "Tahoma", OVERLAY_WORD_SIZE)

    row_h = scaled_pt(OVERLAY_TR_SIZE) + OVERLAY_WORD_SIZE + 2
    _set_row_exact_height(table.rows[0], row_h)

    tbl_element = table._tbl
    doc.element.body.remove(tbl_element)
    p_element = para._element
    p_element.addnext(tbl_element)
    p_element.getparent().remove(p_element)


def _apply_overlay_paragraph(para, direction: str, doc: Document):
    text = para.text
    if not text.strip():
        return

    if _paragraph_has_image(para):
        direction = resolve_direction(text, direction)
        rtl = direction == "en_ar" or is_mostly_arabic(text)
        set_paragraph_direction(para, rtl)
        for run in list(para.runs):
            if _run_has_image(run):
                continue
            _transform_text_run(para, run, direction)
        return

    _replace_paragraph_with_overlay_table(doc, para, direction)


def _process_docx_overlay(doc: Document, direction: str):
    for para in list(doc.paragraphs):
        _apply_overlay_paragraph(para, direction, doc)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in list(cell.paragraphs):
                    _apply_overlay_paragraph(para, direction, doc)


def _grouped_overlay_jobs(jobs: list[tuple], direction: str) -> list[tuple]:
    """يجمع the flower في مربع واحد حتى الترجمة تطلع مرة واحدة."""
    lines: list[list[tuple]] = []
    for job in jobs:
        _token, _x0, y0, _x1, y1, line_text = job
        if lines:
            prev = lines[-1][-1]
            same_line = abs(prev[2] - y0) < max(4.0, (prev[4] - prev[2]) * 0.7)
            if same_line and prev[5] == line_text:
                lines[-1].append(job)
                continue
        lines.append([job])

    grouped: list[tuple] = []
    for line in lines:
        tokens = [job[0] for job in line]
        index = 0
        translations = _translations_for_groups(tokens, direction)
        for group, translation in zip(group_tokens(tokens, direction), translations):
            chunk = line[index:index + len(group)]
            index += len(group)
            words = [token for token in group if WORD_CHAR_RE.search(token)]
            if not words or not chunk:
                continue
            grouped.append((
                " ".join(words),
                min(item[1] for item in chunk),
                min(item[2] for item in chunk),
                max(item[3] for item in chunk),
                max(item[4] for item in chunk),
                translation,
            ))
    return grouped


def _translate_pdf_overlay(source: Path, out_path: Path, direction: str):
    import fitz

    src = fitz.open(str(source))
    out = fitz.open()
    fontfile = find_arabic_font()

    for page_num in range(len(src)):
        page = src[page_num]
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        new_page.show_pdf_page(page.rect, src, page_num)

        words = page.get_text("words")
        word_jobs = list(_iter_pdf_word_boxes(page))
        if not word_jobs and words:
            for w in words:
                token = w[4]
                if not WORD_CHAR_RE.search(token):
                    continue
                word_jobs.append((token, w[0], w[1], w[2], w[3], token))

        if not word_jobs:
            continue

        for token, x0, y0, x1, y1, tr in _grouped_overlay_jobs(word_jobs, direction):
            if not (tr or "").strip():
                continue
            rtl = direction == "en_ar" or is_mostly_arabic(tr)
            _pdf_insert_translation_above(new_page, x0, y0, x1, y1, tr, fontfile, rtl)

    out.save(str(out_path))
    out.close()
    src.close()


def _build_overlay_file(
    source_path: Path, out_dir: Path, stem: str, direction: str, content: str,
) -> dict[str, Path]:
    direction = resolve_direction(content, direction)
    suffix = source_path.suffix.lower()

    if suffix in (".docx", ".doc"):
        out = out_dir / f"{stem}_4_فوق_الكلمات.docx"
        shutil.copy2(source_path, out)
        doc = Document(out)
        _process_docx_overlay(doc, direction)
        doc.save(out)
        return {"overlay": out}

    if suffix == ".pdf":
        out = out_dir / f"{stem}_4_فوق_الكلمات.pdf"
        _translate_pdf_overlay(source_path, out, direction)
        return {"overlay": out}

    out = out_dir / f"{stem}_4_فوق_الكلمات.docx"
    doc = Document()
    for line in content.splitlines():
        if not line.strip():
            doc.add_paragraph()
            continue
        para = doc.add_paragraph(line)
        _replace_paragraph_with_overlay_table(doc, para, direction)
    doc.save(out)
    return {"overlay": out}


def _set_paragraph_translated(para, translated: str, direction: str):
    sz = max(8, int(round(scaled_pt(12))))
    placed = False
    for run in para.runs:
        if run._element.xpath(".//w:drawing") or run._element.xpath(".//w:pict"):
            continue
        if not placed:
            run.text = translated
            set_run_font(run, "Tahoma", sz)
            placed = True
        else:
            run.text = ""
    if not placed:
        run = para.add_run(translated)
        set_run_font(run, "Tahoma", sz)
    style_paragraph(para, translated, direction, base_size=sz)


def _process_docx_paragraphs(doc: Document, direction: str):
    for para in doc.paragraphs:
        if not para.text.strip():
            continue
        translated = translate_text(para.text.strip(), direction)
        _set_paragraph_translated(para, translated, direction)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    if not para.text.strip():
                        continue
                    translated = translate_text(para.text.strip(), direction)
                    _set_paragraph_translated(para, translated, direction)


def _translate_docx(source: Path, out_dir: Path, stem: str, direction: str) -> dict[str, Path]:
    content = extract_text_from_file(source)
    _check_file_limits(source, content)
    direction = resolve_direction(content, direction)

    literal_files = _build_literal_files(content, direction, out_dir, stem)

    structured_path = out_dir / f"{stem}_2_بنفس_الترتيب.docx"
    shutil.copy2(source, structured_path)
    doc_full = Document(structured_path)
    _process_docx_paragraphs(doc_full, direction)
    doc_full.save(structured_path)

    return {**literal_files, "structured": structured_path}


def _pdf_write_in_box(page, rect, text: str, fontsize: float, fontfile: str | None, rtl: bool):
    import fitz
    from services.text_shape import has_arabic

    display = shape_for_pdf(text) if has_arabic(text) else text
    kwargs = {
        "fontsize": fontsize,
        "align": fitz.TEXT_ALIGN_RIGHT if rtl else fitz.TEXT_ALIGN_LEFT,
    }
    if fontfile:
        kwargs["fontfile"] = fontfile
        kwargs["fontname"] = "TahomaAr"
    try:
        page.insert_textbox(rect, display, **kwargs)
    except Exception:
        page.insert_textbox(rect, display, fontsize=fontsize, align=kwargs["align"])


def _build_structured_pdf(source: Path, out_path: Path, direction: str) -> None:
    import fitz

    src_doc = fitz.open(str(source))
    out_doc = fitz.open()
    fontfile = find_arabic_font()

    for page_num in range(len(src_doc)):
        page = src_doc[page_num]
        new_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
        new_page.show_pdf_page(page.rect, src_doc, page_num)

        redact_rects = []
        text_jobs = []

        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            block_lines = []
            block_bbox = None
            block_size = 11
            for line in block.get("lines", []):
                line_text = "".join(s.get("text", "") for s in line.get("spans", []))
                if not line_text.strip():
                    continue
                block_lines.append(line_text.strip())
                if block_bbox is None:
                    block_bbox = fitz.Rect(line["bbox"])
                else:
                    block_bbox |= fitz.Rect(line["bbox"])
                block_size = max(block_size, line["spans"][0].get("size", 11))

            if not block_lines:
                continue
            full_text = " ".join(block_lines)
            new_text = translate_text(full_text, direction)
            redact_rects.append(block_bbox)
            rtl = direction == "en_ar" or is_mostly_arabic(new_text)
            text_jobs.append((block_bbox, new_text, block_size, rtl))

        for rect in redact_rects:
            new_page.add_redact_annot(rect, fill=(1, 1, 1))
        if redact_rects:
            new_page.apply_redactions()

        for rect, new_text, size, rtl in text_jobs:
            pad = fitz.Rect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 40, rect.y1 + size * 3)
            _pdf_write_in_box(new_page, pad, new_text, scaled_pt(size), fontfile, rtl)

    out_doc.save(str(out_path))
    out_doc.close()
    src_doc.close()


def prepare_full_file_translation(
    source_path: Path, output_dir: Path, direction: str = "auto",
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    sample = extract_text_from_file(source_path)
    if not sample.strip():
        raise ValueError("لم يتم العثور على نص في الملف")
    _check_file_limits(source_path, sample)
    direction = resolve_direction(sample, direction)
    if not is_translator_ready():
        raise RuntimeError("محرك الترجمة غير جاهز — انتظر دقيقة ثم أعد المحاولة")
    _clear_word_cache()
    return {
        "source_path": source_path,
        "output_dir": output_dir,
        "content": sample,
        "direction": direction,
        "stem": source_path.stem,
        "suffix": source_path.suffix.lower(),
    }


def prepare_full_image_translation(
    image_path: Path, output_dir: Path, direction: str = "auto",
) -> dict:
    from services.ocr_service import ocr_image_layout

    output_dir.mkdir(parents=True, exist_ok=True)
    layout = ocr_image_layout(image_path)
    content = layout.get("text", "")
    if not content.strip():
        raise ValueError("لم يتم العثور على نص في الصورة")
    _check_file_limits(image_path, content)
    direction = resolve_direction(content, direction)
    if not is_translator_ready():
        raise RuntimeError("محرك الترجمة غير جاهز — انتظر دقيقة ثم أعد المحاولة")
    _clear_word_cache()
    return {
        "image_path": image_path,
        "layout": layout,
        "content": content,
        "direction": direction,
        "stem": image_path.stem,
        "output_dir": output_dir,
    }


def build_full_file_literal(data: dict) -> Path:
    set_file_translation_mode(True)
    return _build_literal_files(
        data["content"], data["direction"], data["output_dir"], data["stem"],
    )["literal"]


def build_full_file_structured(data: dict) -> Path:
    set_file_translation_mode(True)
    source = data["source_path"]
    out_dir = data["output_dir"]
    stem = data["stem"]
    direction = data["direction"]
    content = data["content"]
    suffix = data["suffix"]

    if suffix in (".docx", ".doc"):
        path = out_dir / f"{stem}_2_بنفس_الترتيب.docx"
        shutil.copy2(source, path)
        doc = Document(path)
        _process_docx_paragraphs(doc, direction)
        doc.save(path)
        return path
    if suffix == ".pdf":
        path = out_dir / f"{stem}_2_بنفس_الترتيب.pdf"
        _build_structured_pdf(source, path, direction)
        return path
    path = out_dir / f"{stem}_2_بنفس_الترتيب.txt"
    lines = [
        translate_text(u, direction)
        for u in _extract_logical_units(content) if u.strip()
    ]
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return path


def build_full_file_line_pairs(data: dict) -> Path:
    set_file_translation_mode(True)
    return _build_line_pairs_file(
        data["content"], data["direction"], data["output_dir"], data["stem"],
    )["line_pairs"]


def build_full_file_overlay(data: dict) -> Path:
    set_file_translation_mode(True)
    with use_detected_field(data.get("content") or ""):
        return _build_overlay_file(
            data["source_path"], data["output_dir"], data["stem"],
            data["direction"], data["content"],
        )["overlay"]



def build_full_image_literal(data: dict) -> Path:
    set_file_translation_mode(True)
    return _build_literal_files(
        data["content"], data["direction"], data["output_dir"], data["stem"],
    )["literal"]


def build_full_image_structured(data: dict) -> Path:
    set_file_translation_mode(True)
    path = data["output_dir"] / f"{data['stem']}_2_بنفس_الترتيب.pdf"
    _translate_image_structured(
        data["image_path"], data["layout"], path, data["direction"], data["content"],
    )
    return path


def build_full_image_line_pairs(data: dict) -> Path:
    set_file_translation_mode(True)
    return _build_line_pairs_file(
        data["content"], data["direction"], data["output_dir"], data["stem"],
    )["line_pairs"]


def build_full_image_overlay(data: dict) -> Path:
    set_file_translation_mode(True)
    path = data["output_dir"] / f"{data['stem']}_4_فوق_الكلمات.pdf"
    with use_detected_field(data.get("content") or ""):
        _translate_image_overlay(
            data["image_path"], data["layout"], path, data["direction"], data["content"],
        )
    return path



def _translate_pdf(source: Path, out_dir: Path, stem: str, direction: str) -> dict[str, Path]:
    content = extract_text_from_file(source)
    _check_file_limits(source, content)
    direction = resolve_direction(content, direction)

    literal_files = _build_literal_files(content, direction, out_dir, stem)
    outputs = dict(literal_files)
    out_path = out_dir / f"{stem}_2_بنفس_الترتيب.pdf"
    _build_structured_pdf(source, out_path, direction)
    outputs["structured"] = out_path
    return outputs


def _translate_txt(source: Path, out_dir: Path, stem: str, direction: str) -> dict[str, Path]:
    content = extract_text_from_file(source)
    _check_file_limits(source, content)
    direction = resolve_direction(content, direction)

    literal_files = _build_literal_files(content, direction, out_dir, stem)

    structured_lines = []
    for unit in _extract_logical_units(content):
        structured_lines.append(translate_text(unit, direction))

    structured_path = out_dir / f"{stem}_2_بنفس_الترتيب.txt"
    structured_path.write_text("\n".join(structured_lines), encoding="utf-8-sig")

    return {**literal_files, "structured": structured_path}


def _translate_image_structured(
    image_path: Path, layout: dict, out_path: Path, direction: str, content: str,
):
    import fitz

    direction = resolve_direction(content, direction)
    fontfile = find_arabic_font()
    doc = fitz.open()
    page = doc.new_page(width=layout["width"], height=layout["height"])
    page.insert_image(page.rect, filename=str(image_path))

    redact_rects = []
    text_jobs = []

    for block in layout.get("lines", []):
        line_text = block["text"].strip()
        if not line_text:
            continue
        rect = fitz.Rect(block["x0"], block["y0"], block["x1"], block["y1"])
        new_text = translate_text(line_text, direction)
        redact_rects.append(rect)
        block_h = max(block["y1"] - block["y0"], 8)
        rtl = direction == "en_ar" or is_mostly_arabic(new_text)
        text_jobs.append((rect, new_text, min(block_h, 14), rtl))

    for rect in redact_rects:
        page.add_redact_annot(rect, fill=(1, 1, 1))
    if redact_rects:
        page.apply_redactions()

    for rect, new_text, size, rtl in text_jobs:
        pad = fitz.Rect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 40, rect.y1 + size * 2.5)
        _pdf_write_in_box(page, pad, new_text, scaled_pt(size), fontfile, rtl)

    doc.save(str(out_path))
    doc.close()


def _translate_image_overlay(
    image_path: Path, layout: dict, out_path: Path, direction: str, content: str,
):
    import fitz

    direction = resolve_direction(content, direction)
    fontfile = find_arabic_font()
    doc = fitz.open()
    page = doc.new_page(width=layout["width"], height=layout["height"])
    page.insert_image(page.rect, filename=str(image_path))

    words = [
        (token, x0, y0, x1, y1, line_text)
        for token, x0, y0, x1, y1, line_text in layout.get("words", [])
        if WORD_CHAR_RE.search(token)
    ]
    for token, x0, y0, x1, y1, tr in _grouped_overlay_jobs(words, direction):
        if not (tr or "").strip():
            continue
        rtl = direction == "en_ar" or is_mostly_arabic(tr)
        _pdf_insert_translation_above(page, x0, y0, x1, y1, tr, fontfile, rtl)

    doc.save(str(out_path))
    doc.close()


def _translate_paragraph_pairs(content: str, direction: str) -> list[tuple[str, str]]:
    """ترجمة فقرة بفقرة — سريعة ومناسبة للسحابة."""
    direction = resolve_direction(content, direction)
    pairs: list[tuple[str, str]] = []
    units = _extract_logical_units(content)
    total = len(units)
    for i, unit in enumerate(units, 1):
        unit = unit.strip()
        if not unit:
            continue
        if i % 10 == 0 or i == total:
            logger.info("Translating unit %d/%d", i, total)
        pairs.append((unit, translate_text(unit, direction)))
    return pairs


def _build_fast_outputs(
    pairs: list[tuple[str, str]], output_dir: Path, stem: str, direction: str,
) -> dict[str, Path]:
    """ملفان: PDF ثنائي + TXT كامل — بدون ترجمة كلمة بكلمة."""
    line_pdf = output_dir / f"{stem}_1_سطر_بسطر.pdf"
    create_pairs_pdf(
        pairs, line_pdf,
        title="ترجمة سطر بسطر — أصل وترجمة",
        direction=direction,
    )

    full_txt = output_dir / f"{stem}_2_ترجمة_كاملة.txt"
    full_txt.write_text("\n\n".join(tr for _, tr in pairs), encoding="utf-8-sig")

    return {"line_pairs": line_pdf, "structured": full_txt}


def translate_file_fast(
    source_path: Path, output_dir: Path, direction: str = "auto",
) -> dict[str, Path]:
    _clear_word_cache()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = source_path.stem

    content = extract_text_from_file(source_path)
    if not content.strip():
        raise ValueError("لم يتم العثور على نص في الملف")
    _check_file_limits(source_path, content)
    direction = resolve_direction(content, direction)

    logger.info("Fast file translation: %s (%d chars)", source_path.name, len(content))
    with use_detected_field(content):
        pairs = _translate_paragraph_pairs(content, direction)
    if not pairs:
        raise ValueError("لم يتم العثور على فقرات للترجمة")
    return _build_fast_outputs(pairs, output_dir, stem, direction)


def translate_image_fast(
    image_path: Path, output_dir: Path, direction: str = "auto",
) -> dict[str, Path]:
    _clear_word_cache()
    from services.ocr_service import ocr_image

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem

    content = ocr_image(image_path)
    if not content.strip():
        raise ValueError("لم يتم العثور على نص في الصورة")
    _check_file_limits(image_path, content)
    direction = resolve_direction(content, direction)

    logger.info("Fast image translation: %s (%d chars)", image_path.name, len(content))
    with use_detected_field(content):
        pairs = _translate_paragraph_pairs(content, direction)
    if not pairs:
        raise ValueError("لم يتم العثور على نص للترجمة في الصورة")
    return _build_fast_outputs(pairs, output_dir, stem, direction)


def prepare_dual_file_translation(
    source_path: Path, output_dir: Path, direction: str = "auto",
) -> dict:
    return prepare_full_file_translation(source_path, output_dir, direction)


def prepare_dual_image_translation(
    image_path: Path, output_dir: Path, direction: str = "auto",
) -> dict:
    return prepare_full_image_translation(image_path, output_dir, direction)


def translate_file_dual_modes(
    source_path: Path, output_dir: Path, direction: str = "auto",
) -> dict[str, Path]:
    """ملفان فقط: بنفس الترتيب + فوق الكلمات — Argos محلي سريع."""
    set_file_translation_mode(True)
    try:
        data = prepare_dual_file_translation(source_path, output_dir, direction)
        logger.info("Dual file translation (overlay): %s", source_path.name)
        overlay = build_full_file_overlay(data)
        return {"overlay": overlay}
    finally:
        set_file_translation_mode(False)


def translate_image_dual_modes(
    image_path: Path, output_dir: Path, direction: str = "auto",
) -> dict[str, Path]:
    set_file_translation_mode(True)
    try:
        data = prepare_dual_image_translation(image_path, output_dir, direction)
        logger.info("Dual image translation (overlay): %s", image_path.name)
        overlay = build_full_image_overlay(data)
        return {"overlay": overlay}
    finally:
        set_file_translation_mode(False)


def translate_image_two_modes(
    image_path: Path, output_dir: Path, direction: str = "auto"
) -> dict[str, Path]:
    if use_fast_file_translation():
        return translate_image_fast(image_path, output_dir, direction)
    if use_full_file_translation():
        set_file_translation_mode(True)
        try:
            return _translate_image_full(image_path, output_dir, direction)
        finally:
            set_file_translation_mode(False)
    return translate_image_dual_modes(image_path, output_dir, direction)


def _translate_image_full(
    image_path: Path, output_dir: Path, direction: str = "auto",
) -> dict[str, Path]:
    _clear_word_cache()
    from services.ocr_service import ocr_image_layout

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem
    layout = ocr_image_layout(image_path)
    content = layout.get("text", "")
    if not content.strip():
        raise ValueError("لم يتم العثور على نص في الصورة")
    _check_file_limits(image_path, content)
    direction = resolve_direction(content, direction)
    logger.info("Full image translation (4 files): %s", image_path.name)
    logger.info("Step 1/4: literal PDF...")
    result = _build_literal_files(content, direction, output_dir, stem)

    logger.info("Step 2/4: structured PDF...")
    structured_pdf = output_dir / f"{stem}_2_بنفس_الترتيب.pdf"
    _translate_image_structured(image_path, layout, structured_pdf, direction, content)
    result["structured"] = structured_pdf

    logger.info("Step 3/4: line pairs...")
    result.update(_build_line_pairs_file(content, direction, output_dir, stem))

    logger.info("Step 4/4: overlay...")
    overlay_pdf = output_dir / f"{stem}_4_فوق_الكلمات.pdf"
    _translate_image_overlay(image_path, layout, overlay_pdf, direction, content)
    result["overlay"] = overlay_pdf

    logger.info("Full image translation done")
    return result


def translate_file_two_modes(
    source_path: Path, output_dir: Path, direction: str = "auto"
) -> dict[str, Path]:
    if use_fast_file_translation():
        return translate_file_fast(source_path, output_dir, direction)
    if use_full_file_translation():
        set_file_translation_mode(True)
        try:
            return _translate_file_full(source_path, output_dir, direction)
        finally:
            set_file_translation_mode(False)
    return translate_file_dual_modes(source_path, output_dir, direction)


def _translate_file_full(
    source_path: Path, output_dir: Path, direction: str = "auto",
) -> dict[str, Path]:
    _clear_word_cache()
    suffix = source_path.suffix.lower()
    stem = source_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    sample = extract_text_from_file(source_path)
    if not sample.strip():
        raise ValueError("لم يتم العثور على نص في الملف")
    direction = resolve_direction(sample, direction)
    logger.info("Full translation (4 files): %s", source_path.name)
    with use_detected_field(sample):
        return _translate_file_full_body(
            source_path, output_dir, direction, sample, suffix, stem,
        )


def _translate_file_full_body(
    source_path: Path, output_dir: Path, direction: str, sample: str, suffix: str, stem: str,
) -> dict[str, Path]:
    logger.info("Step 1-2/4: literal + structured...")
    if suffix in (".docx", ".doc"):
        result = _translate_docx(source_path, output_dir, stem, direction)
    elif suffix == ".pdf":
        result = _translate_pdf(source_path, output_dir, stem, direction)
    elif suffix == ".txt":
        result = _translate_txt(source_path, output_dir, stem, direction)
    else:
        raise ValueError(f"نوع الملف غير مدعوم: {suffix}")

    logger.info("Step 3/4: line pairs...")
    result.update(_build_line_pairs_file(sample, direction, output_dir, stem))
    logger.info("Step 4/4: overlay...")
    result.update(_build_overlay_file(source_path, output_dir, stem, direction, sample))
    logger.info("Full translation done: %s", list(result.keys()))
    return result


# توافق مع الكود القديم
translate_file_three_modes = translate_file_two_modes
