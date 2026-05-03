#!/bin/bash
set -e

echo "======================================"
echo "  EcommerceAdmin - Auto Setup Script  "
echo "======================================"

# ── 1. Install system dependencies ────────────────────────────────────────────
echo ""
echo "[1/7] Installing system dependencies..."
sudo apt update -qq
sudo apt install -y python3 python3-venv python3-pip \
    mysql-server \
    python3-dev default-libmysqlclient-dev build-essential pkg-config

# ── 2. Start MySQL ─────────────────────────────────────────────────────────────
echo ""
echo "[2/7] Starting MySQL service..."
sudo systemctl start mysql
sudo systemctl enable mysql

# ── 3. Create database and user ────────────────────────────────────────────────
echo ""
echo "[3/7] Setting up MySQL database..."
sudo mysql -e "
CREATE DATABASE IF NOT EXISTS ecommerceadmin CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER IF NOT EXISTS 'ecom_user'@'localhost' IDENTIFIED BY 'Ecom@2026Secure';
GRANT ALL PRIVILEGES ON ecommerceadmin.* TO 'ecom_user'@'localhost';
FLUSH PRIVILEGES;
"
echo "    Database 'ecommerceadmin' ready."

# ── 4. Create virtual environment ─────────────────────────────────────────────
echo ""
echo "[4/7] Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# ── 5. Install Python packages ────────────────────────────────────────────────
echo ""
echo "[5/7] Installing Python packages..."
pip install -q -r myproject/requirements.txt

# ── 6. Create .env file ───────────────────────────────────────────────────────
echo ""
echo "[6/7] Creating .env file..."
if [ ! -f myproject/.env ]; then
    SECRET_KEY=$(python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
    cat > myproject/.env <<EOF
SECRET_KEY=${SECRET_KEY}
DEBUG=True

# MySQL Database Configuration
DB_NAME=ecommerceadmin
DB_USER=ecom_user
DB_PASSWORD=Ecom@2026Secure
DB_HOST=localhost
DB_PORT=3306

# NCM API Configuration
NCM_API_KEY=
NCM_API_BASE_URL=https://portal.nepalcanmove.com/api/v1
NCM_API_BASE_URL_V2=https://portal.nepalcanmove.com/api/v2

# Webhook Configuration
NCM_WEBHOOK_SECRET=
NCM_WEBHOOK_URL=

# Pick and Drop API Configuration
PND_API_KEY=
PND_API_SECRET=
PND_API_BASE_URL=https://pickndropnepal.com
EOF
    echo "    .env file created."
else
    echo "    .env already exists, skipping."
fi

# ── 7. Run migrations and load data ───────────────────────────────────────────
echo ""
echo "[7/7] Running migrations and loading data..."
cd myproject
python manage.py migrate
python manage.py loaddata datadump.json
cd ..

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "======================================"
echo "  Setup Complete!"
echo "======================================"
echo ""
echo "  To start the server:"
echo "    source .venv/bin/activate"
echo "    cd myproject"
echo "    python manage.py runserver"
echo ""
