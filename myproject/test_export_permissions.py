"""Standalone check for the role-based export permissions.

Verifies that the three export views (Follow-ups, Logistics Orders, Bulk Logs)
are gated on their new dedicated permission and that admins bypass, mirroring
the repo's existing test_*.py convention (manual django.setup()).

    python test_export_permissions.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.test import Client
from accounts.models import CustomUser


def _mk(username, **perms):
    CustomUser.objects.filter(username=username).delete()
    u = CustomUser.objects.create_user(
        username=username, password='pw12345', email=f'{username}@t.local', role='sales'
    )
    for k, v in perms.items():
        setattr(u, k, v)
    u.is_active = True
    u.save()
    return u


CASES = [
    # url, permission field that should unlock it, extra perms needed to reach the page
    ('/orders/follow-ups/export/', 'can_export_follow_ups', {'can_access_follow_ups': True}),
    ('/logistics/orders/export/', 'can_export_logistics_orders', {'can_view_ncm_orders': True}),
    ('/logistics/bulk-logs/export/', 'can_export_logistics_bulk_logs', {'can_view_ncm_bulk_logs': True}),
]

failures = []
for url, perm, base in CASES:
    # 1. Without the export perm -> denied (redirect or 403)
    denied = _mk('exp_denied', **base)
    c = Client()
    c.force_login(denied)
    r = c.get(url)
    if r.status_code == 200 and 'attachment' in r.get('Content-Disposition', ''):
        failures.append(f'{url}: user WITHOUT {perm} was allowed to download')
    else:
        print(f'OK  denied  {url}  ({r.status_code})')

    # 2. With the export perm -> allowed
    allowed = _mk('exp_allowed', **{**base, perm: True})
    c = Client()
    c.force_login(allowed)
    r = c.get(url)
    if r.status_code != 200:
        failures.append(f'{url}: user WITH {perm} got {r.status_code}, expected 200')
    else:
        print(f'OK  allowed {url}  ({r.status_code})')

    # 3. Export perm but WITHOUT the module's view perm -> still denied
    lopsided = _mk('exp_lopsided', **{perm: True})
    c = Client()
    c.force_login(lopsided)
    r = c.get(url)
    if r.status_code == 200 and 'attachment' in r.get('Content-Disposition', ''):
        failures.append(f'{url}: user with {perm} but no view perm was allowed to download')
    else:
        print(f'OK  no-view {url}  ({r.status_code})')

# 4. Admin bypasses everything
admin = _mk('exp_admin')
admin.role = 'administrator'
admin.is_superuser = True
admin.is_staff = True
admin.save()
for url, _perm, _base in CASES:
    c = Client()
    c.force_login(admin)
    r = c.get(url)
    if r.status_code != 200:
        failures.append(f'{url}: admin got {r.status_code}, expected 200')
    else:
        print(f'OK  admin   {url}  ({r.status_code})')

CustomUser.objects.filter(username__startswith='exp_').delete()

if failures:
    print('\nFAILURES:')
    for f in failures:
        print('  -', f)
    raise SystemExit(1)
print('\nAll export-permission checks passed.')
