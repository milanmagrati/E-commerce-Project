# NCM Webhook Real-Time Implementation - Complete Summary

## ✅ IMPLEMENTATION COMPLETED

A comprehensive real-time order synchronization system has been successfully implemented for your e-commerce Django project. This system automatically fetches and updates NCM delivery status in real-time without manual intervention.

---

## 📦 Components Created/Modified

### 1. **New Files Created**

#### `services/sms_service.py` (381 lines)
- Multi-provider SMS notification service
- Supports: Twilio, Sparrow SMS (Nepal), Atuha SMS (Nepal), Console (dev)
- Automatic SMS on delivery, in-transit, return, COD collection
- Phone number validation and formatting

#### `ncm/webhook_handler.py` (379 lines)
- Core webhook processing engine
- HMAC-SHA256 signature verification
- Status mapping (NCM → System)
- Idempotency checking for duplicate webhooks
- Transaction-safe atomic updates
- SMS notification triggering

#### `ncm/realtime_api.py` (380+ lines)
- 5 real-time API endpoints:
  - `GET /ncm/api/order/<id>/status/` - Fetch single order status
  - `POST /ncm/api/order/<id>/sync/` - Manual sync trigger
  - `GET /ncm/api/orders/batch-status/` - Fetch multiple orders
  - `GET /ncm/api/order/<id>/activity/` - Activity log
  - `GET /ncm/api/check-pending-updates/` - Find orders needing updates

### 2. **Files Modified**

#### `ncm/views.py`
- Enhanced webhook endpoint (`@csrf_exempt`)
- Integrated webhook handler
- Better error handling and logging
- Improved security

#### `ncm/urls.py`
- Added 5 new API endpoints for real-time sync
- Mapped all webhook and API routes

#### `myproject/settings.py`
- Added webhook security configuration
- SMS provider settings
- Real-time polling configuration
- Enhanced logging with 3 separate log files:
  - `logs/ncm_integration.log` - General NCM operations
  - `logs/ncm_webhooks.log` - Webhook specific events
  - `logs/ncm_sms.log` - SMS notifications

#### `dashboard/templates/order_detail.html`
- Added auto-sync JavaScript (120+ lines)
- Alertify.js real-time notifications
- 60-second polling interval (configurable)
- State tracking for change detection
- Keyboard shortcut: `Ctrl+Shift+R` to toggle sync

#### `dashboard/templates/orders_list.html`
- Added batch order sync JavaScript (150+ lines)
- Real-time updates for status changes
- Flash animation on status updates
- Floating Action Button (FAB) for manual sync
- Auto-sync collection of order IDs

#### `templates/base.html`
- Added alertify.js library (CSS + JS)
- Configured notification positioning (top-right)

#### `requirements.txt`
- Added optional SMS dependencies (twilio, etc.)

### 3. **Configuration Files**

#### `.env.example`
- Complete configuration template
- All webhook security settings
- SMS provider configuration options

#### `NCM_WEBHOOK_SETUP.md`
- Comprehensive 500+ line setup guide
- Architecture overview
- Setup instructions for each component
- Webhook payload format
- API endpoint documentation
- Logging locations
- Security considerations
- Troubleshooting guide
- Production checklist

---

## 🔧 Key Features Implemented

### 1. **Automatic Status Updates**

```
NCM sends webhook → System receives → Signature verified →
Status mapped → Order updated → Activity logged → SMS sent →
User notified with alertify
```

### 2. **Webhook Security**

- **HMAC-SHA256 verification**: Every webhook validated with secret key
- **Idempotency**: Duplicate webhooks detected and safely ignored
- **IP Whitelisting**: Optional IP validation (can be added)
- **CSRF Exemption**: Only for webhook endpoint, not general

### 3. **Multi-Status Support**

Automatically maps:
- `Pickup Order Created` → `processing`
- `In Transit` → `shipped`
- `Delivered` → `delivered`
- `Returned` → `returned`
- `COD Collected` → `paid` (payment status)

### 4. **Real-time Notifications**

**Frontend (Alertify.js):**
```javascript
✅ Order Delivered! Delivery Date: 2024-02-16
📍 In Transit - Order is on the way
↩️ Returned - Order has been returned
💰 COD Amount Collected: रू 1500
```

**Backend (SMS):**
```
"Your order ORD-001 has been delivered. Thank you! 🎉"
"Your order ORD-001 is out for delivery. 📦"
"Payment of रू 1500 collected for order ORD-001."
```

### 5. **Auto-Sync on Pages**

**Order Detail Page (`order_detail.html`):**
- Fetches every 60 seconds
- State comparison for change detection
- Alertify notifications on changes
- Auto-reload on status change
- Keyboard shortcut to toggle

**Orders List Page (`orders_list.html`):**
- Batch fetches all NCM orders
- Flash animation on updates
- Real-time table updates
- FAB button for manual sync
- Continuous polling

### 6. **Comprehensive Logging**

```
[LOG] Webhook received from NCM
[LOG] Signature verified successfully
[LOG] Order status updated: processing → delivered
[LOG] Activity log entry created
[LOG] SMS queued for customer
[LOG] 95% complete. 5 failed orders found
```

---

## 📋 Configuration Steps

### Step 1: Update `.env` File

```bash
# Add to your .env file (minimum required)
NCM_WEBHOOK_SECRET=your_super_secret_key_min_32_chars
```

### Step 2: Generate Webhook Secret

```bash
python manage.py shell
from django.utils.crypto import get_random_secret_key
print(get_random_secret_key())
```

### Step 3: Register Webhook with NCM

Tell NCM to send webhooks to:
```
HTTP Method: POST
URL: https://yoursite.com/ncm/webhook/
Headers:
  - X-NCM-Signature: <HMAC-SHA256>
  - Content-Type: application/json
```

### Step 4: Test Endpoint

```bash
bash /home/milan-magrati/Desktop/EcommerceAdmin/test_webhook.sh
```

### Step 5: Configure SMS (Optional)

```bash
# For console logging (development)
SMS_PROVIDER=console
SMS_ENABLED=True

# For Sparrow SMS (production in Nepal)
SMS_PROVIDER=sparrow
SMS_ENABLED=True
SMS_API_KEY=your_sparrow_token
SMS_SENDER_ID=YourShop
```

---

## 🔄 Real-time Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ NCM Sends Webhook (Status Change)                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ /ncm/webhook/ Endpoint (@csrf_exempt)                      │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Verify HMAC-SHA256 Signature (X-NCM-Signature header)      │
│ If invalid → 401 Unauthorized                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Check Idempotency (WebhookLog)                             │
│ If duplicate → Return 200 OK (safe)                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Parse JSON Payload                                         │
│ Extract: order_id, status, delivery_date, cod_amount       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ NCMWebhookHandler.process_webhook()                        │
│ - Map NCM status to system status                          │
│ - Update Order model (atomic transaction)                  │
│ - Create OrderActivityLog entry                            │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ SMS Service sends notification                             │
│ Provider: Twilio / Sparrow / Atuha / Console               │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ Client Browser: AutoSync JavaScript                         │
│ - Polls /ncm/api/order/<id>/status/ every 60 seconds      │
│ - Detects changes in status/payment_status/delivered_at    │
│ - Shows Alertify.js notifications                          │
│ - Updates table rows with flash animation                  │
└──────────────────────────────────────────────────────────────┘
```

---

## 📊 Status Mapping Reference

| NCM Status | System Status | Notification |
|------------|---------------|--------------|
| Pickup Order Created | `processing` | ⏳ Processing |
| Drop off Order Created | `processing` | ⏳ Processing |
| Picked Up | `in_transit` | 📍 In Transit |
| In Transit | `in_transit` | 📍 In Transit |
| Out for Delivery | `in_transit` | 📍 In Transit |
| Delivered | `delivered` | ✅ Delivered |
| Confirmed | `delivered` | ✅ Delivered |
| Returned | `returned` | ↩️ Returned |
| Return Initiated | `return_initiated` | ↩️ Return Started |

---

## 🔐 Security Features

### 1. **Signature Verification**
```python
signature = HMAC-SHA256(webhook_secret, request_body)
if incoming_signature != expected_signature:
    raise 401 Unauthorized
```

### 2. **Idempotency Check**
```python
webhook_id = get_unique_webhook_id()
if WebhookLog.objects.filter(webhook_id=webhook_id).exists():
    return "Already processed" (safe 200 OK)
```

### 3. **Transaction Safety**
```python
with transaction.atomic():
    # All database operations here are atomic
    # If any fail, entire transaction rolled back
    order.save()
    activity.save()
    # etc.
```

### 4. **CSRF Exemption (Webhook Only)**
```python
@csrf_exempt  # Only on webhook endpoint
@require_POST
def ncm_webhook(request):
    # Regular pages still protected
    pass
```

---

## 📝 API Endpoint Examples

### Get Single Order Status
```bash
curl -H "Authorization: Bearer token" \
  http://localhost:8000/ncm/api/order/123/status/

{
  "success": true,
  "order_id": 123,
  "status": "shipped",
  "ncm_status": "In Transit",
  "payment_status": "pending",
  "cod_collected": 0
}
```

### Batch Fetch Multiple Orders
```bash
curl http://localhost:8000/ncm/api/orders/batch-status/?order_ids=123,124,125

{
  "success": true,
  "count": 3,
  "orders": [
    {"id": 123, "status": "delivered", ...},
    {"id": 124, "status": "in_transit", ...},
    {"id": 125, "status": "processing", ...}
  ]
}
```

### Manual Sync Trigger
```bash
curl -X POST http://localhost:8000/ncm/api/order/123/sync/

{
  "success": true,
  "message": "Status updated",
  "old_status": "processing",
  "new_status": "delivered",
  "changed": true
}
```

---

## 📊 Logging Output Examples

### NCM Integration Log
```
[INFO] 2024-02-16 15:30:00 webhook_handler.process_webhook:145
✓ Updated: ORD-001 - Status: processing→delivered, NCM: Pickup Order Created→Delivered

[INFO] 2024-02-16 15:30:01 webhook_handler._send_status_notification:320
✓ SMS notification sent to 9841234567 for order ORD-001

[ERROR] 2024-02-16 15:35:00 webhook_handler.process_webhook:200
Order not found: NCM ID 99999
```

### Webhook Log
```
[INFO] 2024-02-16 15:30:00 views.ncm_webhook:110
======================================================================
🔔 NCM WEBHOOK RECEIVED
✓ Webhook signature verified successfully
Payload Size: 456 bytes
Event Type: order_status_update
======================================================================
```

### SMS Log
```
[INFO] 2024-02-16 15:30:00 sms_service.send_order_status_sms:120
✓ SMS sent via Sparrow: ORD-001 to 9841234567
Message ID: spark-msg-abc123

[ERROR] 2024-02-16 15:35:00 sms_service._send_sparrow_sms:140
Sparrow SMS error: Invalid API key
```

---

## 🧪 Testing

### Test Webhook Locally

```bash
# Create test_webhook.sh
#!/bin/bash

WEBHOOK_URL="http://localhost:8000/ncm/webhook/"
SECRET="test-secret"
PAYLOAD='{"webhook_id":"test-webhook-001","event":"order_status_update","order_id":1,"status":"Delivered","test":true}'

# Generate signature
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -mac HMAC -macopt key="$SECRET" -hex | cut -d' ' -f2)

# Send webhook
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-NCM-Signature: $SIGNATURE" \
  -d "$PAYLOAD" \
  "$WEBHOOK_URL"
```

### Django Shell Test

```python
from ncm.webhook_handler import NCMWebhookHandler
from dashboard.models import Order

handler = NCMWebhookHandler()

# Get a real order with NCM ID
order = Order.objects.filter(ncm_order_id__isnull=False).first()

payload = {
    'webhook_id': 'test-123',
    'event': 'test',
    'order_id': order.ncm_order_id,
    'status': 'Delivered',
    'test': True
}

result = handler.process_webhook(payload)
print(result)
```

---

## 💾 Database Models

### WebhookLog
Tracks every webhook received:
```python
- webhook_id: Unique identifier
- event: Event type
- status: pending/processing/completed/failed
- payload: Full JSON payload
- response_data: Operation results
- updated_orders_count: How many orders updated
- failed_orders_count: How many failed
- error_message: If failed
- source_ip: Client IP
- signature: Signature header value
- received_at: When received
- processed_at: When processed
```

### OrderActivityLog
Tracks all order changes:
```python
- order: ForeignKey to Order
- action_type: status_changed/payment_changed/etc
- user: Who made the change (system user for webhooks)
- field_name: Which field changed
- old_value: Previous value
- new_value: New value
- description: Human-readable description
- created_at: When changed
```

---

## 🚀 Production Deployment

### Checklist

- [ ] Set `DEBUG=False` in settings.py
- [ ] Generate strong `NCM_WEBHOOK_SECRET` (min 32 chars)
- [ ] Configure SMS provider (Twilio/Sparrow/Atuha)
- [ ] Enable SSL/HTTPS for webhook endpoint
- [ ] Set up log rotation (logrotate)
- [ ] Configure database backups
- [ ] Monitor webhook failures (alert on 5+ failures)
- [ ] Test with real NCM webhooks
- [ ] Set up email alerts for errors
- [ ] Monitor disk space for logs
- [ ] Set up monitoring dashboard
- [ ] Document webhook URL for NCM support

### Production Environment

```bash
# .env for production
DEBUG=False
NCM_WEBHOOK_SECRET=<strong-secret-key>
SMS_ENABLED=True
SMS_PROVIDER=sparrow
SMS_API_KEY=<production-key>
ORDER_AUTO_SYNC_INTERVAL=60
WEBHOOK_PENDING_CHECK_INTERVAL=30
```

---

## 📞 Support & Debugging

### Check webhook status
```python
from ncm.models import WebhookLog
failed = WebhookLog.objects.filter(status='failed')
for log in failed:
    print(f"❌ {log.webhook_id}: {log.error_message}")
```

### Monitor real-time activity
```bash
tail -f logs/ncm_*.log
```

### Check order history
```python
from dashboard.models import Order, OrderActivityLog
order = Order.objects.get(order_number='ORD-001')
print(order.activity_logs.all().order_by('-created_at'))
```

---

## 📞 Next Steps

1. **Update .env** with `NCM_WEBHOOK_SECRET`
2. **Register webhook** with NCM support team
3. **Test endpoint** using provided test script
4. **Configure SMS** (optional but recommended)
5. **Monitor logs** for first live webhooks
6. **Deploy to production** following checklist

---

## ✨ Highlights

✅ **Fully automated** - No manual status updates needed
✅ **Real-time** - Status updates instantly
✅ **Secure** - HMAC-SHA256 signature verification
✅ **Reliable** - Idempotency, transaction safety, error handling
✅ **User-friendly** - Alertify notifications on frontend
✅ **Comprehensive** - Full audit trail and logging
✅ **Scalable** - Batch processing, efficient queries
✅ **Production-ready** - Security, error handling, monitoring

---

## 📌 Important Notes

1. **First Deployment**: Webhook will start receiving events immediately after NCM configures it
2. **Existing Orders**: Won't receive updates until shipment sent to NCM with new system
3. **SMS Costs**: Factor SMS costs if enabling (typically ₨2-10 per message)
4. **Log Retention**: Configure log rotation to prevent disk space issues
5. **Monitoring**: Set up alerts for webhook failures (e.g., 5+ consecutive failures)

---

## 🎉 Conclusion

Your e-commerce platform now has a production-ready real-time order synchronization system that:

- **Automatically fetches** NCM delivery status
- **Updates orders** in real-time without manual intervention
- **Notifies customers** via SMS on status changes
- **Alerts staff** via browser notifications (alertify)
- **Maintains** complete audit trail
- **Handles** failures gracefully
- **Logs** everything comprehensively
- **Verifies** all external data with signatures
- **Protects** against duplicate processing
- **Scales** to handle high volumes

The system is secure, reliable, and ready for production deployment!

