"""Verify the Follow-ups page's bulk delete, export and date filter/sort.

Creates a handful of throwaway follow-ups (phone numbers all start 0099),
exercises the new endpoints against them, then hard-deletes them again in a
finally block so the dev DB is left exactly as it was found.

    python test_followup_bulk_export_sort.py
"""
import io
import json
import os
import re
import sys
from datetime import timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

# The test Client speaks to host "testserver"; this project's ALLOWED_HOSTS is
# the real deployment list, so let it through for this process only.
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.utils import timezone
from openpyxl import load_workbook

from dashboard.models import FollowUp, FollowUpLog

User = get_user_model()

PASS, FAIL = [], []
MARKER = '0099'   # phone prefix that marks a row as belonging to this script


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print(('  [PASS] ' if condition else '  [FAIL] ') + label + (f' -> {detail}' if detail else ''))


def make_follow_up(name, phone, status, lead_source, days_ago):
    """Create a follow-up backdated by `days_ago` local days."""
    fu = FollowUp.objects.create(name=name, phone=phone, status=status,
                                 lead_source=lead_source, remarks=f'{name} remark')
    when = timezone.now() - timedelta(days=days_ago)
    # created_at/updated_at are auto_now_add/auto_now, so they can only be
    # backdated with a direct UPDATE.
    FollowUp.objects.filter(pk=fu.pk).update(created_at=when, updated_at=when)
    fu.refresh_from_db()
    return fu


def rendered_ids(html):
    """Data-row ids in the order the table renders them."""
    return [int(m) for m in re.findall(r'<tr data-id="(\d+)"', html)]


def main():
    admin = User.objects.filter(is_superuser=True, is_active=True).first()
    if not admin:
        print('No superuser found - cannot exercise the views. Aborting.')
        return 1

    client = Client()
    client.force_login(admin)
    today = timezone.localdate()

    created = []
    try:
        # Deliberately out of both alphabetical and chronological order.
        created = [
            make_follow_up('Zara Test', f'{MARKER}00001', 'Pending', 'Facebook', 0),
            make_follow_up('Amit Test', f'{MARKER}00002', 'Interested', 'Instagram', 1),
            make_follow_up('Manoj Test', f'{MARKER}00003', 'Pending', 'Facebook', 5),
            make_follow_up('Bikash Test', f'{MARKER}00004', 'Interested', 'Facebook', 20),
            make_follow_up('Sita Test', f'{MARKER}00005', 'Pending', 'Instagram', 45),
        ]
        by_name = {f.name: f for f in created}
        ids = [f.id for f in created]
        print(f'Acting as: {admin.username}   Seeded {len(created)} follow-ups\n')

        base = {'q': MARKER, 'per_page': 500}

        print('1) Date range presets scope the list')
        resp = client.get('/orders/follow-ups/', dict(base, date_range='today'))
        check('HTTP 200', resp.status_code == 200, resp.status_code)
        got = rendered_ids(resp.content.decode())
        check('today -> only the row created today', got == [by_name['Zara Test'].id], got)

        resp = client.get('/orders/follow-ups/', dict(base, date_range='last_7_days'))
        got = set(rendered_ids(resp.content.decode()))
        expected = {by_name[n].id for n in ('Zara Test', 'Amit Test', 'Manoj Test')}
        check('last_7_days -> the three rows inside the window', got == expected, f'{got} vs {expected}')

        resp = client.get('/orders/follow-ups/', dict(base, date_range='last_30_days'))
        got = set(rendered_ids(resp.content.decode()))
        check('last_30_days excludes the 45-day-old row',
              by_name['Sita Test'].id not in got and by_name['Bikash Test'].id in got, got)

        print('\n2) Custom range, including a reversed one')
        frm = (today - timedelta(days=6)).isoformat()
        to = today.isoformat()
        resp = client.get('/orders/follow-ups/', dict(base, date_range='custom', date_from=frm, date_to=to))
        got = set(rendered_ids(resp.content.decode()))
        check('custom from/to matches last_7_days', got == expected, got)

        resp_rev = client.get('/orders/follow-ups/', dict(base, date_range='custom', date_from=to, date_to=frm))
        check('reversed custom range is swapped, not empty',
              set(rendered_ids(resp_rev.content.decode())) == expected)

        resp_bad = client.get('/orders/follow-ups/', dict(base, date_range='custom', date_from='not-a-date'))
        check('garbage date does not 500 (falls back to all time)',
              resp_bad.status_code == 200 and len(rendered_ids(resp_bad.content.decode())) == len(ids),
              resp_bad.status_code)

        print('\n3) Sorting')
        resp = client.get('/orders/follow-ups/', dict(base, sort='name', dir='asc'))
        got = rendered_ids(resp.content.decode())
        expected_names = ['Amit Test', 'Bikash Test', 'Manoj Test', 'Sita Test', 'Zara Test']
        check('name asc is alphabetical', got == [by_name[n].id for n in expected_names], got)

        resp = client.get('/orders/follow-ups/', dict(base, sort='name', dir='desc'))
        check('name desc is the reverse',
              rendered_ids(resp.content.decode()) == [by_name[n].id for n in reversed(expected_names)])

        resp = client.get('/orders/follow-ups/', dict(base, sort='created_at', dir='asc'))
        got = rendered_ids(resp.content.decode())
        oldest_first = [f.id for f in sorted(created, key=lambda f: f.created_at)]
        check('created_at asc is oldest first', got == oldest_first, got)

        resp = client.get('/orders/follow-ups/', dict(base, sort='not_a_column', dir='sideways'))
        check('unknown sort/dir falls back to newest first',
              rendered_ids(resp.content.decode()) == list(reversed(oldest_first)))

        print('\n3b) Sorting on the derived follow-up columns')
        # Zara gets 2 notes, Amit 1, everyone else none.
        for fu, notes in ((by_name['Zara Test'], 2), (by_name['Amit Test'], 1)):
            for i in range(notes):
                FollowUpLog.objects.create(follow_up=fu, user=admin,
                                           field_changed=f'Followup {i + 1}',
                                           old_value='-', new_value=f'note {i + 1}')

        resp = client.get('/orders/follow-ups/', dict(base, sort='followup_count', dir='desc'))
        got = rendered_ids(resp.content.decode())
        check('followup_count desc puts the busiest row first',
              got[:2] == [by_name['Zara Test'].id, by_name['Amit Test'].id], got)

        resp = client.get('/orders/follow-ups/', dict(base, sort='last_followup_at', dir='desc'))
        got = rendered_ids(resp.content.decode())
        check('last_followup_at desc leads with the two rows that have notes',
              set(got[:2]) == {by_name['Zara Test'].id, by_name['Amit Test'].id}, got)

        resp = client.get('/orders/follow-ups/', dict(base, sort='last_followup_at', dir='asc'))
        got = rendered_ids(resp.content.decode())
        check('rows with no follow-up sink to the bottom in ASC too, not to the top',
              set(got[:2]) == {by_name['Zara Test'].id, by_name['Amit Test'].id}, got)

        resp = client.get('/orders/follow-ups/', dict(base, sort='name', dir='asc'))
        check('adding notes did not duplicate rows via the annotation join',
              len(rendered_ids(resp.content.decode())) == len(ids))

        print('\n3c) Sorting composes with a custom date range')
        frm7 = (today - timedelta(days=6)).isoformat()
        resp = client.get('/orders/follow-ups/',
                          dict(base, date_range='custom', date_from=frm7, date_to=today.isoformat(),
                               sort='name', dir='asc'))
        got = rendered_ids(resp.content.decode())
        in_window = sorted(['Zara Test', 'Amit Test', 'Manoj Test'])
        check('custom range + name asc filters and orders together',
              got == [by_name[n].id for n in in_window], got)

        print('\n4) Filtered-ids endpoint (the "select all matching" toolbar link)')
        resp = client.get('/api/orders/follow-ups/ids/', dict(base, status='Pending'))
        data = json.loads(resp.content)
        got = set(data['ids'])
        expected_pending = {by_name[n].id for n in ('Zara Test', 'Manoj Test', 'Sita Test')}
        check('ids match the status filter', got == expected_pending, got)
        check('total agrees with the id count', data['total'] == len(data['ids']), data)
        check('not truncated at this size', data['truncated'] is False)

        print('\n5) Excel export')
        resp = client.get('/orders/follow-ups/export/', dict(base, date_range='last_7_days'))
        check('HTTP 200', resp.status_code == 200, resp.status_code)
        check('is an xlsx attachment', 'spreadsheetml' in resp['Content-Type'], resp['Content-Type'])
        wb = load_workbook(io.BytesIO(resp.content))
        check('expected sheets present',
              {'Follow-ups', 'Follow-up Notes', 'Status Summary', 'Report Info'} <= set(wb.sheetnames),
              wb.sheetnames)
        ws = wb['Follow-ups']
        headers = [c.value for c in ws[1]]
        phone_col = headers.index('Phone Number')
        exported = {row[phone_col].value for row in ws.iter_rows(min_row=2)}
        check('exported rows match the filtered set',
              exported == {by_name[n].phone for n in ('Zara Test', 'Amit Test', 'Manoj Test')}, exported)
        info = {r[0].value: r[1].value for r in wb['Report Info'].iter_rows(min_row=2)}
        check('Report Info records the date filter',
              'Last 7 days' in str(info.get('Date Filter', '')), info.get('Date Filter'))

        print('\n6) Explicit id selection beats the ambient filters')
        picked = [by_name['Sita Test'].id, by_name['Bikash Test'].id]
        resp = client.get('/orders/follow-ups/export/',
                          dict(base, date_range='today', ids=','.join(str(i) for i in picked)))
        ws = load_workbook(io.BytesIO(resp.content))['Follow-ups']
        exported = {row[phone_col].value for row in ws.iter_rows(min_row=2)}
        check('exactly the ticked rows, despite date_range=today',
              exported == {by_name['Sita Test'].phone, by_name['Bikash Test'].phone}, exported)
        check('filename marks it a selection',
              'followups_selection' in resp['Content-Disposition'], resp['Content-Disposition'])

        resp = client.get('/orders/follow-ups/export/', dict(base, ids='junk,,999999999'))
        ws = load_workbook(io.BytesIO(resp.content))['Follow-ups']
        check('garbage ids yield an empty sheet, not a 500', ws.max_row == 1, ws.max_row)

        print('\n7) CSV export')
        resp = client.get('/orders/follow-ups/export/', dict(base, format='csv', status='Pending'))
        check('HTTP 200 and text/csv', resp.status_code == 200 and 'text/csv' in resp['Content-Type'],
              resp['Content-Type'])
        body = resp.content.decode('utf-8-sig')
        lines = [l for l in body.splitlines() if l.strip()]
        check('header + 3 pending rows', len(lines) == 4, len(lines))
        check('all three pending phones present',
              all(by_name[n].phone in body for n in ('Zara Test', 'Manoj Test', 'Sita Test')))

        print('\n8) Bulk delete')
        victims = [by_name['Sita Test'].id, by_name['Bikash Test'].id]
        resp = client.post('/api/orders/follow-ups/bulk-delete/',
                           data=json.dumps({'ids': victims}), content_type='application/json')
        result = json.loads(resp.content)
        check('reports both deleted', result.get('deleted') == 2, result)
        check('rows are soft-deleted',
              FollowUp.objects.filter(id__in=victims, is_deleted=True).count() == 2)
        check('a Deleted log was written for each',
              FollowUpLog.objects.filter(follow_up_id__in=victims, field_changed='Deleted').count() == 2)
        check('updated_at was bumped so the sync poll notices',
              all(fu.updated_at > fu.created_at for fu in FollowUp.objects.filter(id__in=victims)))

        resp = client.post('/api/orders/follow-ups/bulk-delete/',
                           data=json.dumps({'ids': victims}), content_type='application/json')
        result = json.loads(resp.content)
        check('re-deleting the same ids is a no-op, not a double log',
              result.get('deleted') == 0 and result.get('skipped') == 2, result)
        check('still exactly one Deleted log each',
              FollowUpLog.objects.filter(follow_up_id__in=victims, field_changed='Deleted').count() == 2)

        resp = client.post('/api/orders/follow-ups/bulk-delete/',
                           data=json.dumps({'ids': []}), content_type='application/json')
        check('empty selection is rejected with 400', resp.status_code == 400, resp.status_code)

        resp = client.post('/api/orders/follow-ups/bulk-delete/',
                           data=json.dumps({'ids': ['abc', None, by_name['Manoj Test'].id]}),
                           content_type='application/json')
        result = json.loads(resp.content)
        check('non-numeric ids are skipped, valid ones still delete',
              result.get('deleted') == 1, result)

        resp = client.post('/api/orders/follow-ups/bulk-delete/',
                           data=json.dumps({'ids': list(range(1, 2100))}),
                           content_type='application/json')
        check('over-large selection is refused', resp.status_code == 400, resp.status_code)

        print('\n9) Deleted rows leave the list and the export')
        resp = client.get('/orders/follow-ups/', base)
        got = set(rendered_ids(resp.content.decode()))
        check('only the two survivors remain',
              got == {by_name['Zara Test'].id, by_name['Amit Test'].id}, got)

    finally:
        removed = FollowUp.objects.filter(phone__startswith=MARKER)
        count = removed.count()
        removed.delete()   # cascades to FollowUpLog
        print(f'\nCleaned up {count} seeded follow-up(s).')

    print(f'\n{"=" * 60}\n{len(PASS)} passed, {len(FAIL)} failed')
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
