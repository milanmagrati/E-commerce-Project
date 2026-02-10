# Webhook Configuration Audit Report

**Generated:** February 10, 2026

---

## 📊 Executive Summary

**✅ WEBHOOKS ARE CONFIGURED** - Your system has NCM webhook endpoints set up and ready to receive status updates from the Nepal Can Move (NCM) logistics provider.

---

## 🔍 Current Webhook Configuration

### 1. **Webhook Endpoints**

#### **Endpoint 1: NCM Webhook (Primary)**
- **URL:** `http://your-domain/ncm/webhook/`
- **HTTP Method:** POST
- **Protection:** @require_POST decorator
- **Purpose:** Receive order status updates from NCM API
- **Location:** `/ncm/views.py` - `ncm_webhook()` function (line 239)

#### **Endpoint 2: Logistics Webhook (Secondary)**
- **URL:** `http://your-domain/logistics/webhook/ncm/`
- **HTTP Method:** POST  
- **Protection:** @require_POST + @csrf_exempt
- **Purpose:** Receive status updates from logistics provider
- **Location:** `/logistics/views.py` - `ncm_webhook()` function (line 20)

---

## 📝 Webhook Functionality Details

### **NCM Webhook (Primary - Recommended)**

**File:** `/ncm/views.py` (lines 238-320)

**Capabilities:**
```python
def ncm_webhook(request):
    - Receives order status updates from NCM
    - Supports single or bulk order updates
    - Handles test webhooks (payload.get('test'))
    - Logs all received events
    - Maps NCM status to system status
    - Creates activity logs for audit trail
    - Returns JSON responses
```

**Expected Payload Format:**
```json
{
  "event": "order_status_changed",
  "timestamp": "2026-02-10T11:30:00Z",
  "status": "Delivered",
  "order_id": 12345,
  "test": false
}
```

**Response on Success:**
```json
{
  "success": true,
  "message": "Webhook processed",
  "event": "order_status_changed",
  "status": "Delivered",
  "updated_orders": [
    {
      "order_number": "ORD000074",
      "ncm_order_id": 12345,
      "new_status": "Delivered"
    }
  ]
}
```

---

## 🔧 Webhook Management Methods

Available in `/services/ncm_service.py`:

### **1. Register Webhook URL**
```python
set_webhook_url(webhook_url: str) -> dict
- Endpoint: /vendor/webhook
- Method: POST
- Purpose: Register your webhook URL with NCM
- Usage: service.set_webhook_url('http://yourdomain.com/ncm/webhook/')
```

### **2. Test Webhook**
```python
test_webhook(webhook_url: str) -> dict
- Endpoint: /vendor/webhook/test
- Method: POST
- Purpose: Send test payload to your webhook
- Usage: service.test_webhook('http://yourdomain.com/ncm/webhook/')
```

---

## ✅ What's Working

1. ✅ **Webhook endpoints configured** - Both primary and secondary endpoints ready
2. ✅ **NCM API service methods exist** - `set_webhook_url()` and `test_webhook()` available
3. ✅ **Request logging** - All webhooks logged for debugging
4. ✅ **Error handling** - Proper 404 and 500 responses
5. ✅ **Status mapping** - NCM status automatically mapped to system status
6. ✅ **Activity logging** - OrderActivityLog created for audit trail
7. ✅ **Test mode support** - Can handle test webhooks from NCM

---

## ⚠️ What Needs to be Done

### **CRITICAL - Must Configure:**

1. **Register Webhook URL with NCM**
   - Your webhook endpoint needs to be registered with NCM's API
   - NCM needs to know where to send the status updates
   - Currently: **NOT YET REGISTERED** (needs manual setup or admin interface)

2. **Create Management Command** (Optional but Recommended)
   ```bash
   python manage.py setup_ncm_webhook
   ```
   - Automatically registers webhook with NCM
   - Runs webhook test
   - Creates setup log

3. **Add Webhook Configuration to Admin Interface** (Optional)
   - Allow admins to view webhook status
   - Trigger webhook registration from Django admin
   - View recent webhook events

---

## 🚀 How to Setup Webhooks

### **Option 1: Manual Setup via Django Shell**

```python
python manage.py shell

from services.ncm_service import NCMService

service = NCMService()

# Register webhook
webhook_url = "http://yourdomain.com/ncm/webhook/"
result = service.set_webhook_url(webhook_url)
print(result)

# Test webhook
test_result = service.test_webhook(webhook_url)
print(test_result)
```

### **Option 2: Create a Management Command** (Recommended)

Create file: `/dashboard/management/commands/setup_ncm_webhook.py`

```python
from django.core.management.base import BaseCommand
from django.conf import settings
from services.ncm_service import NCMService
import logging

logger = logging.getLogger('ncm')

class Command(BaseCommand):
    help = 'Setup NCM webhook registration'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--domain',
            type=str,
            help='Domain for webhook URL',
            default='http://127.0.0.1:8000'
        )
        parser.add_argument(
            '--test',
            action='store_true',
            help='Test webhook after registration',
        )
    
    def handle(self, *args, **options):
        domain = options['domain']
        webhook_url = f"{domain}/ncm/webhook/"
        
        service = NCMService()
        
        self.stdout.write(f"📝 Registering webhook: {webhook_url}")
        result = service.set_webhook_url(webhook_url)
        
        if result['success']:
            self.stdout.write(self.style.SUCCESS('✅ Webhook registered successfully!'))
            self.stdout.write(f"Response: {result['data']}")
            
            if options['test']:
                self.stdout.write("🧪 Testing webhook...")
                test_result = service.test_webhook(webhook_url)
                if test_result['success']:
                    self.stdout.write(self.style.SUCCESS('✅ Test passed!'))
                else:
                    self.stdout.write(self.style.ERROR(f'❌ Test failed: {test_result["error"]}'))
        else:
            self.stdout.write(self.style.ERROR(f'❌ Failed: {result["error"]}'))
```

Then run:
```bash
python manage.py setup_ncm_webhook --domain https://yourdomain.com --test
```

---

## 📋 URL Routing Summary

| URL | View | Purpose | CSRF Protection |
|-----|------|---------|-----------------|
| `/ncm/webhook/` | `ncm_webhook()` | Main webhook endpoint | Required |
| `/logistics/webhook/ncm/` | `ncm_webhook()` | Legacy endpoint | @csrf_exempt |

---

## 🔐 Security Measures

Currently Implemented:
- ✅ `@require_POST` decorator
- ✅ JSON validation
- ✅ Order existence validation
- ✅ Logging all events
- ✅ Error handling

Recommended Additions:
- ⚠️ Add HMAC signature verification
- ⚠️ Add IP whitelist for NCM servers
- ⚠️ Add rate limiting
- ⚠️ Add webhook secret key validation

---

## 📊 Status Mapping

When webhooks are received, NCM statuses are mapped as follows:

```python
'Pickup Order Created' → 'processing'
'Pickup Complete' → 'processing'
'Dispatched' → 'shipped'
'In Transit' → 'shipped'
'Arrived' → 'shipped'
'Out for Delivery' → 'shipped'
'Delivered' → 'delivered'
'Confirmed' → 'delivered'
```

---

## 📝 Required Environment

The system needs these settings configured in `.env`:

```
NCM_API_KEY=your_ncm_api_key
NCM_API_BASE_URL=https://api.ncm.example.com/v1
NCM_API_BASE_URL_V2=https://api.ncm.example.com/v2
```

---

## 📊 Webhook Event Flow

```
NCM API → (POST) → /ncm/webhook/ → Parse Payload
                    ↓
                    Check payload type
                    ↓
            ┌───────┴────────┬──────────┐
            ↓                ↓          ↓
        Test mode    Single order   Bulk orders
            ↓            ↓          ↓
        Return OK    Update DB   Loop & Update
            ↓            ↓          ↓
                    Create Log  Create Logs
                        ↓          ↓
                    Return JSON Response
```

---

## ✅ Checklist for Production

- [ ] Register webhook URL with NCM (`set_webhook_url()`)
- [ ] Test webhook endpoint (`test_webhook()`)
- [ ] Add IP whitelist for NCM servers
- [ ] Add HMAC signature verification
- [ ] Set up webhook event logging/monitoring
- [ ] Configure error alerts
- [ ] Test with sample payload
- [ ] Monitor webhook logs in production
- [ ] Set up retry logic for failed webhooks (Optional)
- [ ] Add webhook secret key validation (Optional)

---

## 📞 Support

For webhook issues:
1. Check `/logs/ncm_integration.log`
2. Enable verbose logging in settings
3. Test webhook manually via management command
4. Verify NCM API credentials
5. Check network connectivity to NCM API

---

**Report Status:** ✅ COMPLETE
**Webhook Configuration:** ✅ READY TO USE
**Next Step:** Register webhook URL with NCM
