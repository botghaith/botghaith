#!/bin/bash
# إعداد سيرفر Oracle Cloud Ubuntu لتشغيل البوت 24/7
set -euo pipefail

BOT_USER="${SUDO_USER:-ubuntu}"
BOT_HOME="/home/${BOT_USER}"
BOT_DIR="${BOT_HOME}/botghaith"
REPO="https://github.com/botghaith/botghaith.git"
SERVICE_NAME="botghaith"

echo "==> تحديث النظام..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

echo "==> تثبيت الحزم..."
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-venv python3-pip git \
    tesseract-ocr tesseract-ocr-ara \
    libgl1 libglib2.0-0

echo "==> استنساخ المشروع..."
if [ ! -d "${BOT_DIR}/.git" ]; then
    git clone "${REPO}" "${BOT_DIR}"
else
    cd "${BOT_DIR}"
    git pull origin main
fi

cd "${BOT_DIR}"

echo "==> إنشاء البيئة الافتراضية..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

if [ ! -f .env ]; then
    cp deploy/oracle/env.template .env
    echo ""
    echo "!!! أضف BOT_TOKEN و SUPABASE_SECRET_KEY في: ${BOT_DIR}/.env"
    echo "    nano ${BOT_DIR}/.env"
    echo ""
fi

echo "==> تثبيت خدمة systemd..."
sudo cp deploy/oracle/botghaith.service /etc/systemd/system/${SERVICE_NAME}.service
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}

if ! grep -qE '^BOT_TOKEN=.+' .env 2>/dev/null; then
    echo "عدّل .env أولاً ثم شغّل: sudo systemctl start ${SERVICE_NAME}"
else
    sudo systemctl restart ${SERVICE_NAME}
    echo "==> البوت يعمل!"
    sudo systemctl status ${SERVICE_NAME} --no-pager
fi

echo ""
echo "أوامر مفيدة:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
echo "  sudo systemctl restart ${SERVICE_NAME}"
