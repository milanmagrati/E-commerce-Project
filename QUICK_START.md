# 🚀 NCM Webhook Quick Start Guide

## 5-Minute Setup

### 1. Update `.env` (30 seconds)

```bash
# Add this line to your .env file
NCM_WEBHOOK_SECRET=your-strong-secret-key-min-32-characters

# Optional: For SMS
SMS_PROVIDER=console
SMS_ENABLED=True
```

### 2. Generate Secret Key (1 minute)

```bash
python manage.py shell
from django.utils.crypto import get_random_secret_key
print(get_random_secret_key())
# Copy and paste into .env for NCM_WEBHOOK_SECRET
```

### 3. Check Webhook Endpoint (1 minute)

Visit: `http://localhost:8000/ncm/webhook/`

Should show: `Method Not Allowed` (because it only accepts POST from NCM)

### 4. Test with Real Order (2 minutes)

```bash
# First, create an order with ncm_order_id
python manage.py shell

from dashboard.models import Order
order = Order.objects.get(id=1)
order.ncm_order_id = 12345  # Replace with real NCM ID
order.save()
```

### 5. Register with NCM Support (1 minute)

Ask NCM to configure webhook for:
```
URL: https://yoursite.com/ncm/webhook/
Method: POST
Headers sent: X-NCM-Signature, Content-Type
```

---

## 🔄 How It Works

**When NCM sends update:**
1. ✅ Your webhook receives it
2. 🔐 Signature verified (security)
3. 🔍 Check if not duplicate
4. 📝 Update order status
5. 📱 Send SMS notification
6. 🔔 User sees alert (Alertify)

---

## 🧪 Test It Now

### Test with curl

```bash
#!/bin/bash
WEBHOOK_URL="http://localhost:8000/ncm/webhook/"
SECRET="your-secret-here"
PAYLOAD='{"webhook_id":"test-001","event":"order_status_update","order_id":12345,"status":"Delivered","test":true}'

SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -mac HMAC -macopt key="$SECRET" -hex | cut -d' ' -f2)

curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-NCM-Signature: $SIGNATURE" \
  -d "$PAYLOAD" \
  "$WEBHOOK_URL"
```

---

## 📊 Check Status

```bash
# View latest webhooks
python manage.py shell

from ncm.models import WebhookLog
WebhookLog.objects.all().order_by('-received_at')[:5]

# View failed webhooks
WebhookLog.objects.filter(status='failed')

# View order activity
from dashboard.models import Order, OrderActivityLog
order = Order.objects.get(id=1)
order.activity_logs.all().order_by('-created_at')
```

---

## 🐛 Troubleshooting

### No webhooks received?
- [ ] Check webhook URL is correct
- [ ] Verify firewall allows POST requests
- [ ] Check NCM configuration

### Wrong signature error?
- [ ] Use exact `NCM_WEBHOOK_SECRET` from .env
- [ ] Verify secret is at least 32 chars
- [ ] Check with NCM what signature method they use

### Orders not updating?
- [ ] Order must have `ncm_order_id` set
- [ ] Check `logs/ncm_integration.log`

---

## 💡 Features

| Feature | Status | How to Use |
|---------|--------|-----------|
| Auto-sync order detail | ✅ Works | Open order detail - auto-updates every 60 sec |
| Real-time alerts | ✅ Works | Alertify.js notifications in corners |
| SMS notifications | ⚙️ Optional | Set `SMS_ENABLED=True` in .env |
| Batch order sync | ✅ Works | Orders list auto-updates |
| Signature verification | ✅ Automatic | No action needed |
| Duplicate prevention | ✅ Automatic | No action needed |

---

## 📱 Enable SMS (Optional)

### For Development/Testing
```
SMS_PROVIDER=console
SMS_ENABLED=True
# Messages logged to console
```

### For Production (Sparrow in Nepal)
```
SMS_PROVIDER=sparrow
SMS_ENABLED=True
SMS_API_KEY=your_sparrow_api_key
SMS_SENDER_ID=YourShopName
```

### For Global Scale (Twilio)
```
SMS_PROVIDER=twilio
SMS_ENABLED=True
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=+1234567890
```

---

## 🔔 Keyboard Shortcuts

On order detail page:
- **Ctrl+Shift+R** - Toggle auto-sync on/off

---

## 📋 API Endpoints

```bash
# Get single order status
GET /ncm/api/order/123/status/

# Manually sync single order
POST /ncm/api/order/123/sync/

# Get multiple orders
GET /ncm/api/orders/batch-status/?order_ids=123,124,125

# Get activity log
GET /ncm/api/order/123/activity/?limit=10

# Find orders needing updates
GET /ncm/api/check-pending-updates/
```

---

## 📊 Status Mapping

```
NCM Status              → System Status
─────────────────────────────────────
Pickup Order Created    → processing
In Transit              → shipped
Out for Delivery        → shipped
Delivered               → delivered
Returned                → returned
```

---

## 🎯 Common Tasks

### Check if webhook working
```bash
tail logs/ncm_webhooks.log
# Should see: "✓ Webhook received and processed"
```

### View all order updates
```bash
from dashboard.models import OrderActivityLog
OrderActivityLog.objects.all().order_by('-created_at')[:10]
```

### Reset order for testing
```python
order = Order.objects.get(order_number='ORD-001')
order.ncm_status = None
order.status = 'processing'
order.save()
```

### Check SMS logs
```bash
tail logs/ncm_sms.log
```

---

## ⚡ Pro Tips

1. **Auto-sync interval**: Change in `settings.py` if needed
```python
ORDER_AUTO_SYNC_INTERVAL = 60  # seconds
```

2. **Monitor webhooks**: 
```bash
watch -n 5 'sqlite3 db.sqlite3 "SELECT count(*) FROM ncm_webhooklog;"'
```

3. **Test mode webhook**:
```json
{"test": true, "status": "Delivered", "webhook_id": "test-001"}
```
This won't update any orders, just tests the connection.

4. **Check logs in real-time**:
```bash
tail -f logs/ncm_*.log | grep -E "ERROR|✓|❌"
```

---

## 🚀 Go Live Checklist

- [ ] NCM_WEBHOOK_SECRET configured
- [ ] At least one order with ncm_order_id
- [ ] Webhook URL registered with NCM
- [ ] Tested with real NCM webhook
- [ ] SMS provider configured (if needed)
- [ ] Logs directory writable
- [ ] HTTPS enabled (if production)
- [ ] Database backups scheduled

---

## 📞 Need Help?

1. Check `NCM_WEBHOOK_SETUP.md` for detailed docs
2. View `IMPLEMENTATION_SUMMARY.md` for architecture
3. Check logs: `logs/ncm_*.log`
4. Run Django shell to debug
5. Contact NCM support for webhook configuration

---

## 🎉 You're All Set!

Your system will now:
- ✅ Automatically receive NCM updates
- ✅ Update orders in real-time  
- ✅ Notify customers via SMS
- ✅ Alert staff via browser
- ✅ Log everything for debugging

Enjoy automated order synchronization! 🚀
