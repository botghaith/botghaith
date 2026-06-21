import logging

from supabase import Client, create_client

from config import SUPABASE_SECRET_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
            raise RuntimeError("Supabase غير مُعد — أضف SUPABASE_URL و SUPABASE_SECRET_KEY")
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
        logger.info("Supabase client initialized")
    return _client
