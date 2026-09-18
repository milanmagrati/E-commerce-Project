"""
Regression check: ncm_branches_json must not drop NCM branches whose
`address` field comes back as null (23 such branches as of Aug 2026,
including FALASHAIN / FALA1 in Bajura).

Run: python test_ncm_branches_null_address.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.test import RequestFactory
from django.conf import settings
from accounts.models import CustomUser
from dashboard.views import ncm_branches_json
import json


def main():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if user is None:
        user = CustomUser.objects.filter(role='administrator').first()
    assert user, "need an admin/superuser to call the view"

    rf = RequestFactory()
    request = rf.get('/dashboard/api/ncm-branches/', HTTP_ACCEPT='application/json')
    request.user = user

    response = ncm_branches_json(request)
    payload = json.loads(response.content)
    branches = payload.get('branches', [])
    names = {b['name'].upper() for b in branches}

    print(f"branches returned: {len(branches)}")
    print(f"FALASHAIN present: {'FALASHAIN' in names}")

    assert response.status_code == 200, payload
    assert 'FALASHAIN' in names, "FALASHAIN (null-address branch) was dropped"
    # We should now be within a couple of rows of NCM's own total (631),
    # not ~608. Allow a small margin for genuinely code/name-less rows.
    assert len(branches) >= 628, f"still dropping branches: only {len(branches)}"
    print("PASS")


if __name__ == '__main__':
    main()
