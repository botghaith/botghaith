import logging
import socket
from urllib.parse import urlparse

from supabase import Client, create_client

from config import SUPABASE_SECRET_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

_client: Client | None = None


def check_supabase_reachable() -> None:
    """يتأكد أن عنوان المشروع يُحل قبل استخدام العميل."""
    host = urlparse(SUPABASE_URL or "").hostname or ""
    if not host:
        raise RuntimeError("SUPABASE_URL غير صالح")
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError(
            f"تعذر الوصول إلى Supabase ({host}) — المشروع محذوف أو الدومين غير موجود"
        ) from exc


def get_supabase() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
            raise RuntimeError("Supabase غير مُعد — أضف SUPABASE_URL و SUPABASE_SECRET_KEY")
        check_supabase_reachable()
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
        logger.info("Supabase client initialized")
    return _client
