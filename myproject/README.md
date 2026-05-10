# 🛍️ EcommerceAdmin - Complete System Documentation

![Django](https://img.shields.io/badge/Django-6.0.1-092E20?style=for-the-badge&logo=django)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python)
![MySQL](https://img.shields.io/badge/MySQL-Database-4479A1?style=for-the-badge&logo=mysql)
![Real-Time](https://img.shields.io/badge/Real--Time-Webhooks-FF4500?style=for-the-badge&logo=socket.io)
![Logistics](https://img.shields.io/badge/Logistics-NCM%20%7C%20PND-0052CC?style=for-the-badge)
![Status](https://img.shields.io/badge/Status-Production%20Ready-green)

---

## 📖 Table of Contents

1. [System Overview](#-system-overview)
2. [Quick Start & Setup](#-quick-start--setup)
3. [System Architecture](#-system-architecture)
4. [Key Apps & Modules](#-key-apps--modules)
5. [Real-Time Webhooks & Logistics](#-real-time-webhooks--logistics)
    - [Status Updates Flow](#status-updates-flow)
    - [API Endpoints](#api-endpoints)
    - [Webhook Security](#webhook-security)
6. [Inventory & Backorders](#-inventory--backorders)
7. [HRM & Staff Performance](#-hrm--staff-performance)
8. [Multi-Provider SMS System](#-multi-provider-sms-system)
9. [Frontend Real-Time Notifications](#-frontend-real-time-notifications)
10. [Testing & Verification](#-testing--verification)
11. [Troubleshooting Guide](#-troubleshooting-guide)
12. [Logs & Audit Trail](#-logs--audit-trail)
13. [Production Checklist](#-production-checklist)

---

## 📖 System Overview

**EcommerceAdmin** is an enterprise-grade, comprehensive Django-based e-commerce admin system tailored for scalable operations. It handles real-time order management, inventory control, logistics syncing, HR management, internal messaging, and comprehensive staff performance tracking.

Built for modern e-commerce, it provides seamless, automated integrations with Nepali logistics providers (NCM, Pick and Drop), multiple SMS gateways, and features a granular role-based access control system.

### Core Features:
- **Zero-Touch Logistics Sync**: Automated order status mapping and updates via Webhooks.
- **Advanced Inventory**: Atomic inventory allocation, restocking, and robust backorder handling to prevent overselling.
- **Granular RBAC**: Role-based access control with custom user permissions.
- **HR & Leave Management**: Modern UI for leave policies, dynamic validation, and employee tracking.
- **Multi-Provider Notifications**: Integrated SMS dispatching via Twilio, Sparrow SMS, and Atuha.
- **Staff Performance Tracking**: KPI metrics, sales targets, and warehouse operations tracking.
- **Return & Dispatch Operations**: Sophisticated lifecycle management for Returns, RTOs (Return to Origin), and batch dispatches.

---

## 🚀 Quick Start & Setup

### 1. Prerequisites
- Python 3.12+
- MySQL Server
- Git

### 2. Installation Steps
```bash
git clone <repo-url>
cd EcommerceAdmin
python3 -m venv .venv
source .venv/bin/activate
pip install -r myproject/requirements.txt
```

### 3. Environment Variables (`.env`)
Create a `.env` file in the `myproject` root directory:
```ini
SECRET_KEY='your-strong-secret-key'
DEBUG=True
DB_NAME='your_db_name'
DB_USER='your_db_user'
DB_PASSWORD='your_db_password'

# Webhook Security
NCM_WEBHOOK_SECRET='your-super-secret-key-here-min-32-chars'

# SMS Configuration (optional)
SMS_PROVIDER=console          # Options: console, twilio, sparrow, atuha
SMS_ENABLED=True             
SMS_API_KEY=your_api_key_here
SMS_SENDER_ID=EcommerceAdmin

# Real-time Settings
ORDER_AUTO_SYNC_INTERVAL=60   # Seconds between auto-sync checks
WEBHOOK_PENDING_CHECK_INTERVAL=30  # Minutes before checking for updates
```

### 4. Database & Server Run
```bash
cd myproject
python3 manage.py makemigrations
python3 manage.py migrate
python3 manage.py createsuperuser
python3 manage.py runserver
```

---

## 🏗️ System Architecture

- **Backend**: Django 6.0.1 (Python 3.12+)
- **Database**: MySQL with strict atomic operations.
- **APIs**: Django REST Framework (DRF) with TokenAuthentication.
- **Frontend**: Django Templates, Alertify.js, AJAX for real-time reactivity without full page loads.
- **Session Strategy**: Database-backed sessions with sliding expiration window.

### Webhook Flow Architecture:
```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  NCM Logistics  │ POST  │ Django Webhook  │       │ MySQL Database  │
│ (Sends Updates) ├──────►│ (Verify & Map)  ├──────►│ (Atomic Save)   │
└─────────────────┘       └────────┬────────┘       └─────────────────┘
                                   │
                                   ▼
                          ┌─────────────────┐
                          │   SMS Service   │
                          │ (Twilio/Sparrow)│
                          └─────────────────┘
```

---

## 🧩 Key Apps & Modules

| App | Description |
|-----|-------------|
| **`accounts`** | Custom User model, Role configurations, and fine-grained permissions. |
| **`dashboard`** | Central command module: Products, Orders, ReturnRequests, Dispatches. |
| **`inventory`** | Real-time backorder tracking, atomic inventory transactions. |
| **`ncm` / `pnd`**| Real-time logistics API endpoints, Webhook handler, status sync services. |
| **`hrm`** | Human Resource Management, Leave Policies, Custom color-coding. |
| **`services`** | Shared business logic (`sms_service.py`). |
| **`chat`** | Internal messaging and communication between staff members. |
| **`todo`** | Internal ticketing system for task tracking. |
| **`store`** | The customer-facing storefront. |

---

## ⚡ Real-Time Webhooks & Logistics

The integration with logistics partners provides live tracking updates instantly without manual polling. The system relies on a central `NCMWebhookHandler` (`ncm/webhook_handler.py`).

### Status Updates Flow

#### 1. Delivered & COD Flow
NCM sends `"status": "Delivered", "cod_amount": 1500.00`.
- System maps to `status = "delivered"`.
- Updates `payment_status = "paid"` and `cod_collected = 1500.00`.
- SMS sent: *"✅ Your order has been delivered!"*
- Alertify notification shown in green to staff.

#### 2. Returned Flow
NCM sends `"status": "Returned"`.
- System maps to `status = "returned"`.
- SMS sent: *"↩️ Order Returned"*
- Alertify notification shown in red to staff.

#### 3. In Transit Flow
NCM sends `"status": "Out for Delivery"`.
- System maps to `status = "in_transit"`.
- SMS sent: *"📍 In Transit"*
- Alertify notification shown in blue to staff.

### API Endpoints
JavaScript-accessible endpoints for frontend polling:
- `GET /ncm/api/order/<id>/status/` - Fetch single order status.
- `POST /ncm/api/order/<id>/sync/` - Manual sync trigger.
- `GET /ncm/api/orders/batch-status/` - Fetch multiple orders at once.
- `GET /ncm/api/order/<id>/activity/` - Get order activity log.
- `GET /ncm/api/check-pending-updates/` - Find orders needing updates.

### Webhook Security
1. **HMAC-SHA256 Verification**: Every payload is authenticated via the `X-NCM-Signature` header.
2. **Idempotency Protection**: Duplicate webhook IDs are checked against `WebhookLog` to prevent duplicate processing.
3. **Atomic Transactions**: Wrapped in `@transaction.atomic()` to guarantee complete state updates.
4. **CSRF Exemption**: Exclusively allowed for the webhook endpoint to accept cross-server POST requests.

---

## 📦 Inventory & Backorders

The core `inventory` module runs on atomic services to ensure consistency under high concurrency.
- **Allocation Check**: Automatically reserves stock on checkout.
- **Backorder Support**: Permits overselling strictly for configured products.
- **Restock Services**: Fulfills pending backorders immediately when supplier shipments arrive.

---

## 👥 HRM & Staff Performance

Built for mid-to-large operations, the `hrm` app centralizes internal operational integrity:
- **Leave Operations**: Defines `min_days`, `max_days`, custom visual markers. Manager approval queues.
- **Target Metrics**: Associates individual performance goals (e.g. Sales numbers, Dispatch speed) and generates KPI visualizations via the `dashboard.models.StaffPerformance` model.

---

## 📱 Multi-Provider SMS System

Located in `services/sms_service.py`.

- **Twilio**: For global deployment.
- **Sparrow SMS**: Leading provider in Nepal.
- **Atuha**: Alternative provider in Nepal.
- **Console**: Development routing (prints to terminal).

Configured entirely via `.env` (e.g. `SMS_PROVIDER=sparrow`).

---

## 🖥️ Frontend Real-Time Notifications

Integrated seamlessly via `Alertify.js` and AJAX polling:
- **Order Detail Page**: Fetches status every 60 seconds. Detects changes in status or payment state and pops up Alertify notifications. Reloads automatically upon status change.
- **Orders List**: Batch-syncs all visible rows and applies CSS flash animations to updated rows.
- **Hotkeys**: Press `Ctrl+Shift+R` to instantly toggle auto-sync.

---

## 🧪 Testing & Verification

### Simulating a Webhook Request locally:
```bash
#!/bin/bash
WEBHOOK_URL="http://localhost:8000/ncm/webhook/"
SECRET="your-webhook-secret"

# Payload for delivered order
PAYLOAD='{
    "webhook_id": "test-delivered-001",
    "event": "order_status_update",
    "order_id": 12345,
    "status": "Delivered",
    "cod_amount": 1500.00,
    "timestamp": "2024-02-16T10:30:00Z"
}'

# Generate signature
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -mac HMAC -macopt key="$SECRET" -hex | cut -d' ' -f2)

# Send webhook
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-NCM-Signature: $SIGNATURE" \
  -d "$PAYLOAD" \
  "$WEBHOOK_URL"
```
Expect a `200 OK` and logs updating.

---

## 🆘 Troubleshooting Guide

- **Webhook not received / 401 Unauthorized**: Ensure `NCM_WEBHOOK_SECRET` exactly matches the sender's configuration. Ensure URL uses HTTPS.
- **Status Not Updating**: Ensure the order possesses a valid `ncm_order_id` linking it to the payload.
- **SMS Not Sending**: Ensure `SMS_ENABLED=True` and verify API keys in `.env`.
- **Alertify Not Showing**: Ensure `base.html` includes the Alertify JS/CSS CDNs and check browser console for JS errors.

---

## 📝 Logs & Audit Trail

Three dedicated log streams manage audibility (`logs/` directory):
1. **`ncm_integration.log`**: Logs general logistics API calls and interactions.
2. **`ncm_webhooks.log`**: Logs incoming webhooks, signatures, duplicates, and parsing results.
3. **`ncm_sms.log`**: Logs SMS queue states and API errors from Sparrow/Twilio.

**Database Audit Trail**:
Every time an order updates, an `OrderActivityLog` is securely written. It records `old_value`, `new_value`, `action_type`, and `timestamp`.

---

## 🚀 Production Checklist

- [ ] Set `DEBUG=False` in Django settings.
- [ ] Configure `ALLOWED_HOSTS` properly.
- [ ] Ensure `NCM_WEBHOOK_SECRET` is set in production environment variables.
- [ ] Ensure `SMS_PROVIDER` is set properly (e.g. `sparrow`).
- [ ] Enable SSL/HTTPS on the production server (Required for Webhooks).
- [ ] Apply log rotation for the `logs/` directory to prevent disk bloat.
- [ ] Run `python manage.py collectstatic` and restart WSGI/ASGI service.

---

**Status**: ✅ **PRODUCTION READY** | **Security**: Enterprise Grade  
Developed by the **EcommerceAdmin Engineering Team**.
