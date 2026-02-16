# NCM Webhook Real-Time Integration System

## Overview

This is a comprehensive real-time order synchronization system that automatically fetches and updates NCM (Nepal Can Move) delivery status without manual intervention. The system includes:

- **Automated Webhook Endpoint**: Receives status updates from NCM in real-time
- **Signature Verification**: Secure HMAC-SHA256 authentication for webhooks
- **Idempotency Protection**: Prevents duplicate processing of the same webhook
- **SMS Notifications**: Automatic SMS to customers on status changes
- **Real-time UI Updates**: Auto-refresh order details with alertify.js notifications
- **Activity Logging**: Complete audit trail of all status changes
- **Production-Ready**: Transaction safety, comprehensive error handling, and logging

---

## System Architecture

### 1. **Webhook Handler** (`ncm/webhook_handler.py`)

The `NCMWebhookHandler` class processes incoming NCM webhooks with:

- **Signature Verification**: HMAC-SHA256 validation using `X-NCM-Signature` header
- **Status Mapping**: Converts NCM status to system status (e.g., "In Transit" → "shipped")
- **Order Update**: Atomically updates order fields with transaction safety
- **SMS Service**: Sends SMS notifications to customers
- **Activity Logging**: Records all changes in `OrderActivityLog`

### 2. **Real-time API Endpoints** (`ncm/realtime_api.py`)

JavaScript-accessible endpoints for auto-sync:

```
GET  /ncm/api/order/<order_id>/status/          - Fetch current order status
POST /ncm/api/order/<order_id>/sync/            - Manual sync from NCM
GET  /ncm/api/orders/batch-status/              - Fetch multiple orders
GET  /ncm/api/order/<order_id>/activity/        - Activity log
GET  /ncm/api/check-pending-updates/            - Find orders needing updates
```

### 3. **SMS Service** (`services/sms_service.py`)

Sends SMS notifications via multiple providers:

- **Twilio**: For global deployment
- **Sparrow**: Popular in Nepal
- **Atuha**: Alternative Nepal provider
- **Console**: Development/testing mode

### 4. **Frontend Auto-sync** (`order_detail.html`)

JavaScript integration with:

- **Alertify.js**: Beautiful real-time notifications
- **Polling**: Periodic status checks (default: 60 seconds)
- **State Tracking**: Detects changes and notifies user
- **Toggle**: Ctrl+Shift+R to enable/disable auto-sync

---

## Setup Instructions

### 1. **Configure Environment Variables**

Add to your `.env` file:

```bash
# Webhook Security
NCM_WEBHOOK_SECRET=your-super-secret-key-here-min-32-chars

# SMS Configuration (optional)
SMS_PROVIDER=console          # Options: console, twilio, sparrow, atuha
SMS_ENABLED=False             # Set to True when ready
SMS_API_KEY=your_api_key_here
SMS_SENDER_ID=EcommerceAdmin

# Real-time Settings
ORDER_AUTO_SYNC_INTERVAL=60   # Seconds between auto-sync checks
WEBHOOK_PENDING_CHECK_INTERVAL=30  # Minutes before checking for NCM updates
```

### 2. **Database Migrations**

Ensure webhook models are created:

```bash
python manage.py migrate ncm
```

The `WebhookLog` model tracks all incoming webhooks.

### 3. **Configure Webhook Signature**

**Generate a Strong Secret Key:**

```python
# In Django shell
from django.utils.crypto import get_random_secret_key
print(get_random_secret_key())
```

**Set in .env:**

```
NCM_WEBHOOK_SECRET=your_generated_secret_key
```

### 4. **Register Webhook URL with NCM**

Send this to NCM to configure your webhook endpoint:

```
Webhook URL: https://yoursite.com/ncm/webhook/
Method: POST
Headers: 
  - X-NCM-Signature: <HMAC-SHA256 signature>
  - Content-Type: application/json
```

### 5. **Test Webhook Endpoint**

Use curl to test (use your actual webhook secret):

```bash
#!/bin/bash

WEBHOOK_URL="http://localhost:8000/ncm/webhook/"
SECRET="your-webhook-secret"
PAYLOAD='{"webhook_id":"test-123","event":"order_status_update","order_id":12345,"status":"Delivered","test":true}'

# Generate signature
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -mac HMAC -macopt key="$SECRET" -hex | cut -d' ' -f2)

# Send webhook
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-NCM-Signature: $SIGNATURE" \
  -d "$PAYLOAD" \
  "$WEBHOOK_URL"
```

---

## Webhook Payload Format

```json
{
  "webhook_id": "unique_identifier_12345",
  "event": "order_status_update",
  "order_id": 12345,
  "or_order_ids": [12345, 12346],
  "status": "Delivered",
  "delivery_date": "2024-02-16T15:30:00Z",
  "cod_amount": 1500.00,
  "timestamp": "2024-02-16T15:30:00Z",
  "test": false
}
```

**Supported Status Values:**
- `Pickup Order Created` → `processing`
- `Drop off Order Created` → `processing`
- `Picked Up` → `in_transit`
- `In Transit` → `in_transit`
- `Out for Delivery` → `in_transit`
- `Delivered` → `delivered`
- `Returned` → `returned`
- `Return Initiated` → `return_initiated`

---

## Real-time Features

### 1. **Auto-sync in Order Detail Page**

```html
<!-- In order_detail.html -->
<!-- Automatically fetches status every 60 seconds -->
<!-- Shows alertify notifications on changes -->
<!-- Reloads page when status changes -->
```

**Keyboard Shortcuts:**
- `Ctrl+Shift+R` - Toggle auto-sync on/off

### 2. **SMS Notifications**

When `SMS_ENABLED=True`, customers receive SMS for:

```
✅ Delivered: "Your order {ORDER#} has been delivered. Thank you!"
📍 In Transit: "Your order {ORDER#} is out for delivery."
↩️ Returned: "Your order {ORDER#} has been marked for return."
💰 COD Collected: "Payment of रू {AMOUNT} collected for order {ORDER#}."
```

### 3. **Activity Logging**

All webhook updates create audit entries:

```
OrderActivityLog:
- order: Order object
- action_type: 'status_changed'
- user: system user (ncm_webhook_system)
- field_name: 'ncm_status'
- old_value: previous status
- new_value: new status
- description: "NCM Webhook: Delivered"
- created_at: timestamp
```

---

## API Endpoints

### Get Order Status

```bash
GET /ncm/api/order/123/status/

Response:
{
  "success": true,
  "order_id": 123,
  "order_number": "ORD-001",
  "status": "shipped",
  "ncm_status": "In Transit",
  "payment_status": "paid",
  "delivered_at": "2024-02-16T15:30:00Z",
  "cod_collected": 1500.00,
  "source": "hybrid",
  "timestamp": "2024-02-16T15:35:00Z"
}
```

### Manual Status Sync

```bash
POST /ncm/api/order/123/sync/

Response:
{
  "success": true,
  "message": "Status updated",
  "old_status": "processing",
  "new_status": "delivered",
  "ncm_status": "Delivered",
  "changed": true
}
```

### Batch Status Fetch

```bash
GET /ncm/api/orders/batch-status/?order_ids=123,124,125

Response:
{
  "success": true,
  "count": 3,
  "orders": [
    {"id": 123, "status": "delivered", "ncm_status": "Delivered", ...},
    {"id": 124, "status": "in_transit", "ncm_status": "In Transit", ...},
    {"id": 125, "status": "processing", "ncm_status": "Pickup Order Created", ...}
  ]
}
```

### Activity Log

```bash
GET /ncm/api/order/123/activity/?limit=10

Response:
{
  "success": true,
  "activities": [
    {
      "id": 456,
      "action_type": "status_changed",
      "field_name": "ncm_status",
      "old_value": "In Transit",
      "new_value": "Delivered",
      "user": "NCM Webhook System",
      "created_at": "2024-02-16T15:30:00Z"
    }
  ]
}
```

---

## Logging

Logs are stored in three separate files for debugging:

### 1. **NCM Integration Log** (`logs/ncm_integration.log`)
```
[DEBUG] 2024-02-16 15:30:00 webhook_handler.process_webhook:145 ✓ Updated: ORD-001 - Status: processing→delivered
[ERROR] 2024-02-16 15:35:00 webhook_handler.process_webhook:200 Order not found: NCM ID 99999
```

### 2. **Webhook Log** (`logs/ncm_webhooks.log`)
```
[INFO] 2024-02-16 15:30:00 views.ncm_webhook:110 ✓ Webhook signature verified
[WARNING] 2024-02-16 15:35:00 views.ncm_webhook:220 ⚠️ Duplicate webhook detected: webhook-123
```

### 3. **SMS Log** (`logs/ncm_sms.log`)
```
[INFO] 2024-02-16 15:30:00 sms_service.send_order_status_sms:85 ✓ SMS sent to 9841234567 for ORD-001
[ERROR] 2024-02-16 15:35:00 sms_service._send_sparrow_sms:140 Invalid API key
```

---

## Security Considerations

### 1. **Webhook Signature Verification**

All incoming webhooks are validated using HMAC-SHA256:

```python
# Header: X-NCM-Signature
signature = HMAC-SHA256(webhook_secret, request_body)
```

If signature doesn't match, webhook is rejected with **401 Unauthorized**.

### 2. **IP Whitelisting** (Optional)

Add to `settings.py`:

```python
NCM_WEBHOOK_ALLOWED_IPS = [
    '203.86.xxx.xxx',  # NCM's IP
    '203.86.xxx.xxx',
]
```

### 3. **CSRF Exemption**

The webhook endpoint uses `@csrf_exempt` because it's called from external NCM servers (not from your own site).

### 4. **Idempotency**

Duplicate webhooks are detected and safely ignored:

```
webhook_id is unique and checked against WebhookLog
If duplicate found → return 200 OK (safe)
```

---

## Troubleshooting

### Webhook not being triggered

**Check:**
1. Webhook URL is correct: `https://yoursite.com/ncm/webhook/`
2. NCM webhook is configured in NCM dashboard
3. Server firewall allows inbound POST requests
4. Django CSRF settings (should be fine with @csrf_exempt)

**Test:**
```bash
python manage.py shell
from ncm.models import WebhookLog
WebhookLog.objects.all().order_by('-received_at')[:5]
```

### Orders not updating

**Check:**
1. Order has `ncm_order_id` set
2. Order status is in updatable list (not 'delivered', 'cancelled')
3. Check logs: `tail -f logs/ncm_integration.log`
4. Verify signature: `tail -f logs/ncm_webhooks.log`

### SMS not sending

**If using Sparrow:**
- Check API key in .env: `SMS_API_KEY`
- Verify phone number format: Should be 10 digits
- Check logs: `tail -f logs/ncm_sms.log`

**If using development mode:**
- Set `SMS_PROVIDER=console` and `SMS_ENABLED=True`
- Check console output for SMS logs

---

## Production Deployment Checklist

- [ ] Set `DEBUG=False` in Django settings
- [ ] Configure `ALLOWED_HOSTS` properly
- [ ] Generate strong `NCM_WEBHOOK_SECRET`
- [ ] Set `NCM_WEBHOOK_SECRET` in .env (not in code)
- [ ] Configure proper logging (syslog, ELK, etc.)
- [ ] Set up monitoring for webhook failures
- [ ] Configure SMS provider (not 'console')
- [ ] Test webhook signature verification
- [ ] Set up database backups
- [ ] Monitor log file sizes (set rotation limits)
- [ ] Configure SSL/TLS for webhook endpoint
- [ ] Test with real NCM webhooks
- [ ] Set up alert notifications for errors

---

## Examples

### Example 1: Manual Webhook Test

```python
# In Django shell
from ncm.webhook_handler import NCMWebhookHandler
from unittest.mock import Mock

handler = NCMWebhookHandler()

# Simulate webhook payload
payload = {
    'webhook_id': 'test-webhook-001',
    'event': 'order_status_update',
    'order_id': 123,
    'status': 'Delivered',
    'delivery_date': '2024-02-16',
    'cod_amount': 1500.00,
    'timestamp': '2024-02-16T10:30:00Z'
}

# Process
result = handler.process_webhook(payload)
print(result)
```

### Example 2: Check Webhook Status

```python
from ncm.models import WebhookLog
from django.utils import timezone
from datetime import timedelta

# Show latest webhooks
recent = WebhookLog.objects.all().order_by('-received_at')[:10]
for log in recent:
    print(f"{log.webhook_id} - {log.status} - {log.updated_orders_count} updated")

# Show failed webhooks
failed = WebhookLog.objects.filter(status='failed')
for log in failed:
    print(f"❌ {log.webhook_id}: {log.error_message}")

# Show webhooks from last hour
one_hour_ago = timezone.now() - timedelta(hours=1)
recent_hour = WebhookLog.objects.filter(received_at__gte=one_hour_ago)
print(f"Total webhooks in last hour: {recent_hour.count()}")
```

### Example 3: Configure SMS for Production

```python
# .env configuration for Sparrow SMS (Nepal)
SMS_PROVIDER=sparrow
SMS_ENABLED=True
SMS_API_KEY=your_sparrow_api_token
SMS_SENDER_ID=YourShop
```

---

## Support & Monitoring

### Monitor webhook failures

```bash
# Watch for errors
tail -f logs/ncm_*.log | grep ERROR

# Count webhook statuses
sqlite3 db.sqlite3 "SELECT status, COUNT(*) FROM ncm_webhooklog GROUP BY status;"

# Find pending updates
python manage.py shell
from ncm.realtime_api import api_check_pending_ncm_updates
from django.test import RequestFactory
from django.contrib.auth.models import AnonymousUser

req = RequestFactory().get('/api/check-pending/')
req.user = AnonymousUser()  # Or a real user
# ... This would show orders needing updates
```

---

## API Documentation

See `/ncm/realtime_api.py` for complete endpoint documentation with all query parameters and response formats.

