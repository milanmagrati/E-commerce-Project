#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  EcommerceAdmin — Zero-Touch Production Setup Script
#  Run once. Everything (server, systemd) is automated.
#  Usage:  bash setup.sh
# ═══════════════════════════════════════════════════════════
set -e

# ── Resolve absolute paths ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
DJANGO_DIR="$PROJECT_ROOT/myproject"
VENV_DIR="$PROJECT_ROOT/.venv"
GUNICORN_BIN="$VENV_DIR/bin/gunicorn"
CURRENT_USER="$(whoami)"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   EcommerceAdmin - Zero-Touch Setup      ║"
echo "╚══════════════════════════════════════════╝"
echo "  Project  : $PROJECT_ROOT"
echo "  User     : $CURRENT_USER"
echo ""

# ── 1. System dependencies (Python, MySQL) ─────────
echo "[1/8] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -q \
    python3 python3-venv python3-pip \
    mysql-server \
    python3-dev default-libmysqlclient-dev build-essential pkg-config
echo "    ✓ System packages installed"

# ── 2. Start & enable MySQL ────────────────────
echo "[2/8] Starting MySQL..."
sudo systemctl start mysql    && sudo systemctl enable mysql

# ── 3. MySQL database setup ──────────────────────────────
echo "[3/8] Setting up MySQL database..."
sudo mysql -e "
CREATE DATABASE IF NOT EXISTS ecommerceadmin CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER IF NOT EXISTS 'ecom_user'@'localhost' IDENTIFIED BY 'Ecom@2026Secure';
GRANT ALL PRIVILEGES ON ecommerceadmin.* TO 'ecom_user'@'localhost';
FLUSH PRIVILEGES;
"
echo "    ✓ Database ready"

# ── 4. Python virtual environment ───────────────────────
echo "[4/8] Creating virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "    ✓ .venv created"
else
    echo "    ✓ .venv already exists"
fi
source "$VENV_DIR/bin/activate"

# ── 5. Python packages ───────────────────────────────────
echo "[5/8] Installing Python packages..."
pip install -q --upgrade pip
pip install -q -r "$DJANGO_DIR/requirements.txt"
# Install gunicorn for standard WSGI serving
pip install -q gunicorn
echo "    ✓ Python packages installed"

# ── 6. Create .env ───────────────────────────────
echo "[6/8] Configuring .env..."
ENV_FILE="$DJANGO_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    # Fresh install — create full .env
    SECRET_KEY=$(python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
    cat > "$ENV_FILE" <<EOF
SECRET_KEY=${SECRET_KEY}
DEBUG=False

# Database
DB_NAME=ecommerceadmin
DB_USER=ecom_user
DB_PASSWORD=Ecom@2026Secure
DB_HOST=localhost
DB_PORT=3306

# NCM API
NCM_API_KEY=
NCM_API_BASE_URL=https://portal.nepalcanmove.com/api/v1
NCM_API_BASE_URL_V2=https://portal.nepalcanmove.com/api/v2

# Webhook
NCM_WEBHOOK_SECRET=
NCM_WEBHOOK_URL=

# Pick and Drop API
PND_API_KEY=
PND_API_SECRET=
PND_API_BASE_URL=https://pickndropnepal.com
EOF
    echo "    ✓ .env created"
else
    echo "    ✓ .env already configured"
fi

# ── 7. Migrations & static files ─────────────────────────
echo "[7/8] Running migrations and collecting static files..."
cd "$DJANGO_DIR"
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear 2>/dev/null || python manage.py collectstatic --noinput
cd "$PROJECT_ROOT"
echo "    ✓ Database migrated, static files collected"

# ── 8. Create systemd service (auto-starts on reboot) ────
echo "[8/8] Installing systemd service (ecomadmin)..."
SERVICE_FILE="/etc/systemd/system/ecomadmin.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=EcommerceAdmin — Gunicorn Server
After=network.target mysql.service
Requires=mysql.service

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${DJANGO_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=${DJANGO_DIR}/.env
ExecStart=${GUNICORN_BIN} --workers 3 --bind 0.0.0.0:8000 myproject.wsgi:application
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ecomadmin
sudo systemctl restart ecomadmin
echo "    ✓ systemd service 'ecomadmin' created, enabled, and started"

# ── 9. Health check ──────────────────────────────────────
echo "[9/9] Verifying everything is running..."
sleep 3

MYSQL_OK=false
APP_OK=false

sudo systemctl is-active --quiet mysql      && MYSQL_OK=true
sudo systemctl is-active --quiet ecomadmin  && APP_OK=true

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║            SETUP COMPLETE ✓              ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  MySQL    : $([ "$MYSQL_OK" = true ] && echo '✓ Running' || echo '✗ Check logs')"
echo "  App      : $([ "$APP_OK"   = true ] && echo '✓ Running on port 8000' || echo '✗ Check: journalctl -u ecomadmin -n 50')"
echo ""
echo "  Useful commands:"
echo "    View app logs   : sudo journalctl -u ecomadmin -f"
echo "    Restart app     : sudo systemctl restart ecomadmin"
echo "    Stop app        : sudo systemctl stop ecomadmin"
echo "    After git pull  : sudo systemctl restart ecomadmin"
echo ""
echo "  App is live at: http://$(hostname -I | awk '{print $1}'):8000"
echo ""
