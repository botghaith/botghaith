"""تحميل حزم الترجمة المحلية عند أول تشغيل"""
import logging
import os
import sys

import config  # noqa: F401 — يضبط ARGOS_PACKAGES_DIR قبل argostranslate

logger = logging.getLogger(__name__)


def install_translation_packages(*, required: bool | None = None) -> bool:
    if required is None:
        required = bool(os.getenv("RENDER") or os.getenv("REQUIRE_ARGOS", ""))

    try:
        import argostranslate.package
        import argostranslate.translate

        installed = argostranslate.package.get_installed_packages()
        has_ar_en = any(p.from_code == "ar" and p.to_code == "en" for p in installed)
        has_en_ar = any(p.from_code == "en" and p.to_code == "ar" for p in installed)

        if has_ar_en and has_en_ar:
            logger.info("حزم الترجمة مثبتة مسبقاً في %s", config.ARGOS_PACKAGES_DIR)
            return True

        logger.info("جاري تحميل حزم الترجمة (أول مرة فقط)...")
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()

        for from_code, to_code in [("ar", "en"), ("en", "ar")]:
            if any(p.from_code == from_code and p.to_code == to_code for p in installed):
                continue
            pkg = next(
                (p for p in available if p.from_code == from_code and p.to_code == to_code),
                None,
            )
            if not pkg:
                raise RuntimeError(f"حزمة الترجمة غير متوفرة: {from_code} → {to_code}")
            download_path = pkg.download()
            argostranslate.package.install_from_path(download_path)
            logger.info("تم تثبيت الترجمة: %s → %s", from_code, to_code)

        logger.info("اكتمل تثبيت حزم الترجمة")
        return True
    except Exception as e:
        msg = f"تعذر تثبيت حزم الترجمة: {e}"
        if required:
            logger.error(msg)
            raise
        logger.warning(msg)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok = install_translation_packages(required=bool(os.getenv("RENDER")))
    if not ok:
        sys.exit(1)
