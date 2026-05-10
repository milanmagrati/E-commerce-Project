# EcommerceAdmin Project Documentation

## Overview
EcommerceAdmin is a comprehensive Django-based e-commerce admin system designed for real-time order management, inventory, logistics, staff performance, and integration with Nepali logistics providers (NCM, Pick and Drop, etc.). It features granular user permissions, robust return management, real-time webhook synchronization, and multi-provider SMS notifications.

---

## Table of Contents
1. [Project Structure](#project-structure)
2. [Core Features](#core-features)
3. [Key Apps & Modules](#key-apps--modules)
4. [Setup & Configuration](#setup--configuration)
5. [Webhook & Real-Time Sync](#webhook--real-time-sync)
6. [SMS Notification System](#sms-notification-system)
7. [Staff & Performance](#staff--performance)
8. [Return & Dispatch Management](#return--dispatch-management)
9. [Purchase & Supplier Management](#purchase--supplier-management)
10. [Documentation Index](#documentation-index)

---

## Project Structure

- **accounts/**: Custom user model, roles, and permissions
- **dashboard/**: Products, orders, inventory, returns, dispatch, reporting
- **ncm/**: NCM logistics integration, webhook handler, real-time APIs
- **services/**: SMS notification service, logistics services
- **store/**: Storefront and context processors
- **integrations/**: Third-party integrations
- **pick_and_drop/**: Pick and Drop logistics integration
- **hrm/**: HR management
- **todo/**: Internal ticketing/todo system
- **media/**, **static/**: Uploaded files and static assets

---

## Core Features
- Real-time order status sync via webhooks (NCM, Pick and Drop)
- Granular user roles and permissions
- Multi-provider SMS notifications (Twilio, Sparrow, Atuha)
- Inventory, product, and bundle management
- Staff performance tracking and targets
- Robust return and refund management
- Purchase and supplier management
- Comprehensive logging and audit trails

---

## Key Apps & Modules
- **accounts**: CustomUser, Role, permissions
- **dashboard**: Product, Order, ReturnRequest, Dispatch, StaffPerformance, Purchase, Supplier
- **ncm**: Webhook handler, real-time API endpoints
- **services/sms_service.py**: SMS sending logic for multiple providers

---

## Setup & Configuration
1. **Clone the repository** and create a virtual environment:
   ```bash
   git clone <repo-url>
   cd EcommerceAdmin
   python -m venv .venv
   source .venv/bin/activate
   pip install -r myproject/requirements.txt
   ```
2. **Configure environment variables** in `.env`:
   - `SECRET_KEY`, `DEBUG`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `NCM_WEBHOOK_SECRET`, etc.
3. **Apply migrations**:
   ```bash
   python myproject/manage.py migrate
   ```
4. **Create a superuser**:
   ```bash
   python myproject/manage.py createsuperuser
   ```
5. **Run the development server**:
   ```bash
   python myproject/manage.py runserver
   ```

---

## Webhook & Real-Time Sync
- Webhook endpoint: `/ncm/webhook/` (POST)
- Real-time API endpoints for order status, batch sync, activity log
- HMAC-SHA256 signature verification for security
- Logging: `logs/ncm_integration.log`, `logs/ncm_webhooks.log`, `logs/ncm_sms.log`

---

## SMS Notification System
- Supports Twilio, Sparrow SMS, Atuha, and console (dev)
- Automatic SMS on order status changes
- Configurable via `.env` and `services/sms_service.py`

---

## Staff & Performance
- Staff performance tracked via `dashboard.models.StaffPerformance`
- Targets and metrics for sales, warehouse, etc.

---

## Return & Dispatch Management
- Robust return request and item models
- Dispatch/batch management for logistics
- Soft delete and restore for all major models

---

## Purchase & Supplier Management
- Supplier, purchase, and payment tracking
- Outstanding calculation and payment status

---

## Documentation Index
See `DOCUMENTATION_INDEX.md` for a full list of guides, quick starts, and technical references.

---

## Credits
Developed by the EcommerceAdmin team. For more details, see individual module docstrings and the documentation files in the project root.
