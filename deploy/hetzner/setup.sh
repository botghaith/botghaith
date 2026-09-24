#!/bin/bash
# إعداد سيرفر Hetzner Ubuntu لتشغيل البوت 24/7
set -euo pipefail

BOT_USER="bot"
BOT_HOME="/home/${BOT_USER}"
BOT_DIR="${BOT_HOME}/botghaith"
REPO="https://github.com/botghaith/botghaith.git"
SERVICE_NAME="botghaith"

if [ "$(id -u)" -ne 0 ]; then
    echo "شغّل السكربت كـ root: sudo bash deploy/hetzner/setup.sh"
    exit 1
fi

echo "==> تحديث النظام..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

echo "==> تثبيت الحزم..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-venv python3-pip git \
    tesseract-ocr tesseract-ocr-ara \
    libgl1 libglib2.0-0 \
    libreoffice-impress

if ! id -u "${BOT_USER}" >/dev/null 2>&1; then
    echo "==> إنشاء المستخدم ${BOT_USER}..."
    useradd --create-home --shell /bin/bash "${BOT_USER}"
fi

echo "==> استنساخ المشروع..."
if [ ! -d "${BOT_DIR}/.git" ]; then
    sudo -u "${BOT_USER}" git clone "${REPO}" "${BOT_DIR}"
else
    sudo -u "${BOT_USER}" git -C "${BOT_DIR}" pull origin main
fi

cd "${BOT_DIR}"

echo "==> إنشاء البيئة الافتراضية..."
sudo -u "${BOT_USER}" python3 -m venv venv
sudo -u "${BOT_USER}" ./venv/bin/pip install --upgrade pip -q
sudo -u "${BOT_USER}" ./venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
    sudo -u "${BOT_USER}" cp deploy/hetzner/env.template .env
    echo ""
    echo "!!! أضف BOT_TOKEN في: ${BOT_DIR}/.env"
    echo "    nano ${BOT_DIR}/.env"
    echo ""
fi

echo "==> تثبيت خدمة systemd..."
cp deploy/hetzner/botghaith.service /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

if ! grep -qE '^BOT_TOKEN=.+' .env 2>/dev/null; then
    echo "عدّل .env أولاً ثم شغّل: systemctl start ${SERVICE_NAME}"
else
    chown "${BOT_USER}:${BOT_USER}" .env
    chmod 600 .env
    systemctl restart ${SERVICE_NAME}
    echo "==> البوت يعمل!"
    systemctl status ${SERVICE_NAME} --no-pager
fi

echo ""
echo "أوامر مفيدة:"
echo "  systemctl status ${SERVICE_NAME}"
echo "  journalctl -u ${SERVICE_NAME} -f"
echo "  systemctl restart ${SERVICE_NAME}"
