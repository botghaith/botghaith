"""استخراج النص من الصور والملفات المصورة — دقة وترتيب قريب من الأصل"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
]

OCR_LANG = "ara+eng"
MIN_NATIVE_CHARS = 20
OCR_DPI = 220
MAX_IMAGE_SIDE = 2400
MIN_IMAGE_SIDE = 1400
OCR_TIMEOUT = 90
PDF_OCR_WORKERS = 4
EXTRACT_TIMEOUT = 600

_tesseract_ready = False


def _setup_tesseract():
    global _tesseract_ready
    if _tesseract_ready:
        return
    import pytesseract

    for path in TESSERACT_PATHS:
        if Path(path).exists():
            pytesseract.pytesseract.tesseract_cmd = path
            _tesseract_ready = True
            return

    found = os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH")
    if found and Path(found).exists():
        pytesseract.pytesseract.tesseract_cmd = found
        _tesseract_ready = True
        return

    raise RuntimeError(
        "لم يتم العثور على Tesseract OCR. ثبّته من: https://github.com/tesseract-ocr/tesseract"
    )


def _prepare_image(img):
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) < MIN_IMAGE_SIDE:
        scale = MIN_IMAGE_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        w, h = img.size

    if max(w, h) > MAX_IMAGE_SIDE:
        scale = MAX_IMAGE_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

    img = ImageOps.grayscale(img)
    img = ImageEnhance.Contrast(img).enhance(1.4)
    img = ImageEnhance.Sharpness(img).enhance(1.2)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def _structured_text_from_ocr_data(data: dict) -> tuple[str, float]:
    lines_map: dict[tuple, list[tuple[int, str, int]]] = {}
    confs: list[int] = []
    n = len(data.get("text", []))

    for i in range(n):
        word = (data["text"][i] or "").strip()
        if not word:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (ValueError, TypeError):
            conf = -1
        if conf >= 0:
            confs.append(conf)
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines_map.setdefault(key, []).append(
            (data["left"][i], word, data["top"][i])
        )

    output_lines: list[str] = []
    prev_top = None
    prev_par = None

    for key in sorted(lines_map.keys()):
        _, par, _ = key
        items = sorted(lines_map[key], key=lambda x: x[0])
        line_top = items[0][2]

        if prev_par is not None and par != prev_par:
            output_lines.append("")
        elif prev_top is not None and line_top - prev_top > 22:
            output_lines.append("")

        output_lines.append(" ".join(w[1] for w in items))
        prev_top = line_top
        prev_par = par

    avg_conf = sum(confs) / len(confs) if confs else 0.0
    return "\n".join(output_lines), avg_conf


def _ocr_structured(img, lang: str, config: str) -> tuple[str, float]:
    import pytesseract

    _setup_tesseract()
    data = pytesseract.image_to_data(
        img,
        lang=lang,
        config=config,
        timeout=OCR_TIMEOUT,
        output_type=pytesseract.Output.DICT,
    )
    return _structured_text_from_ocr_data(data)


def _ocr_image_pil(img) -> str:
    from PIL import ImageEnhance

    base = _prepare_image(img)
    variants = [base, ImageEnhance.Contrast(base).enhance(1.8)]
    configs = [
        (OCR_LANG, "--oem 1 --psm 1 -c preserve_interword_spaces=1"),
        (OCR_LANG, "--oem 1 --psm 3 -c preserve_interword_spaces=1"),
        (OCR_LANG, "--oem 1 --psm 6 -c preserve_interword_spaces=1"),
        ("ara", "--oem 1 --psm 3 -c preserve_interword_spaces=1"),
        ("eng", "--oem 1 --psm 3 -c preserve_interword_spaces=1"),
    ]

    best_text, best_score = "", -1.0
    for variant in variants:
        for lang, config in configs:
            try:
                text, conf = _ocr_structured(variant, lang, config)
            except Exception as e:
                logger.warning(f"OCR attempt failed ({lang}): {e}")
                continue
            if not text.strip():
                continue
            score = conf + min(len(text) / 150, 50)
            if score > best_score:
                best_text, best_score = text, score
    return best_text


def _extract_native_structured(page) -> str:
    blocks = page.get_text("dict").get("blocks", [])
    text_blocks = [b for b in blocks if b.get("type") == 0]
    text_blocks.sort(key=lambda b: (round(b["bbox"][1] / 8), b["bbox"][0]))

    output_lines: list[str] = []
    prev_bottom = None

    for block in text_blocks:
        block_top = block["bbox"][1]
        if prev_bottom is not None and block_top - prev_bottom > 18:
            output_lines.append("")

        block_lines = sorted(block.get("lines", []), key=lambda ln: ln["bbox"][1])
        for line in block_lines:
            spans = sorted(line.get("spans", []), key=lambda s: s["bbox"][0])
            line_text = "".join(s.get("text", "") for s in spans).strip()
            if line_text:
                output_lines.append(line_text)

        prev_bottom = block["bbox"][3]

    return "\n".join(output_lines)


def ocr_image(image_path: Path) -> str:
    from PIL import Image

    with Image.open(image_path) as raw:
        return _ocr_image_pil(raw)


def ocr_image_layout(image_path: Path) -> dict:
    """استخراج النص مع إحداثيات الكلمات والأسطر لترجمة الصور."""
    import pytesseract
    from PIL import Image

    _setup_tesseract()
    with Image.open(image_path) as raw:
        orig_w, orig_h = raw.size
        prepared = _prepare_image(raw.convert("RGB"))
    prep_w, prep_h = prepared.size
    scale_x = orig_w / prep_w
    scale_y = orig_h / prep_h

    data = pytesseract.image_to_data(
        prepared,
        lang=OCR_LANG,
        config="--oem 1 --psm 3 -c preserve_interword_spaces=1",
        timeout=OCR_TIMEOUT,
        output_type=pytesseract.Output.DICT,
    )

    lines_map: dict[tuple, list[dict]] = {}
    word_jobs: list[tuple] = []
    n = len(data.get("text", []))

    for i in range(n):
        word = (data["text"][i] or "").strip()
        if not word:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (ValueError, TypeError):
            conf = -1
        if conf >= 0 and conf < 10:
            continue

        left = int(data["left"][i] * scale_x)
        top = int(data["top"][i] * scale_y)
        width = int(data["width"][i] * scale_x)
        height = int(data["height"][i] * scale_y)
        x0, y0 = left, top
        x1, y1 = left + max(width, 1), top + max(height, 1)

        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines_map.setdefault(key, []).append({
            "word": word,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "left": left,
        })

    line_blocks: list[dict] = []
    full_lines: list[str] = []

    for key in sorted(lines_map.keys()):
        items = sorted(lines_map[key], key=lambda x: x["left"])
        line_text = " ".join(w["word"] for w in items)
        if not line_text.strip():
            continue
        x0 = min(w["x0"] for w in items)
        y0 = min(w["y0"] for w in items)
        x1 = max(w["x1"] for w in items)
        y1 = max(w["y1"] for w in items)
        line_blocks.append({
            "text": line_text,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        })
        full_lines.append(line_text)
        for w in items:
            word_jobs.append((w["word"], w["x0"], w["y0"], w["x1"], w["y1"], line_text))

    full_text, _ = _structured_text_from_ocr_data(data)
    if not full_text.strip():
        full_text = "\n".join(full_lines)

    return {
        "text": full_text,
        "width": orig_w,
        "height": orig_h,
        "words": word_jobs,
        "lines": line_blocks,
    }


def _ocr_pdf_page(page, temp_dir: Path, page_num: int) -> str:
    from PIL import Image

    pix = page.get_pixmap(dpi=OCR_DPI)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return _ocr_image_pil(img)


def _extract_page_text(page, temp_dir: Path, page_num: int) -> str:
    native = _extract_native_structured(page)
    plain_len = len(native.replace("\n", "").replace(" ", ""))
    if plain_len >= MIN_NATIVE_CHARS:
        return native
    ocr_text = _ocr_pdf_page(page, temp_dir, page_num)
    return ocr_text if ocr_text.strip() else native


def _ocr_page_job(args: tuple) -> tuple[int, str]:
    page_index, pdf_path_str, temp_dir_str = args
    import fitz

    doc = fitz.open(pdf_path_str)
    try:
        page = doc[page_index]
        text = _extract_page_text(page, Path(temp_dir_str), page_index + 1)
        return page_index, text
    finally:
        doc.close()


def extract_pdf_text_smart(pdf_path: Path, temp_dir: Path | None = None) -> str:
    import fitz

    temp_dir = temp_dir or pdf_path.parent
    doc = fitz.open(str(pdf_path))
    page_count = len(doc)
    doc.close()

    jobs = [(i, str(pdf_path), str(temp_dir)) for i in range(page_count)]
    results: dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=PDF_OCR_WORKERS) as pool:
        futures = {pool.submit(_ocr_page_job, job): job[0] for job in jobs}
        for fut in as_completed(futures):
            try:
                idx, text = fut.result()
                if text.strip():
                    results[idx] = text.strip()
            except Exception as e:
                logger.warning(f"OCR page failed: {e}")

    parts = [results[i] for i in sorted(results)]
    return "\n\n".join(parts)


def extract_text_smart(file_path: Path, temp_dir: Path | None = None) -> str:
    suffix = file_path.suffix.lower()
    temp_dir = temp_dir or file_path.parent

    if suffix == ".pdf":
        return extract_pdf_text_smart(file_path, temp_dir)

    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"):
        return ocr_image(file_path)

    raise ValueError(f"نوع الملف غير مدعوم لاستخراج النص: {suffix}")
