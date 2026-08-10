"""
Verifies that the Follow-ups live-sync endpoint never delivers the same
activity notification twice.

Regression cover for the "toasts keep repeating on the Follow-ups page" bug:
the event feed used to be cursored on a wall-clock timestamp, so overlapping
polls, a browser clock running behind the server's, or a re-poll with a stale
cursor all replayed the same FollowUpLog rows as fresh toasts.

Run:  python test_follow_up_sync_duplicates.py
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings                   # noqa: E402
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.test import Client                     # noqa: E402
from django.utils import timezone                  # noqa: E402
from datetime import timedelta                     # noqa: E402

from accounts.models import CustomUser             # noqa: E402
from dashboard.models import FollowUp, FollowUpLog  # noqa: E402

SYNC_URL = '/api/orders/follow-ups/sync/'

failures = []
created = {'users': [], 'follow_ups': []}


def check(label, condition, detail=''):
    if condition:
        print(f'  PASS  {label}')
    else:
        print(f'  FAIL  {label}  {detail}')
        failures.append(label)


def make_user(username):
    user = CustomUser.objects.create_user(
        username=username,
        email=f'{username}@synctest.local',
        password='x',
        role='administrator',
    )
    created['users'].append(user.pk)
    return user


def poll(client, last_sync, last_event_id, **filters):
    params = {'last_sync': last_sync}
    if last_event_id is not None:
        params['last_event_id'] = last_event_id
    params.update(filters)
    resp = client.get(SYNC_URL, params)
    assert resp.status_code == 200, f'sync returned HTTP {resp.status_code}'
    data = resp.json()
    assert data.get('success'), f'sync failed: {data}'
    return data


def log(fu, user, field, old, new):
    return FollowUpLog.objects.create(
        follow_up=fu, user=user, field_changed=field,
        old_value=old, new_value=new,
    )


def main():
    stamp = timezone.now().strftime('%H%M%S%f')
    viewer = make_user(f'synctest_viewer_{stamp}')
    actor = make_user(f'synctest_actor_{stamp}')
    actor2 = make_user(f'synctest_actor2_{stamp}')

    fu = FollowUp.objects.create(
        name='Sync Test Lead', phone=f'98{stamp[:8]}', lead_source='Facebook',
        status='Inquery', remarks='sync test',
    )
    created['follow_ups'].append(fu.pk)

    client = Client()
    client.force_login(viewer)

    # --- 1. A freshly opened page must not replay pre-existing activity ----
    log(fu, actor, 'Status', 'New', 'Inquery')          # happened "before" load
    boot = poll(client, timezone.now().isoformat(), None)
    check('fresh page load replays no history', boot['events'] == [],
          f'got {len(boot["events"])} events')
    check('bootstrap returns an event cursor',
          isinstance(boot.get('last_event_id'), int),
          f'got {boot.get("last_event_id")!r}')

    cursor_ts, cursor_id = boot['timestamp'], boot['last_event_id']

    # --- 2. Another user's change is delivered exactly once ---------------
    log(fu, actor, 'Status', 'Inquery', 'CNR')
    log(fu, actor, 'Followup 1', '-', 'bg')

    first = poll(client, cursor_ts, cursor_id)
    check('a new change is delivered', len(first['events']) == 1,
          f'got {len(first["events"])} events')
    if first['events']:
        ev = first['events'][0]
        check('both log rows are grouped into one toast', len(ev['changes']) == 2,
              f'got {len(ev["changes"])} changes')
        check('toast is attributed to the actor', ev['user'] == actor.username,
              f'got {ev["user"]!r}')
        check('toast carries a dedupe key', bool(ev.get('event_key')))

    cursor_ts, cursor_id = first['timestamp'], first['last_event_id']

    # --- 3. Re-polling must never re-deliver it (this was the bug) --------
    for i in range(1, 4):
        again = poll(client, cursor_ts, cursor_id)
        check(f'repeat poll #{i} delivers nothing new', again['events'] == [],
              f'got {len(again["events"])} events')
        cursor_ts, cursor_id = again['timestamp'], again['last_event_id']

    # --- 4. Two overlapping polls carrying the SAME cursor ----------------
    # This is what a response slower than the 2s poll interval produced: the
    # second request went out before the first had advanced the cursor.
    log(fu, actor, 'Remarks', 'sync test', 'overlap probe')
    stale_ts, stale_id = cursor_ts, cursor_id
    poll_a = poll(client, stale_ts, stale_id)
    poll_b = poll(client, stale_ts, stale_id)
    keys_a = {e['event_key'] for e in poll_a['events']}
    keys_b = {e['event_key'] for e in poll_b['events']}
    check('overlapping polls deliver identical payloads (client dedupes by key)',
          keys_a == keys_b and len(keys_a) == 1,
          f'a={keys_a} b={keys_b}')

    cursor_ts, cursor_id = poll_a['timestamp'], poll_a['last_event_id']

    # --- 5. A browser clock running behind the server must not replay -----
    skewed_ts = (timezone.now() - timedelta(minutes=30)).isoformat()
    skewed = poll(client, skewed_ts, cursor_id)
    check('stale wall-clock cursor replays no notifications',
          skewed['events'] == [], f'got {len(skewed["events"])} events')

    cursor_id = skewed['last_event_id']
    cursor_ts = skewed['timestamp']

    # --- 6. The viewer's own edits never notify the viewer ----------------
    log(fu, viewer, 'Status', 'CNR', 'Followup need')
    own = poll(client, cursor_ts, cursor_id)
    check('own changes produce no self-notification', own['events'] == [],
          f'got {len(own["events"])} events')
    check('cursor still advances past own logs',
          own['last_event_id'] > cursor_id,
          f'{own["last_event_id"]} <= {cursor_id}')

    cursor_ts, cursor_id = own['timestamp'], own['last_event_id']

    # --- 7. Two different actors on one entry = two distinct toasts -------
    log(fu, actor, 'Status', 'Followup need', 'Cancelled')
    log(fu, actor2, 'Remarks', 'overlap probe', 'second actor')
    multi = poll(client, cursor_ts, cursor_id)
    check('each actor gets their own toast', len(multi['events']) == 2,
          f'got {len(multi["events"])} events')
    actors = {e['user'] for e in multi['events']}
    check('toasts are attributed to the right actors',
          actors == {actor.username, actor2.username}, f'got {actors}')

    cursor_ts, cursor_id = multi['timestamp'], multi['last_event_id']

    # --- 8. Filtered views still get notified, flagged as hidden ----------
    log(fu, actor, 'Remarks', 'second actor', 'filtered probe')
    filtered = poll(client, cursor_ts, cursor_id, status='Delivered')
    check('activity outside the active filter is still announced',
          len(filtered['events']) == 1, f'got {len(filtered["events"])} events')
    if filtered['events']:
        check('and is flagged as not visible under that filter',
              filtered['events'][0]['matches_filter'] is False)

    matched = poll(client, cursor_ts, cursor_id, status=fu.status)
    check('activity inside the active filter is flagged visible',
          bool(matched['events']) and matched['events'][0]['matches_filter'] is True)

    cursor_ts, cursor_id = matched['timestamp'], matched['last_event_id']

    # --- 9. A large backlog drains over several polls, losing nothing ------
    # (what a tab left open in the background used to accumulate)
    backlog = [log(fu, actor, 'Remarks', str(i), str(i + 1)) for i in range(250)]
    seen_keys, seen_changes, polls = set(), 0, 0
    while polls < 10:
        polls += 1
        batch = poll(client, cursor_ts, cursor_id)
        for e in batch['events']:
            seen_keys.add(e['event_key'])
            seen_changes += len(e['changes'])
        cursor_ts, cursor_id = batch['timestamp'], batch['last_event_id']
        if not batch['events']:
            break
    check('a 250-log backlog is delivered in full', seen_changes == len(backlog),
          f'got {seen_changes} of {len(backlog)} changes over {polls} polls')
    check('the backlog is delivered without repeats',
          seen_changes == sum(1 for _ in backlog) and len(seen_keys) >= 2,
          f'{len(seen_keys)} distinct event keys')

    drained = poll(client, cursor_ts, cursor_id)
    check('nothing is re-delivered once the backlog is drained',
          drained['events'] == [], f'got {len(drained["events"])} events')


def cleanup():
    FollowUpLog.objects.filter(follow_up_id__in=created['follow_ups']).delete()
    FollowUp.objects.filter(pk__in=created['follow_ups']).delete()
    CustomUser.objects.filter(pk__in=created['users']).delete()


if __name__ == '__main__':
    print('Follow-ups sync — duplicate notification checks\n')
    try:
        main()
    finally:
        cleanup()
        print('\n(test records cleaned up)')

    if failures:
        print(f'\n{len(failures)} FAILED: ' + ', '.join(failures))
        sys.exit(1)
    print('\nAll checks passed.')
