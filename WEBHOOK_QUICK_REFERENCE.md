# 🔗 NCM Webhook Quick Reference

## ✅ WEBHOOK IS REGISTERED & WORKING

---

## ⚡ Quick Commands

### Register/Update Webhook
```bash
python manage.py setup_ncm_webhook --domain https://yourdomain.com --test
```

### Monitor Live Logs
```bash
tail -f logs/ncm_integration.log
```

### View Specific Webhooks
```bash
tail -f logs/ncm_integration.log | grep -i webhook
```

### Django Admin
```
Dashboard → Orders → Order Activity Log
Filter: action_type = 'status_changed'
```

---

## 📍 Webhook Endpoints

| Endpoint | Full URL | Status |
|----------|----------|--------|
| **Primary** | `http://domain/ncm/webhook/` | ✅ Active |
| **Backup** | `http://domain/logistics/webhook/ncm/` | ✅ Available |

---

## 🔄 Webhook Flow

```
NCM API → POST to /ncm/webhook/ → Django Processes
    ↓
Parse Status Update → Look up Order
    ↓
Map Status → Update Database
    ↓
Create Log Entry → Return JSON Response
    ↓
Log Event → Done ✅
```

---

## 📊 Status Auto-Mapping

When NCM sends webhook:
- `"Delivered"` → Order status becomes `delivered` ✅
- `"In Transit"` → Order status becomes `shipped` ✅
- `"Dispatched"` → Order status becomes `shipped` ✅

---

## 🧪 Test Webhook

```bash
# Quick test
curl -X POST http://127.0.0.1:8000/ncm/webhook/ \
  -H "Content-Type: application/json" \
  -d '{
    "event": "order_status_changed",
    "status": "Delivered",
    "order_id": 12345,
    "test": true
  }'
```

---

## 📝 What to Monitor

1. **Logs** → `/logs/ncm_integration.log`
   - Watch for: `=== NCM Webhook ===`
   - Success: `✓ Updated: ORD000074 → Delivered`

2. **Admin** → Django Admin OrderActivityLog
   - Check for new entries
   - Verify status changes

3. **Database** → Order status field
   - Should update automatically
   - Check order detail page

---

## 🚀 Production Deployment

**Before going live:**
```bash
# 1. Register with production domain
python manage.py setup_ncm_webhook --domain https://yourdomain.com --test

# 2. Verify SSL/HTTPS works
curl -X POST https://yourdomain.com/ncm/webhook/ \
  -H "Content-Type: application/json" \
  -d '{"test": true}'

# 3. Monitor logs
tail -f logs/ncm_integration.log
```

---

## ✅ Registered Domains

- ✅ `http://127.0.0.1:8000/ncm/webhook/` (Development)
- 📋 Add your production domain here after deployment

---

## 📞 Troubleshooting

| Problem | Solution |
|---------|----------|
| "Connection refused" on test | Start Django server |
| 404 on webhook | Check URL patterns in ncm/urls.py |
| No logs appearing | Check `/logs/` directory exists |
| Status not updating | Verify order NCM ID exists |

---

## 💡 Tips

- ✅ Webhooks work with or without server running initially (NCM retries)
- ✅ All events logged to `ncm_integration.log` for debugging
- ✅ Use management command to update domain anytime
- ✅ No additional coding needed - fully automated

---

**Last Updated:** Feb 10, 2026  
**Status:** 🟢 ACTIVE & REGISTERED
