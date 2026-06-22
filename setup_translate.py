"""تحميل حزم الترجمة المحلية عند أول تشغيل"""
import logging

import config  # noqa: F401 — يضبط ARGOS_PACKAGES_DIR قبل argostranslate

logger = logging.getLogger(__name__)


def install_translation_packages():
    try:
        import argostranslate.package
        import argostranslate.translate

        installed = argostranslate.package.get_installed_packages()
        has_ar_en = any(p.from_code == "ar" and p.to_code == "en" for p in installed)
        has_en_ar = any(p.from_code == "en" and p.to_code == "ar" for p in installed)

        if has_ar_en and has_en_ar:
            logger.info("حزم الترجمة مثبتة مسبقاً في %s", config.ARGOS_PACKAGES_DIR)
            return

        logger.info("جاري تحميل حزم الترجمة (أول مرة فقط)...")
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()

        for from_code, to_code in [("ar", "en"), ("en", "ar")]:
            pkg = next(
                (p for p in available if p.from_code == from_code and p.to_code == to_code),
                None,
            )
            if pkg:
                download_path = pkg.download()
                argostranslate.package.install_from_path(download_path)
                logger.info(f"تم تثبيت الترجمة: {from_code} → {to_code}")

        logger.info("اكتمل تثبيت حزم الترجمة")
    except Exception as e:
        logger.warning(f"تعذر تثبيت حزم الترجمة: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    install_translation_packages()
