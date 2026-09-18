#!/usr/bin/env python3
"""
Regression tests for the dashboard/inventory stock-accounting bugs.

  1. restore_order_stock() gave stock back whenever a caller felt like it,
     with no check that the stock had ever been deducted. Order stock is
     deducted in exactly one place -- the dispatch scan, which records a
     successful DispatchItem -- but an order reaches order_status='dispatched'
     by many other routes (manual status change, bulk status action, order
     edit, logistics sync). Cancelling/trashing such an order invented
     inventory that never left the shelf, and a dispatched -> processing ->
     dispatched -> cancelled cycle inflated it again every lap.

  2. allocate_order() overwrote the OrderItem's reserved_qty but accumulated
     onto the Product's, so allocating the same order twice reserved the
     stock twice and leaked reserved_qty that nothing ever released. The
     leaked amount reads as unavailable forever, quietly pushing later
     orders into backorder.

Everything runs inside a transaction that is rolled back.

Usage:
    python test_inventory_stock_integrity.py
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from django.db import transaction
from accounts.models import CustomUser
from dashboard.models import (
    Product, Order as DashOrder, OrderItem as DashOrderItem,
    Dispatch, DispatchItem,
)
from inventory.services import restore_order_stock, allocate_order

failures = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {actual}, expected {expected}")
    if not ok:
        failures.append(name)


try:
    with transaction.atomic():
        user = CustomUser.objects.filter(is_superuser=True).first()
        if user is None:
            user = CustomUser.objects.create_superuser(
                username='zz_inv_admin', email='zzinv@example.invalid',
                password='x', role='administrator',
            )

        def make_product(tag, stock):
            return Product.objects.create(
                user=user, name=f'ZZ Stock Product {tag}',
                slug=f'zz-stock-product-{tag}', description='ZZ test',
                price=Decimal('100.00'), stock=stock,
                reserved_qty=0, backordered_qty=0,
            )

        def make_order(tag, product, qty):
            o = DashOrder.objects.create(
                order_number=f'ZZORD{tag}', customer_name='ZZ Test',
                customer_phone='9800000000', shipping_address='ZZ address',
                branch_city='ZZ city', order_from='manual',
                payment_method='cod', order_status='dispatched',
                created_by=user, total_amount=Decimal('100.00'),
            )
            DashOrderItem.objects.create(
                order=o, product=product, quantity=qty,
                price=Decimal('100.00'),
            )
            return o

        # ── 1. No dispatch on record -> nothing to give back ──
        print("\n1. Status says 'dispatched' but nothing was ever dispatched")
        p1 = make_product('01', 100)
        o1 = make_order('01', p1, 10)
        result = restore_order_stock(o1)
        p1.refresh_from_db()
        check('restore reported nothing done', result, False)
        check('stock left alone', p1.stock, 100)

        # ── 2. A real dispatch -> stock is restored, exactly once ──
        print("\n2. A genuinely dispatched order gets its stock back")
        p2 = make_product('02', 100)
        o2 = make_order('02', p2, 10)
        # Mirror the dispatch scan: deduct, and record the successful item.
        p2.stock -= 10
        p2.save(update_fields=['stock'])
        dispatch = Dispatch.objects.create(
            batch_number='ZZBATCH01', logistics='ncm', created_by=user)
        di = DispatchItem.objects.create(
            dispatch=dispatch, order=o2, scanned_order_id=o2.order_number,
            dispatch_status='success',
        )
        check('stock deducted by dispatch', Product.objects.get(pk=p2.pk).stock, 90)

        check('restore reported done', restore_order_stock(o2), True)
        p2.refresh_from_db()
        check('stock restored', p2.stock, 100)

        # ── 3. Once the dispatch row is invalidated, no second restore ──
        print("\n3. After the dispatch row is invalidated, restore is a no-op")
        di.dispatch_status = 'failed'
        di.save(update_fields=['dispatch_status'])
        check('second restore refused', restore_order_stock(o2), False)
        p2.refresh_from_db()
        check('stock not inflated', p2.stock, 100)

        # ── 4. force=True still restores for a caller that knows better ──
        print("\n4. force=True bypasses the guard")
        p4 = make_product('04', 50)
        o4 = make_order('04', p4, 5)
        check('forced restore ran', restore_order_stock(o4, force=True), True)
        p4.refresh_from_db()
        check('stock restored under force', p4.stock, 55)

        # ── 5. Allocating the same order twice reserves stock once ──
        print("\n5. Re-allocating an order does not double-reserve")
        from store.models import Order as StoreOrder, OrderItem as StoreOrderItem
        p5 = make_product('05', 100)
        so = StoreOrder.objects.create(
            user=user, order_number='ZZSTORE05', status='confirmed',
            shipping_address='ZZ address', total_price=Decimal('100.00'),
        )
        si = StoreOrderItem.objects.create(
            order=so, product=p5, quantity=8, price=Decimal('100.00'),
        )
        allocate_order(so)
        p5.refresh_from_db()
        si.refresh_from_db()
        check('reserved after first allocate', p5.reserved_qty, 8)
        check('item reserved', si.reserved_qty, 8)

        allocate_order(so)   # confirm the same order a second time
        p5.refresh_from_db()
        si.refresh_from_db()
        check('reserved still 8 after second allocate', p5.reserved_qty, 8)
        check('item still 8', si.reserved_qty, 8)
        check('stock untouched by reservation', p5.stock, 100)

        # ── 6. Backorders are not double-counted either ──
        print("\n6. Re-allocating a short-stocked order does not double-backorder")
        p6 = make_product('06', 3)
        p6.backorders_allowed = True
        p6.save(update_fields=['backorders_allowed'])
        so6 = StoreOrder.objects.create(
            user=user, order_number='ZZSTORE06', status='confirmed',
            shipping_address='ZZ address', total_price=Decimal('100.00'),
        )
        si6 = StoreOrderItem.objects.create(
            order=so6, product=p6, quantity=10, price=Decimal('100.00'),
        )
        allocate_order(so6)
        p6.refresh_from_db()
        check('reserved what it could', p6.reserved_qty, 3)
        check('backordered the rest', p6.backordered_qty, 7)

        allocate_order(so6)
        p6.refresh_from_db()
        check('reserved unchanged on re-run', p6.reserved_qty, 3)
        check('backordered unchanged on re-run', p6.backordered_qty, 7)

        raise transaction.TransactionManagementError('__ROLLBACK__')

except transaction.TransactionManagementError as e:
    if '__ROLLBACK__' not in str(e):
        raise

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("All checks passed. Test data rolled back.")
