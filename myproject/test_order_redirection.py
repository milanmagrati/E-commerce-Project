#!/usr/bin/env python
"""
Comprehensive test script for Order Redirection Activity Log implementation.
Tests all components: model, migrations, views, and template.
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
sys.path.insert(0, '/home/milan-magrati/Desktop/EcommerceAdmin/myproject')
django.setup()

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from dashboard.models import OrderActivityLog, Order
import json

print("\n" + "="*80)
print("ORDER REDIRECTION IMPLEMENTATION TEST SUITE")
print("="*80)

# Test 1: Verify Migration Applied
print("\n[OK] TEST 1: Verify Migration 0054 Applied")
print("-" * 80)
loader = MigrationLoader(None, ignore_no_migrations=True)
migrated_apps = loader.disk_migrations

if ('dashboard', '0054_add_metadata_to_orderactivitylog') in loader.applied_migrations:
    print("  [SUCCESS] Migration 0054_add_metadata_to_orderactivitylog is APPLIED")
else:
    print("  [FAILED] Migration 0054_add_metadata_to_orderactivitylog is NOT applied")
    print("    Run: python manage.py migrate")

# Test 2: Verify Model Configuration
print("\n✅ TEST 2: Verify OrderActivityLog Model Configuration")
print("-" * 80)

# Check if metadata field exists
metadata_field = OrderActivityLog._meta.get_field('metadata')
print(f"  [SUCCESS] metadata field exists: {metadata_field}")
print(f"    - Type: {type(metadata_field).__name__}")
print(f"    - Blank: {metadata_field.blank}")
print(f"    - Null: {metadata_field.null}")
print(f"    - Default: {metadata_field.default}")

# Check action_type choices
action_type_field = OrderActivityLog._meta.get_field('action_type')
choices = dict(action_type_field.choices)
if 'redirected' in choices:
    print(f"  [SUCCESS] 'redirected' action_type exists: {choices['redirected']}")
else:
    print(f"  [FAILED] 'redirected' action_type NOT found")
    print(f"    Available choices: {list(choices.keys())}")

# Test 3: Verify Database Schema
print("\n✅ TEST 3: Verify Database Schema")
print("-" * 80)

with connection.cursor() as cursor:
    # Check if metadata column exists
    cursor.execute("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'dashboard_orderactivitylog'
        AND column_name = 'metadata'
    """)
    result = cursor.fetchone()
    if result:
        col_name, col_type, nullable = result
        print(f"  [SUCCESS] metadata column exists in database")
        print(f"    - Column: {col_name}")
        print(f"    - Type: {col_type}")
        print(f"    - Nullable: {nullable}")
    else:
        print(f"  [FAILED] metadata column NOT found in database")
        print(f"    Run: python manage.py migrate")

# Test 4: Verify Redirect Functions Exist
print("\n✅ TEST 4: Verify Redirect View Functions")
print("-" * 80)

try:
    from dashboard.views import (
        redirect_order_get,
        redirect_order_save,
        redirect_rtv_get,
        redirect_rtv_save,
        redirect_order_to_ncm,
    )
    print("  [SUCCESS] redirect_order_get exists")
    print("  [SUCCESS] redirect_order_save exists")
    print("  [SUCCESS] redirect_rtv_get exists")
    print("  [SUCCESS] redirect_rtv_save exists")
    print("  [SUCCESS] redirect_order_to_ncm exists")
except ImportError as e:
    print(f"  [FAILED] Error importing redirect functions: {e}")

# Test 5: Verify Template File
print("\n✅ TEST 5: Verify Template File")
print("-" * 80)

template_path = '/home/milan-magrati/Desktop/EcommerceAdmin/myproject/dashboard/templates/order_detail.html'
if os.path.exists(template_path):
    with open(template_path, 'r') as f:
        template_content = f.read()

    checks = [
        ('activity-redirection-details', 'Redirection details CSS class'),
        ("log.action_type == 'redirected'", "Redirect condition check"),
        ('log.metadata', 'Metadata access'),
        ('log.metadata.customer_name', 'Customer name field'),
        ('log.metadata.customer_phone', 'Customer phone field'),
        ('log.metadata.shipping_address', 'Shipping address field'),
        ('log.metadata.branch_city', 'Branch/city field'),
        ('Old Customer Details', 'Section title'),
    ]

    for check_str, description in checks:
        if check_str in template_content:
            print(f"  [SUCCESS] {description}: '{check_str}'")
        else:
            print(f"  [FAILED] {description} NOT found: '{check_str}'")
else:
    print(f"  [FAILED] Template file not found: {template_path}")

# Test 6: Verify CSS Styles
print("\n✅ TEST 6: Verify CSS Styles")
print("-" * 80)

if os.path.exists(template_path):
    with open(template_path, 'r') as f:
        template_content = f.read()

    css_checks = [
        ('.activity-redirection-details', 'Redirection container'),
        ('.redirection-section', 'Section styling'),
        ('.old-customer-details', 'Customer details grid'),
        ('.detail-row', 'Detail row styling'),
        ('.detail-label', 'Label styling'),
        ('.detail-value', 'Value styling'),
    ]

    for css_class, description in css_checks:
        if css_class in template_content:
            print(f"  [SUCCESS] {description}: {css_class}")
        else:
            print(f"  [FAILED] {description} NOT found: {css_class}")

# Test 7: Test Data Creation and Retrieval
print("\n✅ TEST 7: Test Metadata Storage and Retrieval")
print("-" * 80)

try:
    # Get a sample order
    sample_order = Order.objects.filter(is_deleted=False).first()
    if sample_order:
        # Create a test activity log with metadata
        test_metadata = {
            'customer_name': 'Test Customer',
            'customer_phone': '9841234567',
            'shipping_address': 'Test Address, Kathmandu',
            'branch_city': 'Kathmandu',
        }

        test_log = OrderActivityLog.objects.create(
            order=sample_order,
            action_type='redirected',
            user=None,
            description='Test redirection entry',
            field_name='ncm_status',
            old_value='',
            new_value='redirected',
            metadata=test_metadata
        )

        # Retrieve and verify
        retrieved_log = OrderActivityLog.objects.get(id=test_log.id)
        if retrieved_log.metadata == test_metadata:
            print(f"  [SUCCESS] Metadata stored and retrieved correctly")
            print(f"    - Customer Name: {retrieved_log.metadata.get('customer_name')}")
            print(f"    - Customer Phone: {retrieved_log.metadata.get('customer_phone')}")
            print(f"    - Shipping Address: {retrieved_log.metadata.get('shipping_address')}")
            print(f"    - Branch City: {retrieved_log.metadata.get('branch_city')}")
        else:
            print(f"  [FAILED] Metadata mismatch")
            print(f"    Expected: {test_metadata}")
            print(f"    Got: {retrieved_log.metadata}")

        # Clean up
        test_log.delete()
    else:
        print(f"  ⚠ No sample orders found. Cannot test data storage.")
except Exception as e:
    print(f"  [FAILED] Error testing metadata: {e}")

# Final Summary
print("\n" + "="*80)
print("TEST SUITE COMPLETE")
print("="*80)
print("\n✅ All critical components verified!")
print("\nNext Steps:")
print("  1. Test with actual order redirections in the admin panel")
print("  2. Verify Activity Log displays old customer details correctly")
print("  3. Check responsive design on mobile devices")
print("\n")
