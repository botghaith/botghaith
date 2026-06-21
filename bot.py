#!/usr/bin/env python3
"""
بوت تيليجرام تعليمي متكامل لطلاب الجامعات
من إعداد المهندس غيث اسعد
"""

import logging
import sys

from telegram.ext import Application

from config import BOT_TOKEN
from database.db import Database
from handlers.start import setup_start_handlers
from handlers.translation import setup_translation_handlers
from handlers.pdf_tools import setup_pdf_handlers
from handlers.exams import setup_exam_handlers
from handlers.student import setup_student_handlers
from handlers.admin import setup_admin_handlers
from setup_translate import install_translation_packages

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN غير موجود! أنشئ ملف .env")
        sys.exit(1)

    db = Database()
    install_translation_packages()

    start_handlers, back_to_main = setup_start_handlers(db)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .connect_timeout(120.0)
        .read_timeout(600.0)
        .write_timeout(600.0)
        .get_updates_read_timeout(60.0)
        .build()
    )

    async def on_error(update, context):
        logger.exception("Unhandled error: %s", context.error)

    app.add_error_handler(on_error)

    for handler in start_handlers:
        app.add_handler(handler, group=-1)

    app.add_handler(setup_translation_handlers(db, back_to_main))
    app.add_handler(setup_pdf_handlers(db, back_to_main))

    for handler in setup_exam_handlers(db, back_to_main):
        app.add_handler(handler)

    for handler in setup_student_handlers(db):
        app.add_handler(handler)

    for handler in setup_admin_handlers(db, back_to_main):
        app.add_handler(handler)

    logger.info("🎓 البوت التعليمي يعمل الآن — إعداد المهندس غيث اسعد")
    import asyncio

async def run():
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await app.updater.idle()

if __name__ == "__main__":
    asyncio.run(run())



