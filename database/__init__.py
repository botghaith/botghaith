import logging

from config import is_supabase_enabled

logger = logging.getLogger(__name__)


def get_database():
    if is_supabase_enabled():
        try:
            from database.supabase_db import SupabaseDatabase

            return SupabaseDatabase()
        except Exception as exc:
            logger.warning(
                "Supabase غير متاح — التحويل إلى SQLite المحلي: %s",
                exc,
            )
    from database.db import Database
    return Database()
