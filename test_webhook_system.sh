#!/bin/bash

#============================================================
# 🧪 NCM Webhook System - Verification Test Script
#============================================================
# This script verifies all components of the NCM webhook system

set -e

echo "=================================================="
echo "🧪 NCM Webhook System - Comprehensive Test"
echo "=================================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test results
PASSED=0
FAILED=0

# Function to print test results
test_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ PASS${NC}: $2"
        ((PASSED++))
    else
        echo -e "${RED}❌ FAIL${NC}: $2"
        ((FAILED++))
    fi
}

echo -e "${BLUE}1. Checking Project Structure${NC}"
echo "=================================================="

# Check if key files exist
[ -f "./myproject/settings.py" ] && echo -e "${GREEN}✓${NC} settings.py found" || echo -e "${RED}✗${NC} settings.py NOT found"
[ -f "./ncm/webhook_handler.py" ] && echo -e "${GREEN}✓${NC} webhook_handler.py found" || echo -e "${RED}✗${NC} webhook_handler.py NOT found"
[ -f "./ncm/realtime_api.py" ] && echo -e "${GREEN}✓${NC} realtime_api.py found" || echo -e "${RED}✗${NC} realtime_api.py NOT found"
[ -f "./services/sms_service.py" ] && echo -e "${GREEN}✓${NC} sms_service.py found" || echo -e "${RED}✗${NC} sms_service.py NOT found"

echo ""
echo -e "${BLUE}2. Checking Django Installation${NC}"
echo "=================================================="

# Check Django version
python3 -c "import django; print('Django version:', django.VERSION)" 2>/dev/null
test_result $? "Django installed and importable"

# Check if database exists
[ -f "./db.sqlite3" ] && echo -e "${GREEN}✓${NC} Database file found" || echo -e "${YELLOW}⚠${NC} Database file not found (first run?)"

echo ""
echo -e "${BLUE}3. Checking Python Dependencies${NC}"
echo "=================================================="

# Check required packages
python3 -c "import requests" 2>/dev/null
test_result $? "requests package installed"

python3 -c "import pytz" 2>/dev/null
test_result $? "pytz package installed"

python3 -c "import openpyxl" 2>/dev/null
test_result $? "openpyxl package installed"

python3 -c "import decouple" 2>/dev/null
test_result $? "python-decouple package installed"

# Optional packages
python3 -c "import
 twilio" 2>/dev/null && echo -e "${GREEN}✓${NC} twilio installed (SMS ready)" || echo -e "${YELLOW}✓${NC} twilio not installed (optional)"

echo ""
echo -e "${BLUE}4. Checking Environment Configuration${NC}"
echo "=================================================="

# Check .env file
if [ -f "./.env" ]; then
    echo -e "${GREEN}✓${NC} .env file exists"
    
    # Check for required keys
    if grep -q "NCM_WEBHOOK_SECRET" .env; then
        echo -e "${GREEN}✓${NC} NCM_WEBHOOK_SECRET configured"
    else
        echo -e "${YELLOW}⚠${NC} NCM_WEBHOOK_SECRET not in .env"
    fi
    
    if grep -q "NCM_API_KEY" .env; then
        echo -e "${GREEN}✓${NC} NCM_API_KEY configured"
    else
        echo -e "${YELLOW}⚠${NC} NCM_API_KEY not in .env"
    fi
else
    echo -e "${RED}✗${NC} .env file NOT found - Create from .env.example"
fi

echo ""
echo -e "${BLUE}5. Checking Logs Directory${NC}"
echo "=================================================="

# Create logs directory if needed
if [ ! -d "./logs" ]; then
    mkdir -p logs
    echo -e "${GREEN}✓${NC} Created logs directory"
else
    echo -e "${GREEN}✓${NC} logs directory exists"
fi

[ -w "./logs" ] 
test_result $? "logs directory is writable"

echo ""
echo -e "${BLUE}6. Django Models Check${NC}"
echo "=================================================="

# Check if models are importable
python3 << 'EOF' 2>/dev/null
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from dashboard.models import Order, OrderActivityLog
from ncm.models import WebhookLog, NCMBulkLog
print("✓ All models imported successfully")
EOF

test_result $? "Django models importable"

echo ""
echo -e "${BLUE}7. Webhook URL Check${NC}"
echo "=================================================="

# Check if URL patterns include webhook
python3 << 'EOF' 2>/dev/null
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.urls import get_resolver
resolver = get_resolver()

# Check for webhook URL
try:
    webhook_pattern = resolver.url_patterns
    has_webhook = any('webhook' in str(p.pattern) for p in webhook_pattern)
    if has_webhook:
        print("✓ Webhook URL pattern found in routing")
    else:
        print("✗ Webhook URL pattern NOT found")
except:
    print("✗ Could not check URL patterns")
EOF

echo ""
echo -e "${BLUE}8. Services Import Check${NC}"
echo "=================================================="

python3 << 'EOF' 2>/dev/null
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

try:
    from services.ncm_service import NCMService
    print("✓ NCMService imported")
except ImportError as e:
    print(f"✗ NCMService import failed: {e}")

try:
    from services.sms_service import SMSService
    print("✓ SMSService imported")
except ImportError as e:
    print(f"✗ SMSService import failed: {e}")

try:
    from ncm.webhook_handler import NCMWebhookHandler
    print("✓ NCMWebhookHandler imported")
except ImportError as e:
    print(f"✗ NCMWebhookHandler import failed: {e}")
EOF

echo ""
echo -e "${BLUE}9. Testing Webhook Handler${NC}"
echo "=================================================="

python3 << 'EOF' 2>/dev/null
import os
import django
import json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from ncm.webhook_handler import NCMWebhookHandler

handler = NCMWebhookHandler()

# Test payload
payload = {
    'webhook_id': 'test-webhook-001',
    'event': 'test',
    'test': True
}

try:
    result = handler.process_webhook(payload)
    if result.get('success'):
        print("✓ Webhook handler test payload processed successfully")
    else:
        print(f"✗ Webhook handler failed: {result.get('message')}")
except Exception as e:
    print(f"✗ Webhook handler error: {e}")
EOF

echo ""
echo -e "${BLUE}10. SMS Service Test${NC}"
echo "=================================================="

python3 << 'EOF' 2>/dev/null
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from services.sms_service import SMSService

sms = SMSService()
print(f"✓ SMS Service initialized (Provider: {sms.provider})")

# Test SMS message preparation
message = sms._prepare_message('ORD-001', 'delivered')
if message:
    print("✓ SMS message template working")
else:
    print("✗ SMS message template failed")
EOF

echo ""
echo -e "${BLUE}11. Real-time API Endpoints${NC}"
echo "=================================================="

python3 << 'EOF' 2>/dev/null
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

try:
    from ncm import realtime_api
    
    endpoints = [
        'api_get_order_status',
        'api_sync_order_status',
        'api_get_orders_status_batch',
        'api_get_order_activity_log',
        'api_check_pending_ncm_updates'
    ]
    
    for endpoint in endpoints:
        if hasattr(realtime_api, endpoint):
            print(f"✓ {endpoint} found")
        else:
            print(f"✗ {endpoint} NOT found")
            
except Exception as e:
    print(f"✗ Error checking endpoints: {e}")
EOF

echo ""
echo -e "${BLUE}12. Settings Configuration${NC}"
echo "=================================================="

python3 << 'EOF' 2>/dev/null
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings

configs = {
    'NCM_API_KEY': 'NCM API Key',
    'NCM_WEBHOOK_SECRET': 'Webhook Secret',
    'SMS_ENABLED': 'SMS Enabled',
    'SMS_PROVIDER': 'SMS Provider',
    'ORDER_AUTO_SYNC_INTERVAL': 'Auto-sync Interval',
    'TIME_ZONE': 'Time Zone',
}

for key, label in configs.items():
    if hasattr(settings, key):
        value = getattr(settings, key)
        if 'SECRET' in key or 'KEY' in key or 'TOKEN' in key or 'PASSWORD' in key:
            print(f"✓ {label}: [HIDDEN]")
        else:
            print(f"✓ {label}: {value}")
    else:
        print(f"⚠ {label}: Not configured")
EOF

echo ""
echo "=================================================="
echo -e "${BLUE}📊 Test Summary${NC}"
echo "=================================================="
echo -e "Passed: ${GREEN}${PASSED}${NC}"
echo -e "Failed: ${RED}${FAILED}${NC}"

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ All tests passed! System is ready.${NC}"
    exit 0
else
    echo -e "${YELLOW}⚠ Some tests failed. Please review the output above.${NC}"
    exit 1
fi
