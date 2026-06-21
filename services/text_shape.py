"""تنسيق العربية والإنجليزية — خطوط، اتجاه، وربط الحروف"""
import re
from pathlib import Path

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
EN_RE = re.compile(r"[A-Za-z]")

ARABIC_FONTS = [
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/trado.ttf",
]


def has_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text or ""))


def is_mostly_arabic(text: str) -> bool:
    sample = (text or "")[:500]
    ar = len(ARABIC_RE.findall(sample))
    en = len(EN_RE.findall(sample))
    return ar > en


def find_arabic_font() -> str | None:
    for fp in ARABIC_FONTS:
        if Path(fp).exists():
            return fp
    return None


def shape_for_pdf(text: str) -> str:
    """للعرض في PDF — ربط الحروف واتجاه RTL"""
    if not has_arabic(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def set_run_font(run, name: str = "Tahoma", size: int = 11, small: bool = False):
    run.font.name = name
    run.font.size = Pt(8 if small else size)
    r = run._element
    r_pr = r.get_or_add_rPr()
    r_fonts = OxmlElement("w:rFonts")
    r_fonts.set(qn("w:ascii"), name)
    r_fonts.set(qn("w:hAnsi"), name)
    r_fonts.set(qn("w:cs"), name)
    r_pr.append(r_fonts)


def set_paragraph_direction(para, rtl: bool):
    p_pr = para._element.get_or_add_pPr()
    for child in list(p_pr):
        if child.tag in (qn("w:bidi"), qn("w:jc")):
            p_pr.remove(child)
    if rtl:
        bidi = OxmlElement("w:bidi")
        p_pr.append(bidi)
        jc = OxmlElement("w:jc")
        jc.set(qn("w:val"), "right")
        p_pr.append(jc)
    else:
        jc = OxmlElement("w:jc")
        jc.set(qn("w:val"), "left")
        p_pr.append(jc)


def style_paragraph(para, text: str, direction: str, base_size: int = 12):
    rtl = direction == "en_ar" or is_mostly_arabic(text)
    set_paragraph_direction(para, rtl)
    for run in para.runs:
        if run.text:
            set_run_font(run, "Tahoma", base_size)


def add_styled_run(para, text: str, small: bool = False, arabic: bool | None = None):
    run = para.add_run(text)
    use_arabic = arabic if arabic is not None else has_arabic(text)
    set_run_font(run, "Tahoma", 8 if small else 12, small=small)
    return run
