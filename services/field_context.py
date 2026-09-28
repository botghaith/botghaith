"""يتعرّف على تخصص الملف من مصطلحاته، ويضبط معنى الكلمة الملتبسة."""

from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_local = threading.local()

# علامات مميزة. الكلمة العامة مثل cell و stress ليست علامة.
_MARKERS: dict[str, tuple[str, ...]] = {
    "medicine": (
        "patient", "diagnosis", "symptom", "biopsy", "tumor", "carcinoma",
        "hemoglobin", "ventricle", "aorta", "myocardial", "infarction",
        "antibiotic", "insulin", "sepsis", "dialysis", "hypertension",
        "pneumonia", "anesthesia", "cranial nerve", "blood pressure",
        "heart failure", "electrocardiogram", "anatomy", "artery",
        "kidney", "liver", "dosage", "therapy", "fracture",
    ),
    "biology": (
        "photosynthesis", "mitosis", "meiosis", "chromosome", "enzyme",
        "allele", "habitat", "ecosystem", "chloroplast", "mitochondria",
        "protein synthesis", "cell membrane", "organism", "bacteria",
        "species", "phylogeny", "gene expression", "ribosome",
    ),
    "chemistry": (
        "molar", "titration", "reagent", "alkane", "stoichiometry",
        "benzene", "covalent", "ionic bond", "anion", "cation",
        "precipitate", "solvent", "orbital", "molecule", "oxidation",
        "equilibrium constant", "periodic table", "catalyst",
    ),
    "chemical": (
        "distillation", "mass transfer", "heat exchanger", "reflux",
        "fluidized", "unit operation", "reactor design", "nusselt",
        "process control", "chemical reactor",
    ),
    "civil": (
        "reinforced concrete", "bending moment", "shear force", "footing",
        "rebar", "slump", "pavement", "soil mechanics", "retaining wall",
        "dead load", "live load", "structural analysis", "foundation",
        "consolidation", "highway",
    ),
    "mechanical": (
        "thermodynamics", "enthalpy", "entropy", "turbine", "crankshaft",
        "piston", "gear", "bearing", "machining", "refrigeration",
        "viscosity", "heat transfer", "shaft", "linkage", "cam",
    ),
    "electrical": (
        "resistor", "capacitor", "inductor", "transformer", "impedance",
        "kirchhoff", "semiconductor", "diode", "transistor",
        "alternating current", "power system", "circuit", "voltage",
        "load flow",
    ),
    "petroleum": (
        "reservoir", "drilling", "porosity", "permeability", "wellbore",
        "crude oil", "casing", "mud weight", "hydrocarbon",
        "petroleum", "reservoir engineering",
    ),
    "computer": (
        "algorithm", "compiler", "database", "operating system", "cache",
        "recursion", "data structure", "encryption", "boolean",
        "binary tree", "machine learning", "programming", "protocol",
        "software", "tcp",
    ),
    "math": (
        "theorem", "lemma", "eigenvalue", "polynomial", "vector space",
        "differential equation", "linear algebra", "axiom", "matrix",
        "integral", "derivative", "proof", "probability",
    ),
    "physics": (
        "quantum", "photon", "wavelength", "relativity", "momentum",
        "electric field", "magnetic field", "electron", "proton",
        "optics", "acceleration", "velocity", "newton",
    ),
    "law": (
        "plaintiff", "defendant", "jurisdiction", "statute", "liability",
        "verdict", "constitution", "legislation", "tort", "clause",
        "court", "contract",
    ),
    "psychology": (
        "psychotherapy", "cognition", "personality", "anxiety disorder",
        "cognitive behavioral", "phobia", "schizophrenia", "stimulus",
        "freud", "perception", "neurotic",
    ),
    "accounting": (
        "balance sheet", "ledger", "depreciation", "journal entry",
        "income statement", "audit", "debit", "credit", "equity",
        "assets", "liability",
    ),
}

# معنى الكلمة الملتبسة داخل التخصص فقط. ما نفرض كلمات الجمل العادية.
_SENSES: dict[str, dict[str, str]] = {
    "medicine": {
        "cell": "خلية",
        "tissue": "نسيج",
        "organ": "عضو",
        "plasma": "بلازما",
        "vessel": "وعاء",
        "culture": "مزرعة",
        "joint": "مفصل",
        "function": "وظيفة",
        "solution": "محلول",
        "stress": "إجهاد",
    },
    "biology": {
        "cell": "خلية",
        "tissue": "نسيج",
        "organ": "عضو",
        "strain": "سلالة",
        "plant": "نبات",
        "culture": "مزرعة",
        "function": "وظيفة",
        "solution": "محلول",
    },
    "chemistry": {
        "solution": "محلول",
        "bond": "رابطة",
        "acid": "حمض",
        "base": "قاعدة",
        "reduction": "اختزال",
    },
    "chemical": {
        "solution": "محلول",
        "bond": "رابطة",
        "reduction": "اختزال",
        "flow": "جريان",
        "plant": "منشأة",
    },
    "civil": {
        "concrete": "خرسانة",
        "beam": "جائز",
        "settlement": "هبوط",
        "stress": "إجهاد",
        "strain": "انفعال",
        "joint": "مفصل",
    },
    "mechanical": {
        "stress": "إجهاد",
        "strain": "انفعال",
        "spring": "نابض",
        "heat": "حرارة",
        "flow": "جريان",
        "force": "قوة",
    },
    "electrical": {
        "charge": "شحنة",
        "resistance": "مقاومة",
        "power": "قدرة",
        "phase": "طور",
    },
    "petroleum": {
        "pressure": "ضغط",
        "flow": "جريان",
        "gas": "غاز",
        "phase": "طور",
    },
    "computer": {
        "function": "دالة",
        "solution": "حل",
        "network": "شبكة",
        "signal": "إشارة",
    },
    "math": {
        "function": "دالة",
        "derivative": "مشتقة",
        "integral": "تكامل",
        "solution": "حل",
        "root": "جذر",
        "series": "متسلسلة",
        "limit": "نهاية",
    },
    "physics": {
        "force": "قوة",
        "mass": "كتلة",
        "charge": "شحنة",
        "wave": "موجة",
        "friction": "احتكاك",
        "energy": "طاقة",
    },
    "law": {
        "settlement": "تسوية",
        "charge": "تهمة",
        "bond": "سند",
    },
    "psychology": {
        "stress": "ضغط نفسي",
    },
    "accounting": {
        "charge": "قيد",
    },
}


def _word_pattern(term: str) -> re.Pattern[str]:
    if term.endswith(("s", "x", "z", "ch", "sh")):
        suffix = r"(?:'s|es)?"
    else:
        suffix = r"(?:'s|s)?"
    return re.compile(rf"(?<![\w]){re.escape(term)}{suffix}(?![\w])", re.IGNORECASE)


_SENSE_PATTERNS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    field: [
        (_word_pattern(word), arabic)
        for word, arabic in sorted(words.items(), key=lambda item: len(item[0]), reverse=True)
    ]
    for field, words in _SENSES.items()
}

_MARKER_RES: dict[str, list[tuple[str, re.Pattern[str] | None]]] = {}
for _field, _markers in _MARKERS.items():
    compiled: list[tuple[str, re.Pattern[str] | None]] = []
    for _marker in _markers:
        if " " in _marker:
            compiled.append((_marker, None))
        else:
            compiled.append((_marker, _word_pattern(_marker)))
    _MARKER_RES[_field] = compiled


def current_field() -> str | None:
    return getattr(_local, "field", None)


def set_field(name: str | None) -> None:
    _local.field = name or None


def sense_for(word: str) -> str | None:
    field = current_field()
    words = _SENSES.get(field or "")
    if not words:
        return None
    key = re.sub(r"\s+", " ", (word or "").strip().casefold())
    if key in words:
        return words[key]
    if len(key) >= 4 and key.endswith("es") and key[:-2] in words:
        return words[key[:-2]]
    if len(key) >= 3 and key.endswith("s") and not key.endswith("ss") and key[:-1] in words:
        return words[key[:-1]]
    return None


def detect_field(text: str) -> str | None:
    """يرجع التخصص إذا تجمعت علاماته بفارق واضح، وإلا لا يخمن."""
    hay = re.sub(r"\s+", " ", (text or "").casefold())
    if len(hay) < 40:
        return None
    scores: dict[str, int] = {}
    for field, markers in _MARKER_RES.items():
        hits = 0
        for marker, pattern in markers:
            if pattern is None:
                found = marker in hay
            else:
                found = pattern.search(hay) is not None
            if found:
                hits += 1
        if hits:
            scores[field] = hits
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_n = ranked[0]
    second_n = ranked[1][1] if len(ranked) > 1 else 0
    if best_n < 3:
        return None
    if second_n and best_n < second_n + 2:
        return None
    return best


def apply_field_terms(text: str, direction: str) -> str:
    """يستبدل الكلمة الملتبسة بمعنى التخصص المتعرف عليه."""
    if direction != "en_ar" or not text:
        return text
    patterns = _SENSE_PATTERNS.get(current_field() or "")
    if not patterns:
        return text
    for pattern, arabic in patterns:
        text = pattern.sub(arabic, text)
    return text


@contextmanager
def use_detected_field(text: str):
    detected = detect_field(text)
    previous = current_field()
    if detected:
        set_field(detected)
        logger.info("Detected translation field: %s", detected)
    try:
        yield detected or previous
    finally:
        set_field(previous)
