#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  EcommerceAdmin — Zero-Touch Production Setup Script
#  Run once. Everything (server, Redis, systemd) is automated.
#  Usage:  bash setup.sh
# ═══════════════════════════════════════════════════════════
set -e

# ── Resolve absolute paths ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
DJANGO_DIR="$PROJECT_ROOT/myproject"
VENV_DIR="$PROJECT_ROOT/.venv"
DAPHNE_BIN="$VENV_DIR/bin/daphne"
CURRENT_USER="$(whoami)"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   EcommerceAdmin - Zero-Touch Setup      ║"
echo "╚══════════════════════════════════════════╝"
echo "  Project  : $PROJECT_ROOT"
echo "  User     : $CURRENT_USER"
echo ""

# ── 1. System dependencies (Python, MySQL, Redis) ─────────
echo "[1/9] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -q \
    python3 python3-venv python3-pip \
    mysql-server \
    python3-dev default-libmysqlclient-dev build-essential pkg-config \
    redis-server
echo "    ✓ System packages installed"

# ── 2. Start & enable MySQL and Redis ────────────────────
echo "[2/9] Starting MySQL and Redis..."
sudo systemctl start mysql    && sudo systemctl enable mysql
sudo systemctl start redis-server && sudo systemctl enable redis-server
# Verify Redis
redis-cli ping > /dev/null && echo "    ✓ Redis is running" || { echo "    ✗ Redis failed to start"; exit 1; }

# ── 3. MySQL database setup ──────────────────────────────
echo "[3/9] Setting up MySQL database..."
sudo mysql -e "
CREATE DATABASE IF NOT EXISTS ecommerceadmin CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER IF NOT EXISTS 'ecom_user'@'localhost' IDENTIFIED BY 'Ecom@2026Secure';
GRANT ALL PRIVILEGES ON ecommerceadmin.* TO 'ecom_user'@'localhost';
FLUSH PRIVILEGES;
"
echo "    ✓ Database ready"

# ── 4. Python virtual environment ───────────────────────
echo "[4/9] Creating virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "    ✓ .venv created"
else
    echo "    ✓ .venv already exists"
fi
source "$VENV_DIR/bin/activate"

# ── 5. Python packages ───────────────────────────────────
echo "[5/9] Installing Python packages..."
pip install -q --upgrade pip
pip install -q -r "$DJANGO_DIR/requirements.txt"
echo "    ✓ Python packages installed"

# ── 6. Create / patch .env ───────────────────────────────
echo "[6/9] Configuring .env..."
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

# Redis — required for real-time WebSockets (Django Channels)
REDIS_URL=redis://127.0.0.1:6379

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
    # .env exists — make sure REDIS_URL is present
    if ! grep -q "^REDIS_URL" "$ENV_FILE"; then
        echo "" >> "$ENV_FILE"
        echo "# Redis — required for real-time WebSockets (Django Channels)" >> "$ENV_FILE"
        echo "REDIS_URL=redis://127.0.0.1:6379" >> "$ENV_FILE"
        echo "    ✓ REDIS_URL added to existing .env"
    else
        echo "    ✓ .env already configured"
    fi
fi

# ── 7. Migrations & static files ─────────────────────────
echo "[7/9] Running migrations and collecting static files..."
cd "$DJANGO_DIR"
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear 2>/dev/null || python manage.py collectstatic --noinput
cd "$PROJECT_ROOT"
echo "    ✓ Database migrated, static files collected"

# ── 8. Create systemd service (auto-starts on reboot) ────
echo "[8/9] Installing systemd service (ecomadmin)..."
SERVICE_FILE="/etc/systemd/system/ecomadmin.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=EcommerceAdmin — Daphne ASGI Server
Documentation=https://github.com/django/daphne
After=network.target mysql.service redis-server.service
Requires=mysql.service redis-server.service

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${DJANGO_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=${DJANGO_DIR}/.env
ExecStart=${DAPHNE_BIN} -b 0.0.0.0 -p 8000 myproject.asgi:application
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
REDIS_OK=false
APP_OK=false

sudo systemctl is-active --quiet mysql      && MYSQL_OK=true
sudo systemctl is-active --quiet redis-server && REDIS_OK=true
sudo systemctl is-active --quiet ecomadmin  && APP_OK=true

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║            SETUP COMPLETE ✓              ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  MySQL    : $([ "$MYSQL_OK" = true ] && echo '✓ Running' || echo '✗ Check logs')"
echo "  Redis    : $([ "$REDIS_OK" = true ] && echo '✓ Running' || echo '✗ Check logs')"
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
