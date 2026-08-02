"""
Verify that get_redirect_order_details returns the old-customer snapshot for
redirected orders, regardless of which metadata key style the redirect log used.

Run:  python test_redirect_order_details.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402

# The Django test client sends Host: testserver
if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

import json  # noqa: E402
from django.template.loader import get_template  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from dashboard.models import Order, OrderActivityLog  # noqa: E402
from dashboard.views import get_redirect_order_details  # noqa: E402

failures = []


def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def call_api(user, order_id):
    request = RequestFactory().get(f'/api/orders/{order_id}/get-redirect-details/')
    request.user = user
    response = get_redirect_order_details(request, order_id)
    return response.status_code, json.loads(response.content.decode())


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found — cannot exercise the permission-gated view.")
        return 1

    redirect_logs = OrderActivityLog.objects.filter(
        action_type='redirected'
    ).select_related('order').order_by('-created_at')

    print("=" * 70)
    print("1. Templates parse")
    print("=" * 70)
    for name in ('redirect_orders.html', 'order_detail.html'):
        try:
            get_template(name)
            check(f"{name} compiles", True)
        except Exception as exc:
            check(f"{name} compiles", False, f"-> {exc}")

    print()
    print("=" * 70)
    print("2. Real redirected orders return old customer details")
    print("=" * 70)

    checked = 0
    with_metadata = 0
    for log in redirect_logs[:25]:
        order = log.order
        if order is None or order.is_deleted:
            continue
        checked += 1

        status, payload = call_api(user, order.id)
        if status != 200 or not payload.get('success'):
            check(f"order {order.order_number} -> 200/success", False,
                  f"status={status} body={str(payload)[:200]}")
            continue

        info = payload['order']['old_customer_info']
        meta = log.metadata if isinstance(log.metadata, dict) else {}
        meta_has_data = any(
            str(meta.get(k) or '').strip()
            for k in ('customer_name', 'old_customer_name',
                      'customer_phone', 'old_customer_phone',
                      'shipping_address', 'old_shipping_address',
                      'branch_city', 'old_branch_city')
        )

        if meta_has_data:
            with_metadata += 1
            populated = any(str(info.get(k) or '').strip()
                            for k in ('name', 'phone', 'branch', 'address'))
            check(
                f"order {order.order_number}: metadata present -> API returns details",
                populated,
                f"\n        metadata = {meta}\n        returned = "
                f"{ {k: info.get(k) for k in ('name', 'phone', 'email', 'branch', 'address')} }",
            )
            if populated:
                print(f"        name={info['name']!r} phone={info['phone']!r} "
                      f"branch={info['branch']!r} address={info['address']!r}")
        else:
            check(f"order {order.order_number}: empty metadata -> empty dict, no crash",
                  isinstance(info, dict))

        # History must always be present for a redirected order
        check(f"order {order.order_number}: redirect_history non-empty",
              len(payload['order']['redirect_history']) > 0)

    if checked == 0:
        print("  (no redirected orders in this database — API path not exercised)")
    else:
        print(f"\n  Checked {checked} redirected order(s); "
              f"{with_metadata} had usable metadata.")

    print()
    print("=" * 70)
    print("3. Key-style normalization (synthetic metadata, rolled back)")
    print("=" * 70)

    from django.db import transaction

    order = Order.objects.filter(is_deleted=False).order_by('-id').first()
    if not order:
        print("  (no orders in database — skipping)")
    else:
        variants = {
            'new style (customer_name/...)': {
                'customer_name': 'Raj Kumar',
                'customer_phone': '9851044348',
                'shipping_address': 'Jaranku',
                'branch_city': 'Kavresthali',
            },
            'legacy style (old_customer_name/...)': {
                'old_customer_name': 'Raj Kumar',
                'old_customer_phone': '9851044348',
                'old_shipping_address': 'Jaranku',
                'old_branch_city': 'Kavresthali',
            },
            'json string metadata': json.dumps({
                'customer_name': 'Raj Kumar',
                'customer_phone': '9851044348',
                'shipping_address': 'Jaranku',
                'branch_city': 'Kavresthali',
            }),
            'empty metadata': {},
            'null metadata': None,
        }

        for label, meta in variants.items():
            try:
                with transaction.atomic():
                    OrderActivityLog.objects.create(
                        order=order,
                        action_type='redirected',
                        user=user,
                        description='synthetic test log',
                        field_name='ncm_status',
                        old_value='',
                        new_value='redirected',
                        metadata=meta,
                    )
                    status, payload = call_api(user, order.id)
                    info = payload.get('order', {}).get('old_customer_info', {})

                    if label in ('empty metadata', 'null metadata'):
                        # Falls through to any real log on this order, or stays empty.
                        ok = status == 200 and payload.get('success') and isinstance(info, dict)
                        check(f"{label}: 200 + dict, no crash", ok, f"-> {info}")
                    else:
                        ok = (info.get('name') == 'Raj Kumar'
                              and info.get('phone') == '9851044348'
                              and info.get('branch') == 'Kavresthali'
                              and info.get('address') == 'Jaranku')
                        check(f"{label}: normalized to short keys", ok, f"-> {info}")
                        check(f"{label}: legacy aliases still emitted",
                              info.get('old_customer_name') == 'Raj Kumar', f"-> {info}")
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass  # synthetic log rolled back

        # Confirm nothing was left behind
        leftover = OrderActivityLog.objects.filter(description='synthetic test log').count()
        check("synthetic logs rolled back", leftover == 0, f"-> {leftover} left")

    print()
    print("=" * 70)
    print("4. Pages render with a real redirect log (synthetic, rolled back)")
    print("=" * 70)

    from django.test import Client
    from django.urls import reverse

    order = Order.objects.filter(is_deleted=False).order_by('-id').first()
    if not order:
        print("  (no orders in database — skipping)")
    else:
        client = Client()
        client.force_login(user)
        try:
            with transaction.atomic():
                OrderActivityLog.objects.create(
                    order=order,
                    action_type='redirected',
                    user=user,
                    description='Order redirected via NCM API. New customer: deeya shrestha.',
                    field_name='ncm_status',
                    old_value='',
                    new_value='redirected',
                    metadata={
                        'customer_name': 'raj kumar',
                        'customer_phone': '9851044348',
                        'shipping_address': 'jaranku',
                        'branch_city': 'Kavresthali',
                    },
                )

                resp = client.get(reverse('order_detail', args=[order.id]))
                body = resp.content.decode('utf-8', 'replace')
                check("order_detail page returns 200", resp.status_code == 200,
                      f"-> {resp.status_code}")
                check("order_detail shows the old-customer box",
                      'Old Customer Details (Before Redirection)' in body)
                for value in ('raj kumar', '9851044348', 'jaranku', 'Kavresthali'):
                    check(f"order_detail shows {value!r}", value in body)

                resp = client.get(reverse('redirect_orders_list'))
                check("redirect_orders_list page returns 200", resp.status_code == 200,
                      f"-> {resp.status_code}")

                api_resp = client.get(f'/api/orders/{order.id}/get-redirect-details/')
                check("modal API returns 200 via URL routing", api_resp.status_code == 200,
                      f"-> {api_resp.status_code}")
                api_info = json.loads(api_resp.content.decode())['order']['old_customer_info']
                check("modal API old_customer_info populated",
                      api_info.get('name') == 'raj kumar' and api_info.get('phone') == '9851044348',
                      f"-> {api_info}")

                raise RuntimeError('rollback')
        except RuntimeError:
            pass

        leftover = OrderActivityLog.objects.filter(
            description__startswith='Order redirected via NCM API. New customer: deeya'
        ).count()
        check("page-render synthetic log rolled back", leftover == 0, f"-> {leftover} left")

    print()
    print("=" * 70)
    print("5. Edge cases")
    print("=" * 70)

    client = Client()
    client.force_login(user)

    # 5a. Unknown / deleted order -> clean 404 JSON, not a 500
    missing_id = (Order.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 10_000
    resp = client.get(f'/api/orders/{missing_id}/get-redirect-details/')
    check("unknown order -> 404", resp.status_code == 404, f"-> {resp.status_code}")
    try:
        body = json.loads(resp.content.decode())
        check("unknown order -> JSON error body", body.get('success') is False, f"-> {body}")
    except ValueError:
        check("unknown order -> JSON error body", False, "-> non-JSON response")

    order = Order.objects.filter(is_deleted=False).order_by('-id').first()
    if not order:
        print("  (no orders in database — skipping remaining edge cases)")
    else:
        # 5b. Order with no redirect logs at all
        had_logs = OrderActivityLog.objects.filter(order=order, action_type='redirected').exists()
        if had_logs:
            print("  (order already has redirect logs — skipping 'no redirect log' case)")
        else:
            status, payload = call_api(user, order.id)
            ok = (status == 200 and payload.get('success')
                  and payload['order']['old_customer_info'] == {}
                  and payload['order']['redirect_history'] == [])
            check("order with no redirect logs -> empty info, empty history, 200", ok,
                  f"-> {status} {str(payload.get('order', {}).get('old_customer_info'))[:120]}")

        # 5c. Newest redirect log has empty metadata, an older one has the details.
        #     The newest-log-only logic would show nothing here.
        try:
            with transaction.atomic():
                OrderActivityLog.objects.create(
                    order=order, action_type='redirected', user=user,
                    description='older redirect WITH details', field_name='ncm_status',
                    old_value='', new_value='redirected',
                    metadata={'customer_name': 'raj kumar', 'customer_phone': '9851044348',
                              'shipping_address': 'jaranku', 'branch_city': 'Kavresthali'},
                )
                OrderActivityLog.objects.create(
                    order=order, action_type='redirected', user=user,
                    description='newer redirect WITHOUT details', field_name='ncm_status',
                    old_value='', new_value='redirected', metadata={},
                )

                status, payload = call_api(user, order.id)
                info = payload['order']['old_customer_info']
                history = payload['order']['redirect_history']
                check("newest log empty -> falls back to older log's details",
                      info.get('name') == 'raj kumar', f"-> {info}")
                check("history has one entry per redirect log", len(history) >= 2,
                      f"-> {len(history)}")
                check("history entries carry their own old_customer snapshot",
                      all('old_customer' in h for h in history),
                      f"-> {[sorted(h) for h in history[:2]]}")
                check("history is newest-first",
                      history[0]['redirect_reason'].startswith('newer'),
                      f"-> {history[0]['redirect_reason']}")
                check("history timestamps are pre-formatted for display",
                      bool(history[0].get('redirect_timestamp_display')),
                      f"-> {history[0].get('redirect_timestamp_display')}")

                # The list page must agree with the modal, not show dashes
                resp = client.get(reverse('redirect_orders_list') + '?search=' + (order.order_number or ''))
                lbody = resp.content.decode('utf-8', 'replace')
                check("redirect_orders_list returns 200", resp.status_code == 200,
                      f"-> {resp.status_code}")
                check("redirect_orders_list shows the fallback old customer",
                      'raj kumar' in lbody,
                      "-> old customer missing from list page HTML")

                raise RuntimeError('rollback')
        except RuntimeError:
            pass

        leftover = OrderActivityLog.objects.filter(
            description__in=['older redirect WITH details', 'newer redirect WITHOUT details']
        ).count()
        check("edge-case synthetic logs rolled back", leftover == 0, f"-> {leftover} left")

        # 5d. A field_name with no old/new values must not render a bare
        #     "Field: ncm_status" chip with nothing under it.
        baseline = client.get(reverse('order_detail', args=[order.id])) \
            .content.decode('utf-8', 'replace').count('activity-field-change')
        try:
            with transaction.atomic():
                OrderActivityLog.objects.create(
                    order=order, action_type='updated', user=user,
                    description='valueless field change', field_name='ncm_status',
                    old_value='', new_value='', metadata={},
                )
                body = client.get(reverse('order_detail', args=[order.id])) \
                    .content.decode('utf-8', 'replace')
                check("valueless field change renders no field chip",
                      body.count('activity-field-change') == baseline,
                      f"-> {body.count('activity-field-change')} vs baseline {baseline}")
                check("the log itself still appears", 'valueless field change' in body)
                raise RuntimeError('rollback')
        except RuntimeError:
            pass

        # 5e. A one-sided change (only new_value) must still be shown
        try:
            with transaction.atomic():
                OrderActivityLog.objects.create(
                    order=order, action_type='updated', user=user,
                    description='one sided change', field_name='ncm_status',
                    old_value='', new_value='ONLY_NEW_VALUE', metadata={},
                )
                body = client.get(reverse('order_detail', args=[order.id])) \
                    .content.decode('utf-8', 'replace')
                check("one-sided change is rendered", 'ONLY_NEW_VALUE' in body)
                raise RuntimeError('rollback')
        except RuntimeError:
            pass

        leftover = OrderActivityLog.objects.filter(
            description__in=['valueless field change', 'one sided change']
        ).count()
        check("field-chip synthetic logs rolled back", leftover == 0, f"-> {leftover} left")

    print()
    print("=" * 70)
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
