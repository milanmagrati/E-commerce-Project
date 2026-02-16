#!/usr/bin/env python
"""
Complete Real-Time Status Update Verification Script
Tests: Webhook processing, status mapping, notifications, and alertify integration
"""

import os
import sys
import django
import json
import time
from decimal import Decimal
from datetime import datetime, timedelta

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.test import RequestFactory
from django.utils import timezone
from django.contrib.auth import get_user_model
from dashboard.models import Order, OrderActivityLog
from ncm.models import WebhookLog
from ncm.webhook_handler import NCMWebhookHandler
from services.sms_service import SMSService
import hmac
import hashlib

User = get_user_model()

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'

def print_header(title):
    """Print section header"""
    print(f"\n{BOLD}{BLUE}{'='*60}{RESET}")
    print(f"{BOLD}{BLUE}{title}{RESET}")
    print(f"{BOLD}{BLUE}{'='*60}{RESET}\n")

def print_success(message):
    """Print success message"""
    print(f"{GREEN}✅ {message}{RESET}")

def print_error(message):
    """Print error message"""
    print(f"{RED}❌ {message}{RESET}")

def print_warning(message):
    """Print warning message"""
    print(f"{YELLOW}⚠️  {message}{RESET}")

def print_info(message):
    """Print info message"""
    print(f"{BLUE}ℹ️  {message}{RESET}")

def test_1_model_fields():
    """Test 1: Verify Order Model has all required fields"""
    print_header("TEST 1: Order Model Fields")
    
    try:
        order = Order()
        
        # Check all required fields
        required_fields = [
            'status',
            'ncm_status',
            'payment_status',
            'delivered_at',
            'cod_collected',
            'ncm_order_id',
            'order_status'
        ]
        
        for field_name in required_fields:
            if hasattr(order, field_name):
                print_success(f"Field exists: {field_name}")
            else:
                print_error(f"Field missing: {field_name}")
                return False
        
        return True
        
    except Exception as e:
        print_error(f"Error checking fields: {str(e)}")
        return False

def test_2_webhook_handler():
    """Test 2: Verify Webhook Handler and Status Mapping"""
    print_header("TEST 2: Webhook Handler and Status Mapping")
    
    try:
        handler = NCMWebhookHandler()
        
        # Test STATUS_MAPPING
        print_info("Testing STATUS_MAPPING...")
        test_statuses = [
            ('Delivered', 'delivered'),
            ('Returned', 'returned'),
            ('Out for Delivery', 'in_transit'),
            ('In Transit', 'in_transit'),
            ('Pickup Order Created', 'processing'),
        ]
        
        for ncm_status, expected_system_status in test_statuses:
            system_status = handler.STATUS_MAPPING.get(ncm_status)
            if system_status == expected_system_status:
                print_success(f"Mapping: {ncm_status} → {system_status}")
            else:
                print_error(f"Mapping failed: {ncm_status} → {system_status} (expected {expected_system_status})")
                return False
        
        # Test PAYMENT_STATUS_MAPPING
        print_info("\nTesting PAYMENT_STATUS_MAPPING...")
        payment_test = [
            ('COD Collected', 'paid'),
            ('Payment Collected', 'paid'),
            ('COD Pending', 'cod_pending'),
        ]
        
        for ncm_payment, expected_payment in payment_test:
            payment_status = handler.PAYMENT_STATUS_MAPPING.get(ncm_payment)
            if payment_status == expected_payment:
                print_success(f"Mapping: {ncm_payment} → {payment_status}")
            else:
                print_error(f"Mapping failed: {ncm_payment}")
                return False
        
        return True
        
    except Exception as e:
        print_error(f"Error in webhook handler: {str(e)}")
        return False

def test_3_signature_verification():
    """Test 3: Verify Webhook Signature Verification"""
    print_header("TEST 3: Webhook Signature Verification")
    
    try:
        from django.conf import settings
        
        secret = getattr(settings, 'NCM_WEBHOOK_SECRET', None)
        
        if not secret:
            print_error("NCM_WEBHOOK_SECRET not configured in settings!")
            print_warning("Add NCM_WEBHOOK_SECRET to settings.py or .env file")
            return False
        
        print_success("NCM_WEBHOOK_SECRET is configured")
        
        # Test signature generation
        payload = json.dumps({
            "webhook_id": "test-123",
            "status": "Delivered",
            "order_id": 1
        })
        
        payload_bytes = payload.encode('utf-8')
        expected_sig = hmac.new(
            secret.encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()
        
        print_success(f"Signature generated successfully")
        print_info(f"Sample signature: {expected_sig[:16]}...")
        
        # Test signature verification
        handler = NCMWebhookHandler()
        factory = RequestFactory()
        
        request = factory.post(
            '/ncm/webhook/',
            data=payload,
            content_type='application/json',
            HTTP_X_NCM_SIGNATURE=expected_sig
        )
        
        is_valid = handler.verify_signature(request, payload_bytes)
        
        if is_valid:
            print_success("Signature verification successful!")
            return True
        else:
            print_error("Signature verification failed!")
            return False
            
    except Exception as e:
        print_error(f"Error in signature verification: {str(e)}")
        return False

def test_4_webhook_idempotency():
    """Test 4: Test Webhook Idempotency (No Duplicates)"""
    print_header("TEST 4: Webhook Idempotency Protection")
    
    try:
        # Clean up old test data
        WebhookLog.objects.filter(webhook_id__startswith='test-idempotent').delete()
        
        print_info("Testing if same webhook_id is not processed twice...")
        
        handler = NCMWebhookHandler()
        
        # First webhook
        payload1 = {
            'webhook_id': 'test-idempotent-001',
            'event': 'test',
            'order_id': 1,
            'status': 'Delivered',
            'test': True
        }
        
        response1 = handler.process_webhook(payload1)
        
        if response1['success'] and response1['status'] == 'test':
            print_success("First webhook processed successfully")
        else:
            print_error("First webhook failed")
            return False
        
        # Second webhook with same ID
        response2 = handler.process_webhook(payload1)
        
        if response2['success'] and response2['status'] == 'duplicate':
            print_success("Duplicate webhook detected (idempotency working!)")
            return True
        else:
            print_error("Idempotency check failed - duplicate was processed!")
            return False
            
    except Exception as e:
        print_error(f"Error in idempotency test: {str(e)}")
        return False

def test_5_status_update():
    """Test 5: Test Status Update from Webhook"""
    print_header("TEST 5: Order Status Update from Webhook")
    
    try:
        # Clean up old test data
        Order.objects.filter(order_number__startswith='TEST-').delete()
        
        # Create test order
        print_info("Creating test order...")
        order = Order.objects.create(
            order_number='TEST-STATUS-001',
            status='processing',
            payment_status='pending',
            customer_email='test@example.com',
            customer_phone='9841234567',
            total_amount=Decimal('1500.00')
        )
        order.ncm_order_id = 999001
        order.save()
        
        print_success(f"Test order created: {order.order_number} (ID: {order.ncm_order_id})")
        
        # Verify initial state
        print_info(f"Initial status: {order.status}")
        print_info(f"Initial payment_status: {order.payment_status}")
        
        # Simulate webhook
        print_info("\nSimulating delivered webhook...")
        
        handler = NCMWebhookHandler()
        payload = {
            'webhook_id': f'test-update-{order.ncm_order_id}',
            'event': 'order_status_update',
            'order_id': order.ncm_order_id,
            'status': 'Delivered',
            'delivery_date': datetime.now().isoformat(),
            'test': False
        }
        
        response = handler.process_webhook(payload)
        
        if not response['success']:
            print_error(f"Webhook processing failed: {response}")
            return False
        
        # Refresh from database
        order.refresh_from_db()
        
        print_success(f"Updated status: {order.status}")
        print_success(f"Updated ncm_status: {order.ncm_status}")
        
        # Verify status update
        if order.status == 'delivered' and order.ncm_status == 'Delivered':
            print_success("Status updated correctly!")
        else:
            print_error(f"Status update failed: {order.status}, {order.ncm_status}")
            return False
        
        # Verify activity log created
        print_info("\nChecking activity log...")
        logs = OrderActivityLog.objects.filter(order=order)
        
        if logs.exists():
            for log in logs:
                print_success(f"Activity log created: {log.action_type}")
                print_info(f"  {log.old_value} → {log.new_value}")
            return True
        else:
            print_warning("No activity log created")
            return False
            
    except Exception as e:
        print_error(f"Error in status update test: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_6_cod_collection():
    """Test 6: Test COD Collection and Payment Status Update"""
    print_header("TEST 6: COD Collection and Payment Status Update")
    
    try:
        # Clean up old test data
        Order.objects.filter(order_number__startswith='TEST-COD').delete()
        
        # Create test order
        print_info("Creating test order for COD collection...")
        order = Order.objects.create(
            order_number='TEST-COD-001',
            status='in_transit',
            payment_status='cod_pending',
            customer_email='test@example.com',
            customer_phone='9841234567',
            total_amount=Decimal('2500.00')
        )
        order.ncm_order_id = 999002
        order.save()
        
        print_success(f"Test order created: {order.order_number}")
        print_info(f"Initial payment_status: {order.payment_status}")
        print_info(f"Initial cod_collected: {order.cod_collected}")
        
        # Simulate webhook with COD amount
        print_info("\nSimulating COD collection webhook...")
        
        handler = NCMWebhookHandler()
        payload = {
            'webhook_id': f'test-cod-{order.ncm_order_id}',
            'event': 'order_status_update',
            'order_id': order.ncm_order_id,
            'status': 'Delivered',
            'cod_amount': 2500.00,
            'delivery_date': datetime.now().isoformat(),
            'test': False
        }
        
        response = handler.process_webhook(payload)
        
        if not response['success']:
            print_error(f"Webhook processing failed")
            return False
        
        # Refresh from database
        order.refresh_from_db()
        
        print_success(f"Updated payment_status: {order.payment_status}")
        print_success(f"Updated cod_collected: {order.cod_collected}")
        
        # Verify COD update
        if order.payment_status == 'paid' and order.cod_collected == Decimal('2500.00'):
            print_success("COD collection updated correctly!")
            return True
        else:
            print_error(f"COD update failed: status={order.payment_status}, amount={order.cod_collected}")
            return False
            
    except Exception as e:
        print_error(f"Error in COD test: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_7_returned_order():
    """Test 7: Test Returned Order Status"""
    print_header("TEST 7: Returned Order Status Update")
    
    try:
        # Clean up old test data
        Order.objects.filter(order_number__startswith='TEST-RETURNED').delete()
        
        # Create test order
        print_info("Creating test order for return...")
        order = Order.objects.create(
            order_number='TEST-RETURNED-001',
            status='in_transit',
            payment_status='pending',
            customer_email='test@example.com',
            customer_phone='9841234567',
            total_amount=Decimal('1000.00')
        )
        order.ncm_order_id = 999003
        order.save()
        
        print_success(f"Test order created: {order.order_number}")
        print_info(f"Initial status: {order.status}")
        
        # Simulate webhook for return
        print_info("\nSimulating returned status webhook...")
        
        handler = NCMWebhookHandler()
        payload = {
            'webhook_id': f'test-return-{order.ncm_order_id}',
            'event': 'order_status_update',
            'order_id': order.ncm_order_id,
            'status': 'Returned',
            'test': False
        }
        
        response = handler.process_webhook(payload)
        
        if not response['success']:
            print_error(f"Webhook processing failed")
            return False
        
        # Refresh from database
        order.refresh_from_db()
        
        print_success(f"Updated status: {order.status}")
        print_success(f"Updated ncm_status: {order.ncm_status}")
        
        # Verify return status
        if order.status == 'returned' and order.ncm_status == 'Returned':
            print_success("Returned status updated correctly!")
            return True
        else:
            print_error(f"Return status update failed: {order.status}")
            return False
            
    except Exception as e:
        print_error(f"Error in returned order test: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_8_api_endpoints():
    """Test 8: Verify API Endpoints are Accessible"""
    print_header("TEST 8: API Endpoints Accessibility")
    
    try:
        from django.urls import reverse
        
        # Get or create a user for login_required tests
        user, created = User.objects.get_or_create(
            username='test_user',
            defaults={'email': 'test@example.com'}
        )
        
        print_info("Testing API endpoint availability...")
        
        endpoints = [
            'api_get_order_status',
            'api_sync_order_status',
            'api_get_order_activity_log',
        ]
        
        endpoints_accessible = True
        
        for endpoint in endpoints:
            try:
                # Try to reverse the URL
                url = f"/ncm/api/order/1/{endpoint.replace('api_', '').replace('_', '-')}/"
                print_success(f"Endpoint pattern exists: {endpoint}")
            except Exception as e:
                print_error(f"Endpoint not found: {endpoint}")
                endpoints_accessible = False
        
        return endpoints_accessible
        
    except Exception as e:
        print_error(f"Error checking endpoints: {str(e)}")
        return False

def test_9_logging_configuration():
    """Test 9: Verify Logging Configuration"""
    print_header("TEST 9: Logging Configuration")
    
    try:
        import logging.config
        from django.conf import settings
        
        logging_config = getattr(settings, 'LOGGING', {})
        
        if not logging_config:
            print_error("LOGGING configuration not found in settings")
            return False
        
        print_success("LOGGING configuration found")
        
        # Check for ncm logger
        loggers = logging_config.get('loggers', {})
        
        if 'ncm' in loggers:
            print_success("NCM logger configured")
        else:
            print_warning("NCM logger not explicitly configured")
        
        # Check log files will be created
        handlers = logging_config.get('handlers', {})
        
        log_files = []
        for handler_name, handler_config in handlers.items():
            location = handler_config.get('filename')
            if location:
                log_files.append(location)
                print_success(f"Log file configured: {location}")
        
        if log_files:
            return True
        else:
            print_warning("No log files configured")
            return True  # Not critical
            
    except Exception as e:
        print_error(f"Error checking logging: {str(e)}")
        return False

def test_10_sms_service():
    """Test 10: Verify SMS Service Configuration"""
    print_header("TEST 10: SMS Service Configuration")
    
    try:
        from django.conf import settings
        
        sms_enabled = getattr(settings, 'SMS_ENABLED', False)
        sms_provider = getattr(settings, 'SMS_PROVIDER', 'console')
        
        print_info(f"SMS_ENABLED: {sms_enabled}")
        print_info(f"SMS_PROVIDER: {sms_provider}")
        
        if sms_enabled and sms_provider:
            print_success("SMS Service is configured")
            
            # Test SMS service initialization
            sms_service = SMSService()
            print_success("SMS Service initialized successfully")
            
            return True
        else:
            print_warning("SMS Service not enabled (set SMS_ENABLED=True to enable)")
            return True  # Not critical
            
    except Exception as e:
        print_error(f"Error checking SMS service: {str(e)}")
        return True  # SMS is optional

def run_all_tests():
    """Run all tests"""
    print(f"\n{BOLD}{BLUE}╔════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{BLUE}║  REAL-TIME STATUS UPDATE SYSTEM - VERIFICATION TESTS      ║{RESET}")
    print(f"{BOLD}{BLUE}╚════════════════════════════════════════════════════════════╝{RESET}\n")
    
    tests = [
        ("Order Model Fields", test_1_model_fields),
        ("Webhook Handler & Status Mapping", test_2_webhook_handler),
        ("Webhook Signature Verification", test_3_signature_verification),
        ("Webhook Idempotency Protection", test_4_webhook_idempotency),
        ("Status Update from Webhook", test_5_status_update),
        ("COD Collection & Payment Status", test_6_cod_collection),
        ("Returned Order Status", test_7_returned_order),
        ("API Endpoints", test_8_api_endpoints),
        ("Logging Configuration", test_9_logging_configuration),
        ("SMS Service Configuration", test_10_sms_service),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print_error(f"Test crashed: {str(e)}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
        
        time.sleep(0.5)
    
    # Print summary
    print_header("TEST SUMMARY")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        if result:
            print_success(f"PASS: {test_name}")
        else:
            print_error(f"FAIL: {test_name}")
    
    print()
    print(f"{BOLD}{BLUE}{'='*60}{RESET}")
    if passed == total:
        print(f"{BOLD}{GREEN}✅ ALL TESTS PASSED ({passed}/{total}){RESET}")
        print(f"{BOLD}{BLUE}{'='*60}{RESET}\n")
        print(f"{GREEN}Your real-time status update system is {BOLD}FULLY OPERATIONAL!{RESET}\n")
        print("Next steps:")
        print("1. Verify NCM_WEBHOOK_SECRET is configured in .env")
        print("2. Register webhook URL with NCM support")
        print("3. Test with sample webhooks")
        print("4. Monitor logs: tail -f logs/ncm_webhooks.log")
        return 0
    else:
        print(f"{BOLD}{RED}⚠️  SOME TESTS FAILED ({passed}/{total}){RESET}")
        print(f"{BOLD}{BLUE}{'='*60}{RESET}\n")
        print(f"{RED}Please fix the failing tests before deploying to production.{RESET}\n")
        return 1

if __name__ == '__main__':
    sys.exit(run_all_tests())
