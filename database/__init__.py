from config import is_supabase_enabled


def get_database():
    if is_supabase_enabled():
        from database.supabase_db import SupabaseDatabase
        return SupabaseDatabase()
    from database.db import Database
    return Database()
