#!/usr/bin/env python3
"""
بوت تيليجرام تعليمي متكامل لطلاب الجامعات
من إعداد المهندس غيث اسعد
"""

import asyncio
import logging
import os
import sys

from telegram.error import Conflict
from telegram.ext import Application

import config  # noqa: F401 — ARGOS_PACKAGES_DIR
from config import BOT_TOKEN, is_supabase_enabled, prefer_local_for_files
from database import get_database
from handlers.admin import setup_admin_handlers
from handlers.exams import setup_exam_handlers
from handlers.pdf_tools import setup_pdf_handlers
from handlers.start import setup_start_handlers
from handlers.student import setup_student_handlers
from handlers.translation import setup_translation_handlers
from setup_translate import install_translation_packages
from services.translator import is_translator_ready

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def create_application(db) -> Application:
    """إنشاء التطبيق وتسجيل جميع المعالجات."""

    async def post_init(_application: Application) -> None:
        logger.info("Installing translation packages in background...")
        await asyncio.to_thread(install_translation_packages)
        if prefer_local_for_files():
            await asyncio.to_thread(is_translator_ready)
        logger.info("Translation packages ready")

    start_handlers, back_to_main = setup_start_handlers(db)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .connect_timeout(120.0)
        .read_timeout(600.0)
        .write_timeout(600.0)
        .get_updates_read_timeout(60.0)
        .build()
    )

    async def on_error(update, context):
        err = context.error
        if isinstance(err, Conflict):
            logger.error(
                "409 Conflict: البوت يعمل في مكانين (Render + جهازك). "
                "أوقف أحدهما — Render للتشغيل الدائم، أو جهازك للتجربة فقط."
            )
            return
        logger.exception("Unhandled error: %s", err)

    app.add_error_handler(on_error)

    for handler in start_handlers:
        app.add_handler(handler, group=-1)

    app.add_handler(setup_translation_handlers(back_to_main))
    app.add_handler(setup_pdf_handlers(db, back_to_main))

    for handler in setup_exam_handlers(db, back_to_main):
        app.add_handler(handler)

    for handler in setup_student_handlers(db):
        app.add_handler(handler)

    for handler in setup_admin_handlers(db, back_to_main):
        app.add_handler(handler)

    return app


def main() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN غير موجود! أضفه في Environment Variables (Render) أو ملف .env")
        sys.exit(1)

    db = get_database()

    app = create_application(db)
    backend = "Supabase" if is_supabase_enabled() else "SQLite"
    host = "Render (24/7)" if os.getenv("RENDER") else "Local (جهازك)"
    logger.info("🎓 البوت يعمل — %s | %s | إعداد المهندس غيث اسعد", host, backend)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
