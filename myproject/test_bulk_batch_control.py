"""
Verifies the Bulk Logs terminate/resume controls (dashboard/bulk_batch.py).

Follows this repo's convention for verification: a standalone script that sets
Django up by hand and exercises the real models against the real DB. Everything
it creates is namespaced with a TEST- batch prefix and torn down at the end.

    python test_bulk_batch_control.py

The provider API is never called: the send helpers are monkeypatched, so this
checks the control logic (claim, cancel, reconcile, resume bookkeeping) rather
than NCM connectivity.
"""

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from dashboard import bulk_batch
from dashboard.models import Order
from ncm.models import NCMBulkLog, NCMBulkLogOrder, NCMBulkLogDetail

BATCH_PREFIX = 'TEST-BBC-'
failures = []
created_batches = []


def check(label, condition, detail=''):
    if condition:
        print(f'  PASS  {label}')
    else:
        print(f'  FAIL  {label}' + (f'  ({detail})' if detail else ''))
        failures.append(label)


def make_batch(orders, status='processing', heartbeat=None, snapshot=True, options=None):
    batch = NCMBulkLog.objects.create(
        batch_number=f'{BATCH_PREFIX}{timezone.now().strftime("%H%M%S%f")}',
        total_orders=len(orders),
        status=status,
        from_branch='TINKUNE',
        created_by=None,
        selected_order_ids=[o.id for o in orders] if snapshot else None,
        send_options=options,
        worker_heartbeat_at=heartbeat,
    )
    created_batches.append(batch)
    return batch


def cleanup():
    ids = [b.id for b in created_batches]
    NCMBulkLogDetail.objects.filter(batch_id__in=ids).delete()
    NCMBulkLogOrder.objects.filter(batch_id__in=ids).delete()
    NCMBulkLog.objects.filter(id__in=ids).delete()


def main():
    user = get_user_model().objects.order_by('id').first()
    orders = list(Order.objects.filter(is_deleted=False).order_by('id')[:3])
    if len(orders) < 3:
        print('SKIP: need at least 3 non-deleted orders in the DB to run this.')
        return 0

    adapter = bulk_batch.get_adapter('ncm')

    # ---------------------------------------------------------------- stalled
    print('\n1. A processing batch with no heartbeat reads as stalled')
    stale = timezone.now() - timedelta(seconds=bulk_batch.STALE_HEARTBEAT_SECONDS + 60)
    batch = make_batch(orders, heartbeat=None)
    check('is_stalled', bulk_batch.is_stalled(batch))
    check('not is_worker_alive', not bulk_batch.is_worker_alive(batch))

    batch2 = make_batch(orders, heartbeat=stale)
    check('stale heartbeat still counts as stalled', bulk_batch.is_stalled(batch2))

    batch3 = make_batch(orders, heartbeat=timezone.now())
    check('fresh heartbeat reads as alive', bulk_batch.is_worker_alive(batch3))
    check('fresh heartbeat is not stalled', not bulk_batch.is_stalled(batch3))

    # ------------------------------------------------------------- reconcile
    print('\n2. Counts are rebuilt from the child rows a dead run left behind')
    batch = make_batch(orders)
    NCMBulkLogOrder.objects.create(batch=batch, order=orders[0],
                                   order_number=orders[0].order_number or 'A', status='success')
    NCMBulkLogOrder.objects.create(batch=batch, order=orders[1],
                                   order_number=orders[1].order_number or 'B', status='failed')
    check('batch starts with zeroed counters', batch.success_count == 0 and batch.failed_count == 0)
    bulk_batch.reconcile_counts(adapter, batch)
    batch.refresh_from_db()
    check('success_count recovered', batch.success_count == 1, f'got {batch.success_count}')
    check('failed_count recovered', batch.failed_count == 1, f'got {batch.failed_count}')

    # --------------------------------------------------------------- pending
    print('\n3. Pending work excludes what already went out')
    pending = bulk_batch.pending_order_ids(adapter, batch)
    check('success is not resent', orders[0].id not in pending)
    check('failure is retried', orders[1].id in pending)
    check('never-reached order is included', orders[2].id in pending)
    check('pending count', len(pending) == 2, f'got {pending}')

    print('\n3b. An order already at the courier is never resent, whatever its row says')
    dispatched = make_batch(orders)
    # 'skipped' means two different things: already at the courier, or (for PND)
    # missing customer details. Only the consignment number tells them apart.
    NCMBulkLogOrder.objects.create(batch=dispatched, order=orders[0],
                                   order_number=orders[0].order_number or 'A',
                                   status='skipped', ncm_order_id=555001)
    NCMBulkLogOrder.objects.create(batch=dispatched, order=orders[1],
                                   order_number=orders[1].order_number or 'B',
                                   status='skipped', ncm_order_id=None)
    p = bulk_batch.pending_order_ids(adapter, dispatched)
    check('skip WITH a consignment number is done', orders[0].id not in p)
    check('skip WITHOUT one is retryable', orders[1].id in p)

    print('\n4. A pre-snapshot batch can only name the orders it recorded')
    legacy = make_batch(orders, snapshot=False)
    NCMBulkLogOrder.objects.create(batch=legacy, order=orders[1],
                                   order_number=orders[1].order_number or 'B', status='failed')
    legacy_pending = bulk_batch.pending_order_ids(adapter, legacy)
    check('recorded failure is resumable', legacy_pending == [orders[1].id], f'got {legacy_pending}')
    state = bulk_batch.describe(adapter, legacy)
    check('unreachable orders are reported', state['unrecoverable_count'] == 2,
          f'got {state["unrecoverable_count"]}')

    # ------------------------------------------------------------- terminate
    print('\n5. Terminating a stalled batch closes it out with true counts')
    batch = make_batch(orders)
    NCMBulkLogOrder.objects.create(batch=batch, order=orders[0],
                                   order_number=orders[0].order_number or 'A', status='success')
    message, state = bulk_batch.terminate('ncm', batch.id, user)
    batch.refresh_from_db()
    check('status is cancelled', batch.status == 'cancelled', f'got {batch.status}')
    check('completed_at stamped', batch.completed_at is not None)
    check('counts reconciled', batch.success_count == 1)
    check('heartbeat cleared', batch.worker_heartbeat_at is None)
    check('message mentions unattempted orders', 'never attempted' in message, message)

    print('\n6. Terminating twice is refused, not silently repeated')
    try:
        bulk_batch.terminate('ncm', batch.id, user)
        check('second terminate raises', False)
    except bulk_batch.BatchError as e:
        check('second terminate raises', True)
        check('and says why', 'cancelled' in str(e).lower(), str(e))

    print('\n7. Terminating a LIVE batch only requests a stop')
    live = make_batch(orders, heartbeat=timezone.now())
    message, state = bulk_batch.terminate('ncm', live.id, user)
    live.refresh_from_db()
    check('still processing', live.status == 'processing', f'got {live.status}')
    check('cancel flag set', live.cancel_requested)
    check('message explains the delay', 'current order' in message, message)

    print('\n8. A running loop sees the cancel flag on its next order')
    hb = bulk_batch.BatchHeartbeat(live)
    check('should_stop reports cancelled', hb.should_stop() == 'cancelled')
    hb.release()
    live.refresh_from_db()
    check('release clears the flag', not live.cancel_requested)
    check('release clears the heartbeat', live.worker_heartbeat_at is None)

    # ----------------------------------------------------------------- claim
    print('\n9. Only one caller can claim a batch')
    contested = make_batch(orders)
    first = bulk_batch._claim(adapter, contested)
    second = bulk_batch._claim(adapter, contested)
    check('first claim wins', first)
    check('second claim loses', not second)
    contested.refresh_from_db()
    check('claim stamps a heartbeat', contested.worker_heartbeat_at is not None)
    NCMBulkLog.objects.filter(pk=contested.pk).update(worker_heartbeat_at=None)

    # ---------------------------------------------------------------- resume
    print('\n10. Resume sends only the outstanding orders')
    calls = []

    def fake_send(shim, order, batch):
        calls.append(order.id)
        return {'status': 'success', 'message': 'stubbed'}

    real_get_adapter = bulk_batch.get_adapter

    def stubbed_get_adapter(provider):
        a = real_get_adapter(provider)
        if provider == 'ncm':
            a.send = fake_send
        return a

    bulk_batch.get_adapter = stubbed_get_adapter
    try:
        resumable = make_batch(orders)
        NCMBulkLogOrder.objects.create(batch=resumable, order=orders[0],
                                       order_number=orders[0].order_number or 'A', status='success')
        NCMBulkLogOrder.objects.create(batch=resumable, order=orders[1],
                                       order_number=orders[1].order_number or 'B', status='failed')
        # Run the body inline instead of on a thread, so the assertions below
        # are not racing it.
        pending = bulk_batch.pending_order_ids(stubbed_get_adapter('ncm'), resumable)
        bulk_batch._claim(stubbed_get_adapter('ncm'), resumable)
        bulk_batch._run_resume('ncm', resumable.id, pending,
                               user.id if user else None)
        resumable.refresh_from_db()

        check('already-sent order was not resent', orders[0].id not in calls)
        check('failed order was retried', orders[1].id in calls)
        check('unreached order was sent', orders[2].id in calls)
        check('all three orders now have exactly one row each',
              NCMBulkLogOrder.objects.filter(batch=resumable).count() == 3,
              f'got {NCMBulkLogOrder.objects.filter(batch=resumable).count()}')
        check('retried row was overwritten, not duplicated',
              NCMBulkLogOrder.objects.filter(batch=resumable, order=orders[1]).count() == 1)
        check('counts are final', resumable.success_count == 3,
              f'got {resumable.success_count}')
        check('status is completed', resumable.status == 'completed',
              f'got {resumable.status}')
        check('heartbeat released', resumable.worker_heartbeat_at is None)

        print('\n11. Resume refuses a batch with nothing recoverable')
        hopeless = make_batch(orders, snapshot=False)
        try:
            bulk_batch.resume('ncm', hopeless.id, user)
            check('raises BatchError', False)
        except bulk_batch.BatchError as e:
            check('raises BatchError', True)
            check('explains the loss', 'cannot be resumed' in str(e), str(e))

        print('\n12. Resume refuses a batch someone else is working')
        busy = make_batch(orders, heartbeat=timezone.now())
        try:
            bulk_batch.resume('ncm', busy.id, user)
            check('raises BatchError', False)
        except bulk_batch.BatchError as e:
            check('raises BatchError', True)
            check('says it is already running', 'already being processed' in str(e), str(e))
    finally:
        bulk_batch.get_adapter = real_get_adapter

    print('\n13. Resume replays the batch\'s original send options')
    seen = {}

    def recording_send(shim, order, batch, **kwargs):
        seen['weight'] = kwargs.get('default_weight')
        seen['api_config_id'] = kwargs.get('api_config_id')
        return {'status': 'success', 'message': 'stubbed'}

    import dashboard.views as dv
    real_ncm_send = dv.send_single_order_to_ncm
    dv.send_single_order_to_ncm = lambda shim, order, **kw: recording_send(shim, order, None, **kw)
    try:
        opts = {'api_config_id': '7', 'default_weight': 2.5, 'auto_set_logistics': True}
        with_opts = make_batch(orders[:1], options=opts)
        bulk_batch._claim(adapter, with_opts)
        bulk_batch._run_resume('ncm', with_opts.id, [orders[0].id], user.id if user else None)
        check('weight came from the batch, not the default',
              seen.get('weight') == 2.5, f'got {seen.get("weight")}')
        check('api account came from the batch',
              seen.get('api_config_id') == '7', f'got {seen.get("api_config_id")}')

        print('\n14. A batch with no stored options falls back safely')
        seen.clear()
        legacy_opts = make_batch(orders[:1], options=None)
        bulk_batch._claim(adapter, legacy_opts)
        bulk_batch._run_resume('ncm', legacy_opts.id, [orders[0].id], user.id if user else None)
        check('weight defaults to 1.0', seen.get('weight') == 1.0, f'got {seen.get("weight")}')
        check('api account defaults to none', seen.get('api_config_id') is None,
              f'got {seen.get("api_config_id")}')
    finally:
        dv.send_single_order_to_ncm = real_ncm_send

    print('\n15. annotate_controls decides the buttons for a page of rows')
    page = [
        make_batch(orders),                                    # stalled
        make_batch(orders, status='completed'),
        make_batch(orders, heartbeat=timezone.now()),          # live
    ]
    for log in page:
        log.log_provider = 'ncm'
    bulk_batch.annotate_controls(page)
    check('stalled row offers Terminate', page[0].control['can_terminate'])
    check('stalled row offers Resume', page[0].control['can_resume'])
    check('completed row offers neither',
          not page[1].control['can_terminate'] and not page[1].control['can_resume'])
    check('live row offers Terminate', page[2].control['can_terminate'])
    check('live row does not offer Resume', not page[2].control['can_resume'])

    print('\n16. The Pick and Drop adapter behaves the same way')
    # PND is a near-copy of NCM throughout this codebase, and its bulk log is a
    # separate model - so the shared control code has to be exercised against
    # it too, not assumed equivalent. Its consignment id is a CharField (NCM's
    # is an integer), which is exactly where a shared code path can go wrong.
    from pick_and_drop.models import PNDBulkLog, PNDBulkLogOrder, PNDBulkLogDetail

    pnd = bulk_batch.get_adapter('pnd')
    check('pnd adapter loads', pnd.key == 'pnd')
    check('pnd consignment field', pnd.provider_id_field == 'pnd_order_id')
    check('pnd logistics value', pnd.logistics_value == 'pick_and_drop')

    pnd_batch = PNDBulkLog.objects.create(
        batch_number=f'{BATCH_PREFIX}PND-{timezone.now().strftime("%H%M%S%f")}',
        total_orders=len(orders), status='processing',
        selected_order_ids=[o.id for o in orders],
    )
    try:
        PNDBulkLogOrder.objects.create(batch=pnd_batch, order=orders[0],
                                       order_number='A', status='skipped',
                                       pnd_order_id='PND-XYZ')
        PNDBulkLogOrder.objects.create(batch=pnd_batch, order=orders[1],
                                       order_number='B', status='skipped',
                                       pnd_order_id='')   # empty, not NULL
        p = bulk_batch.pending_order_ids(pnd, pnd_batch)
        check('pnd skip with a consignment id is done', orders[0].id not in p, str(p))
        check('pnd skip with an empty id is retryable', orders[1].id in p, str(p))

        bulk_batch.reconcile_counts(pnd, pnd_batch)
        pnd_batch.refresh_from_db()
        check('pnd counts reconciled', pnd_batch.skipped_count == 2,
              f'got {pnd_batch.skipped_count}')

        _, pnd_state = bulk_batch.terminate('pnd', pnd_batch.id, user)
        pnd_batch.refresh_from_db()
        check('pnd terminate works', pnd_batch.status == 'cancelled', pnd_batch.status)
        check('pnd state carries its provider', pnd_state['provider'] == 'pnd')
    finally:
        PNDBulkLogDetail.objects.filter(batch=pnd_batch).delete()
        PNDBulkLogOrder.objects.filter(batch=pnd_batch).delete()
        pnd_batch.delete()

    print('\n' + '=' * 60)
    if failures:
        print(f'{len(failures)} FAILURE(S):')
        for f in failures:
            print(f'  - {f}')
        return 1
    print('All bulk batch control checks passed.')
    return 0


if __name__ == '__main__':
    try:
        code = main()
    finally:
        cleanup()
    sys.exit(code)
