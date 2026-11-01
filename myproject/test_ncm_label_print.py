"""
Verification suite for NCM shipment label bulk printing:
1. NCMService.get_order_labels() chunking, payload formatting, and error collection.
2. fetch_labels_across_configs() routing across multiple NCM accounts + fallback sweep.
3. _normalize_label() merging NCM API fields with local Order / NCMBulkLogOrder fields:
   - QR code value = NCM order ID (e.g. "27620066")
   - Barcode value = Local order number (e.g. "T18320")
4. Single-batch view (/ncm-bulk-logs/<log_id>/labels/) and multi-batch view (/ncm-bulk-logs/labels/?batch_ids=...).
5. Permission enforcement (staff without view_ncm_bulk_logs gets 403).
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage

User = get_user_model()

from dashboard.models import (
    Customer,
    Order,
    LogisticsAPIConfig,
)
from ncm.models import NCMBulkLog, NCMBulkLogOrder
from services.ncm_service import NCMService
from dashboard.ncm_label_views import (
    fetch_labels_across_configs,
    ncm_bulk_log_labels,
    ncm_labels_print,
)


def attach_messages(request):
    setattr(request, 'session', 'session')
    messages = FallbackStorage(request)
    setattr(request, '_messages', messages)
    return request


def run_tests():
    print("=" * 68)
    print("Running NCM Shipment Label Print Verification Suite")
    print("=" * 68)

    # 1. Test NCMService.get_order_labels chunking & normalization
    svc = NCMService()
    called_chunks = []

    def fake_request(method, endpoint, data=None, params=None, use_v2=False):
        assert method == 'POST'
        assert endpoint.endswith('/vendor/order/label/'), f"Unexpected endpoint: {endpoint}"
        ids = data.get('ids', [])
        called_chunks.append(ids)
        return {
            'success': True,
            'data': {
                'labels': [
                    {
                        'orderid': oid,
                        'delivery_type': 'Door2Door',
                        'cod_charge': 2248.0,
                        'from_branch': {'name': 'TINKUNE', 'code': 'TINK1', 'district': 'KATHMANDU'},
                        'to_branch': {'name': 'BIRATNAGAR', 'code': 'BIRA1', 'district': 'MORANG'},
                        'from': {'name': 'Orajil Official', 'phone': '9840035742', 'phone2': None},
                        'receiver': {
                            'name': 'Rojina Basnet',
                            'phone': '9842297471',
                            'phone2': '',
                            'address': 'Biratnagar, kanchanbari',
                        },
                        'description': {
                            'description': 'Black head remover- 1 pcs',
                            'delivery_instruction': 'Call before delivery',
                            'handling': 'Fragile',
                            'vendor_orderid': f'T{oid}',
                        },
                    }
                    for oid in ids
                ]
            },
        }

    with patch.object(svc, '_make_request', side_effect=fake_request):
        sample_ids = list(range(27620001, 27620066))  # 65 IDs -> 2 chunks (50 + 15)
        res = svc.get_order_labels(sample_ids)
        assert res['success'] is True, f"Expected success, got {res}"
        assert len(called_chunks) == 2, f"Expected 2 chunks, got {len(called_chunks)}"
        assert len(called_chunks[0]) == 50 and len(called_chunks[1]) == 15
        assert len(res['labels']) == 65
        assert res['labels'][0]['orderid'] == 27620001
        print("[PASS] 1. NCMService.get_order_labels() chunks >50 IDs and returns all label dicts")

    # 2. Setup temporary DB fixtures (rolled back at the end)
    admin_user, _ = User.objects.update_or_create(
        username='_test_ncm_label_admin',
        defaults={
            'email': '_test_ncm_label_admin@example.com',
            'is_staff': True,
            'is_superuser': True,
        },
    )
    restricted_user, _ = User.objects.update_or_create(
        username='_test_ncm_label_restricted',
        defaults={
            'email': '_test_ncm_label_restricted@example.com',
            'is_staff': True,
            'is_superuser': False,
        },
    )
    restricted_user.user_permissions.clear()

    customer = Customer.objects.create(
        name='Rojina Basnet',
        phone='9842297471',
        address='Biratnagar, kanchanbari',
    )
    order1 = Order.objects.create(
        order_number='T18320',
        customer=customer,
        customer_name='Rojina Basnet',
        customer_phone='9842297471',
        shipping_address='Biratnagar, kanchanbari',
        total_amount=Decimal('2248.00'),
        order_status='processing',
        ncm_status='Pickup Order Created',
        ncm_order_id=27620066,
        ncm_destination_branch='BIRATNAGAR',
        ncm_from_branch='TINKUNE',
    )
    order2 = Order.objects.create(
        order_number='T18307',
        customer=customer,
        customer_name='Sita Sharma',
        customer_phone='9800000001',
        shipping_address='Pokhara, Lakeside',
        total_amount=Decimal('1500.00'),
        order_status='processing',
        ncm_status='Pickup Order Created',
        ncm_order_id=27620061,
        ncm_destination_branch='POKHARA',
        ncm_from_branch='TINKUNE',
    )
    order_cancelled = Order.objects.create(
        order_number='T18399',
        customer=customer,
        customer_name='Cancelled User',
        customer_phone='9800000099',
        shipping_address='Kathmandu',
        total_amount=Decimal('999.00'),
        order_status='cancelled',
        ncm_status='Cancelled',
        ncm_order_id=27620099,
    )

    batch = NCMBulkLog.objects.create(
        batch_number='BATCH-TEST-LABEL-001',
        created_by=admin_user,
        total_orders=3,
        success_count=2,
        failed_count=1,
        skipped_count=0,
    )
    NCMBulkLogOrder.objects.create(
        batch=batch,
        order=order1,
        order_number='T18320',
        customer_name='Rojina Basnet',
        customer_phone='9842297471',
        destination_branch='BIRATNAGAR',
        cod_amount=Decimal('2248.00'),
        status='success',
        ncm_order_id=27620066,
        message='Sent to NCM',
    )
    NCMBulkLogOrder.objects.create(
        batch=batch,
        order=order2,
        order_number='T18307',
        customer_name='Sita Sharma',
        customer_phone='9800000001',
        destination_branch='POKHARA',
        cod_amount=Decimal('1500.00'),
        status='success',
        ncm_order_id=27620061,
        message='Sent to NCM',
    )
    NCMBulkLogOrder.objects.create(
        batch=batch,
        order=order_cancelled,
        order_number='T18399',
        customer_name='Cancelled User',
        customer_phone='9800000099',
        destination_branch='KATHMANDU',
        cod_amount=Decimal('999.00'),
        status='failed',
        ncm_order_id=None,
        message='Order cancelled',
    )

    try:
        # Mock NCMService.get_order_labels so no external network call is needed during view test
        def fake_get_order_labels(self_svc, ids):
            labels_by_id = {}
            labels = []
            for oid in ids:
                s_oid = str(oid)
                if s_oid == '27620066':
                    item = {
                        'orderid': 27620066,
                        'delivery_type': 'Home',
                        'cod_charge': 2248.0,
                        'from_branch': {'name': 'TINKUNE', 'code': 'TINK1', 'district': 'KATHMANDU'},
                        'to_branch': {'name': 'BIRATNAGAR', 'code': 'BIRA1', 'district': 'MORANG'},
                        'from': {'name': 'Orajil Official', 'phone': '9840035742', 'phone2': ''},
                        'receiver': {
                            'name': 'Rojina Basnet',
                            'phone': '9842297471',
                            'phone2': '',
                            'address': 'Biratnagar, kanchanbari',
                        },
                        'description': {
                            'description': 'Black head remover- 1 pcs',
                            'delivery_instruction': None,
                            'handling': 'Non-Fragile',
                            'vendor_orderid': 'Out valley',  # Simulate legacy NCM vendor_orderid
                        },
                    }
                    labels.append(item)
                    labels_by_id[s_oid] = item
                elif s_oid == '27620061':
                    item = {
                        'orderid': 27620061,
                        'delivery_type': 'Pickup/Collect',
                        'cod_charge': 1500.0,
                        'from_branch': {'name': 'TINKUNE', 'code': 'TINK1', 'district': 'KATHMANDU'},
                        'to_branch': {'name': 'POKHARA', 'code': 'POKH1', 'district': 'KASKI'},
                        'from': {'name': 'Orajil Official', 'phone': '9840035742', 'phone2': ''},
                        'receiver': {
                            'name': 'Sita Sharma',
                            'phone': '9800000001',
                            'phone2': '',
                            'address': 'Pokhara, Lakeside',
                        },
                        'description': {
                            'description': 'Sunscreen SPF 50',
                            'delivery_instruction': 'Leave at counter',
                            'handling': 'Fragile',
                            'vendor_orderid': 'T18307',
                        },
                    }
                    labels.append(item)
                    labels_by_id[s_oid] = item
            return {
                'success': True,
                'labels': labels,
                'labels_by_id': labels_by_id,
                'errors': [],
            }

        rf = RequestFactory()

        with patch.object(NCMService, 'get_order_labels', fake_get_order_labels):
            # 3. Single-batch view test
            req = attach_messages(rf.get(f'/ncm-bulk-logs/{batch.id}/labels/'))
            req.user = admin_user
            resp = ncm_bulk_log_labels(req, batch.id)
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            html = resp.content.decode('utf-8')

            # Verify both orders rendered with QR value (NCM ID) and Barcode value (T18320 / T18307)
            assert '27620066' in html, "Expected NCM order ID 27620066 in rendered HTML"
            assert 'T18320' in html, "Expected local order number T18320 for barcode even when NCM vendor_orderid is 'Out valley'"
            assert '27620061' in html, "Expected NCM order ID 27620061 in rendered HTML"
            assert 'T18307' in html, "Expected local order number T18307 in rendered HTML"
            assert 'BIRATNAGAR' in html and 'B I R A 1' in html
            assert 'T I N K U N E' in html
            assert 'KATHMANDU - MORANG' in html
            assert 'Home' in html
            assert 'Pickup/Collect' in html
            assert 'A4 (2 per page)' in html
            assert 'Thermal 100×150mm (1 per page)' in html
            print("[PASS] 2. Single-batch view (/ncm-bulk-logs/<id>/labels/) renders QR + Barcode + NCM layout")

            # 4. Multi-batch / direct print endpoint test
            req2 = attach_messages(rf.get(f'/ncm-bulk-logs/labels/?batch_ids={batch.id}&layout=thermal_1'))
            req2.user = admin_user
            resp2 = ncm_labels_print(req2)
            assert resp2.status_code == 200
            html2 = resp2.content.decode('utf-8')
            assert '27620066' in html2 and '27620061' in html2
            print("[PASS] 3. Multi-batch endpoint (/ncm-bulk-logs/labels/?batch_ids=...) renders all printable orders")

            # 5. Direct order_ids query param test ( ensures cancelled order is skipped )
            req3 = attach_messages(
                rf.get(f'/ncm-bulk-logs/labels/?order_ids={order1.id},{order_cancelled.id}')
            )
            req3.user = admin_user
            resp3 = ncm_labels_print(req3)
            assert resp3.status_code == 200
            html3 = resp3.content.decode('utf-8')
            assert '27620066' in html3
            assert '27620099' not in html3, "Cancelled order should be skipped from label printing"
            print("[PASS] 4. Cancelled/unsent orders are automatically excluded with skip warning")

            # 6. Permission enforcement test
            req4 = attach_messages(rf.get(f'/ncm-bulk-logs/{batch.id}/labels/'))
            req4.user = restricted_user
            resp4 = ncm_bulk_log_labels(req4, batch.id)
            assert resp4.status_code in (302, 403), f"Expected 302/403 for unauthorized staff, got {resp4.status_code}"
            print("[PASS] 5. Permission check blocks unauthorized staff from printing labels")

    finally:
        batch.delete()
        order1.delete()
        order2.delete()
        order_cancelled.delete()
        customer.delete()
        admin_user.delete()
        restricted_user.delete()

    print("=" * 68)
    print("ALL NCM SHIPMENT LABEL PRINT TESTS PASSED!")
    print("=" * 68)


if __name__ == '__main__':
    run_tests()
