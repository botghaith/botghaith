import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = Path(os.getenv("TEMP_DIR", DATA_DIR / "temp"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "bot.db"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "qhaith").lstrip("@").lower()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL", "")


def is_supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def use_online_translate() -> bool:
    """ترجمة أونلاين افتراضياً — Argos للملفات فقط كاحتياط."""
    if os.getenv("USE_OFFLINE_TRANSLATE", "") == "1":
        return False
    return os.getenv("USE_ONLINE_TRANSLATE", "1") == "1"


def use_fast_file_translation() -> bool:
    """قديم — ملفان نص/PDF مبسّط."""
    return os.getenv("FILE_TRANSLATE_FAST", "") == "1"


def file_translation_mode() -> str:
    """dual = ملفان (بنفس الترتيب + فوق الكلمات) | full = 4 ملفات"""
    return os.getenv("FILE_TRANSLATE_MODE", "dual")


def use_dual_file_translation() -> bool:
    return file_translation_mode() == "dual"


def use_full_file_translation() -> bool:
    return file_translation_mode() == "full" or os.getenv("FILE_TRANSLATE_FULL", "") == "1"


def translation_use_supabase() -> bool:
    """الترجمة لا تستخدم Supabase افتراضياً — أسرع على Render."""
    return os.getenv("TRANSLATION_USE_SUPABASE", "") == "1"


def prefer_local_for_files() -> bool:
    """Argos محلي — ترجمة كلمة بكلمة صحيحة للملفات."""
    if os.getenv("FILE_TRANSLATE_ONLINE", "") == "1":
        return False
    if os.getenv("FILE_TRANSLATE_LOCAL", "") == "1":
        return True
    return bool(os.getenv("RENDER"))


def is_render_host() -> bool:
    return bool(os.getenv("RENDER"))


def file_max_paragraphs() -> int:
    return int(os.getenv("FILE_MAX_PARAGRAPHS", "120"))


def telegram_connect_timeout() -> float:
    return float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "180"))


def telegram_read_timeout() -> float:
    return float(os.getenv("TELEGRAM_READ_TIMEOUT", "1200"))


def telegram_write_timeout() -> float:
    return float(os.getenv("TELEGRAM_WRITE_TIMEOUT", "1200"))


def file_download_max_attempts() -> int:
    return int(os.getenv("FILE_DOWNLOAD_MAX_ATTEMPTS", "8"))


def file_download_retry_wait() -> float:
    return float(os.getenv("FILE_DOWNLOAD_RETRY_WAIT", "10"))


def file_prepare_timeout() -> float:
    return float(os.getenv("FILE_PREPARE_TIMEOUT", "300"))


def file_structured_timeout() -> float:
    return float(os.getenv("FILE_STRUCTURED_TIMEOUT", "900"))


def file_overlay_timeout() -> float:
    return float(os.getenv("FILE_OVERLAY_TIMEOUT", "1800"))


CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "mcqthr").lstrip("@")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/mcqthr")
CHANNEL_REQUIRED = os.getenv("CHANNEL_REQUIRED", "1") == "1"

WELCOME_MESSAGE = """مرحباً بك! 👋

🎓 **بوت تعليمي متكامل لطلاب الجامعات**
من إعداد **المهندس غيث اسعد**

اختر القسم الذي تريده من القائمة أدناه:

📚 الترجمة — نصوص وملفات
📋 واجهة تقرير — غلاف PDF / Word
📄 أدوات PDF والملفات
📝 الامتحانات الإلكترونية
🧑‍🎓 حسابي الطلابي

جميع الأدوات تعمل **محلياً ومجاناً**."""

DATA_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

ARGOS_PACKAGES_DIR = Path(os.getenv("ARGOS_PACKAGES_DIR", DATA_DIR / "argos-packages"))
ARGOS_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
os.environ["ARGOS_PACKAGES_DIR"] = str(ARGOS_PACKAGES_DIR.resolve())
