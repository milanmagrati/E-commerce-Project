"""Verify bonuses actually add to a payslip's total, not just display as a row.

Bug: the payslip print/download view re-queries approved/paid Bonus rows for
the employee+month and lists them under Earnings, but `total_earnings` /
`net_salary` were computed from `slip.gross_salary` / `bd['total_earnings']`
-- neither of which was ever updated when a bonus got approved *after* the
payroll run had already generated the payslip. Bonus rows showed up on the
slip but the totals silently ignored them.

Fix: `_sync_bonus_to_payslip()` folds the current approved/paid bonus total
for an employee/month into that payslip's stored gross_salary/net_salary
(tracking how much was already included via `salary_structure.bonus_total_
included`, so it's idempotent). It's called from `bonus_update_status` (so
approving a bonus updates any existing payslip immediately) and from
`payslip_download` itself (so an already-stale payslip self-heals the next
time it's viewed/printed).

This asserts:
  * approving a bonus (via the real view, not just the helper) updates the
    payslip's gross_salary/net_salary by exactly the bonus amount
  * a second bonus approved later stacks on top (no double-counting, no
    clobbering the first)
  * rejecting an approved bonus removes it from the total again
  * opening payslip_download recomputes the totals to match what it displays
  * a finalized payslip is left untouched

All fixtures are created inside a transaction that is always rolled back, so
this leaves the database untouched.

Run: python test_bonus_payslip_sync.py
"""
import os
import sys
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.db import transaction  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from accounts.models import CustomUser  # noqa: E402
from hrm.models import (  # noqa: E402
    AdvancePayment, Bonus, Employee, Payslip, PayrollRun, PayslipAdjustment,
)
from hrm.views import (  # noqa: E402
    _sync_bonus_to_payslip, bonus_create, bonus_update_status, payslip_adjust, payslip_download,
)

FAILURES = []


class Rollback(Exception):
    """Raised at the end of the fixture block to undo every write."""


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, expected {want!r}")
    if not ok:
        FAILURES.append(f"{label}: got {got!r}, expected {want!r}")


def admin_user():
    user = CustomUser.objects.filter(is_superuser=True).first()
    if user is None:
        print('!! No superuser found — cannot exercise the @login_required views.')
        sys.exit(1)
    return user


def make_non_admin_user():
    return CustomUser.objects.create(
        username='__sync_test_non_admin__', email='sync_test_non_admin@example.invalid',
        role='sales', is_superuser=False, is_staff=False,
    )


def approve_bonus(bonus, user):
    req = RequestFactory().post(
        f'/hrm/bonuses/{bonus.pk}/status/', {'status': 'approved'})
    req.user = user
    resp = bonus_update_status(req, bonus.pk)
    assert resp.status_code == 200, resp.status_code


def reject_bonus(bonus, user):
    req = RequestFactory().post(
        f'/hrm/bonuses/{bonus.pk}/status/', {'status': 'rejected'})
    req.user = user
    resp = bonus_update_status(req, bonus.pk)
    assert resp.status_code == 200, resp.status_code


def render_payslip(slip, user):
    req = RequestFactory().get(f'/hrm/payslips/{slip.pk}/download/')
    req.user = user
    resp = payslip_download(req, slip.pk)
    assert resp.status_code == 200, resp.status_code
    return resp


def add_adjustment(slip, user, adjustment_type, amount):
    req = RequestFactory().post(f'/hrm/payslips/{slip.pk}/adjust/', {
        'action': 'add_adjustment', 'adjustment_type': adjustment_type,
        'category': 'other', 'description': 'test adj', 'amount': str(amount),
        'reason': 'test',
    })
    req.user = user
    resp = payslip_adjust(req, slip.pk)
    assert resp.status_code == 200, resp.status_code
    import json
    data = json.loads(resp.content)
    assert data['success'], data
    return PayslipAdjustment.objects.filter(payslip=slip).order_by('-id').first()


def remove_adjustment(slip, user, adjustment_id):
    req = RequestFactory().post(f'/hrm/payslips/{slip.pk}/adjust/', {
        'action': 'remove_adjustment', 'adjustment_id': str(adjustment_id),
    })
    req.user = user
    resp = payslip_adjust(req, slip.pk)
    assert resp.status_code == 200, resp.status_code


def update_bonus(bonus, user, **fields):
    from hrm.views import bonus_update
    data = {
        'bonus_type': fields.get('bonus_type', bonus.bonus_type),
        'amount': str(fields.get('amount', bonus.amount)),
        'month': str(fields.get('month', bonus.month)),
        'year': str(fields.get('year', bonus.year)),
        'remarks': fields.get('remarks', bonus.remarks or ''),
    }
    req = RequestFactory().post(f'/hrm/bonuses/{bonus.pk}/update/', data)
    req.user = user
    resp = bonus_update(req, bonus.pk)
    assert resp.status_code == 200, resp.status_code
    import json
    return json.loads(resp.content)


def create_bonus_via_view(user, employee, **overrides):
    data = {'employee': str(employee.id), 'amount': '100', 'month': '5', 'year': '2031', 'bonus_type': 'performance'}
    data.update(overrides)
    req = RequestFactory().post('/hrm/bonuses/create/', data)
    req.user = user
    resp = bonus_create(req)
    assert resp.status_code == 200, resp.status_code
    import json
    return json.loads(resp.content)


def delete_bonus(bonus, user):
    from hrm.views import bonus_delete
    req = RequestFactory().post(f'/hrm/bonuses/{bonus.pk}/delete/')
    req.user = user
    resp = bonus_delete(req, bonus.pk)
    assert resp.status_code == 200, resp.status_code
    import json
    return json.loads(resp.content)


def main():
    employee = Employee.objects.filter(employee_status='active').order_by('id').first()
    if employee is None:
        print('!! No active employee in the DB.')
        return 1
    user = admin_user()

    print(f"Employee : {employee.full_name} ({employee.employee_id})")

    MONTH, YEAR = 3, 2031  # obscure test period, unlikely to collide with real data
    BASE_GROSS = Decimal('10000.00')

    try:
        with transaction.atomic():
            run = PayrollRun.objects.create(
                title='Sync-test run', frequency='monthly',
                month=MONTH, year=YEAR, status='completed',
            )
            slip = Payslip.objects.create(
                payroll_run=run, employee=employee,
                basic_salary=BASE_GROSS, gross_salary=BASE_GROSS,
                total_deductions=Decimal('0'), advance_deduction=Decimal('0'),
                absent_deduction=Decimal('0'), net_salary=BASE_GROSS,
                salary_structure={
                    'earnings_list': [{'name': 'Basic Salary', 'amount': float(BASE_GROSS)}],
                    'deductions_list': [],
                },
                status='generated',
            )

            bonus1 = Bonus.objects.create(
                employee=employee, bonus_type='performance', amount=Decimal('2499.00'),
                month=MONTH, year=YEAR, status='pending', created_by=user,
            )
            bonus2 = Bonus.objects.create(
                employee=employee, bonus_type='performance', amount=Decimal('1950.00'),
                month=MONTH, year=YEAR, status='pending', created_by=user,
            )

            print("\nSCENARIO: bonus still pending — payslip must not change")
            _sync_bonus_to_payslip(employee, MONTH, YEAR)
            slip.refresh_from_db()
            check('gross unchanged while bonus pending', slip.gross_salary, BASE_GROSS)

            print("\nSCENARIO: approve bonus #1 via the real view")
            approve_bonus(bonus1, user)
            slip.refresh_from_db()
            check('gross includes bonus #1', slip.gross_salary, BASE_GROSS + bonus1.amount)
            check('net includes bonus #1', slip.net_salary, BASE_GROSS + bonus1.amount)

            print("\nSCENARIO: approve bonus #2 — stacks, does not clobber bonus #1")
            approve_bonus(bonus2, user)
            slip.refresh_from_db()
            expect_both = BASE_GROSS + bonus1.amount + bonus2.amount
            check('gross includes both bonuses', slip.gross_salary, expect_both)
            check('net includes both bonuses', slip.net_salary, expect_both)

            print("\nSCENARIO: reject bonus #1 — total drops back down")
            reject_bonus(bonus1, user)
            slip.refresh_from_db()
            check('gross drops after rejection', slip.gross_salary, BASE_GROSS + bonus2.amount)

            print("\nSCENARIO: printing/downloading the payslip shows a consistent total")
            # re-approve bonus #1 but WITHOUT going through the sync helper this
            # time -- mimics a bonus approved through some other path, or data
            # that predates this fix -- so the payslip is stale on disk again.
            Bonus.objects.filter(pk=bonus1.pk).update(status='approved')
            stale_gross_before_view = Payslip.objects.get(pk=slip.pk).gross_salary
            check('payslip is stale before viewing it',
                  stale_gross_before_view, BASE_GROSS + bonus2.amount)

            render_payslip(slip, user)
            slip.refresh_from_db()
            check('viewing the payslip self-heals the stale total',
                  slip.gross_salary, expect_both)

            print("\nSCENARIO: manual adjustments and bonus sync must compose, not clobber")
            # Baseline coming in: both bonus #1 and #2 are baked in (expect_both).
            adj1 = add_adjustment(slip, user, 'earning', '500.00')
            slip.refresh_from_db()
            after_adj1 = expect_both + Decimal('500.00')
            check('gross after first adjustment', slip.gross_salary, after_adj1)

            # A NEW bonus approved *between* two adjustment edits used to get wiped
            # out by the next adjustment recompute (it reset from a frozen base).
            bonus3 = Bonus.objects.create(
                employee=employee, bonus_type='festival', amount=Decimal('700.00'),
                month=MONTH, year=YEAR, status='pending', created_by=user,
            )
            approve_bonus(bonus3, user)
            slip.refresh_from_db()
            after_bonus3 = after_adj1 + bonus3.amount
            check('gross after bonus approved mid-adjustment-flow', slip.gross_salary, after_bonus3)

            adj2 = add_adjustment(slip, user, 'earning', '300.00')
            slip.refresh_from_db()
            after_adj2 = after_bonus3 + Decimal('300.00')
            check('second adjustment adds on top, does not drop the bonus',
                  slip.gross_salary, after_adj2)

            remove_adjustment(slip, user, adj1.pk)
            slip.refresh_from_db()
            after_remove = after_adj2 - Decimal('500.00')
            check('removing the first adjustment only removes its own amount',
                  slip.gross_salary, after_remove)

            print("\nSCENARIO: finalized payslips are left untouched")
            reject_bonus(bonus1, user)
            slip.refresh_from_db()
            after_reject_bonus1 = after_remove - bonus1.amount
            check('gross drops after rejecting bonus #1 again', slip.gross_salary, after_reject_bonus1)

            slip.is_finalized = True
            slip.save(update_fields=['is_finalized'])
            Bonus.objects.filter(pk=bonus1.pk).update(status='approved')
            _sync_bonus_to_payslip(employee, MONTH, YEAR)
            slip.refresh_from_db()
            check('finalized payslip gross is untouched',
                  slip.gross_salary, after_reject_bonus1)

            print("\nSCENARIO: a live-advance auto-sync must not wipe out bonus/adjustments")
            # Separate payslip/period so it doesn't interact with the fixtures above.
            MONTH2, YEAR2 = 4, 2031
            run2 = PayrollRun.objects.create(
                title='Sync-test run 2', frequency='monthly',
                month=MONTH2, year=YEAR2, status='completed',
            )
            slip2 = Payslip.objects.create(
                payroll_run=run2, employee=employee,
                basic_salary=BASE_GROSS, gross_salary=BASE_GROSS,
                total_deductions=Decimal('0'), advance_deduction=Decimal('0'),
                absent_deduction=Decimal('0'), net_salary=BASE_GROSS,
                salary_structure={
                    'earnings_list': [{'name': 'Basic Salary', 'amount': float(BASE_GROSS)}],
                    'deductions_list': [],
                },
                status='generated',
            )
            bonus4 = Bonus.objects.create(
                employee=employee, bonus_type='other', amount=Decimal('1000.00'),
                month=MONTH2, year=YEAR2, status='pending', created_by=user,
            )
            approve_bonus(bonus4, user)
            slip2.refresh_from_db()
            check('slip2 gross includes bonus4 before advance sync',
                  slip2.gross_salary, BASE_GROSS + bonus4.amount)

            AdvancePayment.objects.create(
                employee=employee, amount=Decimal('2000.00'), reason='test advance',
                repayment_mode='installments', installment_amount=Decimal('500.00'),
                status='disbursed',
            )

            render_payslip(slip2, user)
            slip2.refresh_from_db()
            expect_net2 = BASE_GROSS + bonus4.amount - Decimal('500.00')
            check('advance_deduction recorded on slip2', slip2.advance_deduction, Decimal('500.00'))
            check('net after advance auto-sync still includes the bonus',
                  slip2.net_salary, expect_net2)

            print("\nSCENARIO: non-admin cannot edit/delete an approved or paid bonus")
            non_admin = make_non_admin_user()
            gross_before = slip2.gross_salary
            resp = update_bonus(bonus4, non_admin, amount='9999.00')
            check('non-admin edit of approved bonus is rejected', resp.get('success'), False)
            slip2.refresh_from_db()
            check('non-admin edit did not change the payslip', slip2.gross_salary, gross_before)

            resp = delete_bonus(bonus4, non_admin)
            check('non-admin delete of approved bonus is rejected', resp.get('success'), False)
            check('bonus4 still exists after rejected non-admin delete',
                  Bonus.objects.filter(pk=bonus4.pk).exists(), True)

            print("\nSCENARIO: administrator can edit an approved bonus's amount, payslip re-syncs")
            resp = update_bonus(bonus4, user, amount='1600.00')
            check('admin edit of approved bonus succeeds', resp.get('success'), True)
            slip2.refresh_from_db()
            expect_gross_after_edit = BASE_GROSS + Decimal('1600.00')
            check('gross reflects the edited bonus amount', slip2.gross_salary, expect_gross_after_edit)
            expect_net_after_edit = expect_gross_after_edit - Decimal('500.00')
            check('net reflects the edited bonus amount minus the advance',
                  slip2.net_salary, expect_net_after_edit)

            print("\nSCENARIO: administrator can delete an approved bonus, its amount is removed")
            resp = delete_bonus(bonus4, user)
            check('admin delete of approved bonus succeeds', resp.get('success'), True)
            check('bonus4 row is actually gone', Bonus.objects.filter(pk=bonus4.pk).exists(), False)
            slip2.refresh_from_db()
            check('gross drops back to baseline after admin delete', slip2.gross_salary, BASE_GROSS)
            check('net drops back to baseline minus the advance after admin delete',
                  slip2.net_salary, BASE_GROSS - Decimal('500.00'))

            print("\nSCENARIO: editing/deleting a bonus tied to a FINALIZED payslip surfaces a note, doesn't touch it")
            MONTH3, YEAR3 = 5, 2031
            run3 = PayrollRun.objects.create(
                title='Sync-test run 3', frequency='monthly',
                month=MONTH3, year=YEAR3, status='completed',
            )
            slip3 = Payslip.objects.create(
                payroll_run=run3, employee=employee,
                basic_salary=BASE_GROSS, gross_salary=BASE_GROSS,
                total_deductions=Decimal('0'), advance_deduction=Decimal('0'),
                absent_deduction=Decimal('0'), net_salary=BASE_GROSS,
                salary_structure={
                    'earnings_list': [{'name': 'Basic Salary', 'amount': float(BASE_GROSS)}],
                    'deductions_list': [],
                },
                status='generated',
            )
            bonus5 = Bonus.objects.create(
                employee=employee, bonus_type='other', amount=Decimal('800.00'),
                month=MONTH3, year=YEAR3, status='pending', created_by=user,
            )
            approve_bonus(bonus5, user)
            slip3.refresh_from_db()
            check('slip3 gross includes bonus5 before finalizing', slip3.gross_salary, BASE_GROSS + bonus5.amount)

            slip3.is_finalized = True
            slip3.save(update_fields=['is_finalized'])

            resp = update_bonus(bonus5, user, amount='950.00')
            check('admin edit on a finalized-payslip bonus still succeeds', resp.get('success'), True)
            check('edit response carries a finalized-payslip note', 'note' in resp, True)
            slip3.refresh_from_db()
            check('finalized slip3 gross is untouched by the edit', slip3.gross_salary, BASE_GROSS + bonus5.amount)

            resp = delete_bonus(bonus5, user)
            check('admin delete on a finalized-payslip bonus still succeeds', resp.get('success'), True)
            check('delete response carries a finalized-payslip note', 'note' in resp, True)
            slip3.refresh_from_db()
            check('finalized slip3 gross is untouched by the delete', slip3.gross_salary, BASE_GROSS + bonus5.amount)

            print("\nSCENARIO: bonus_create rejects garbage bonus_type/month/year instead of storing them")
            baseline_count = Bonus.objects.count()
            r = create_bonus_via_view(user, employee, bonus_type='<script>alert(1)</script>')
            check('bogus bonus_type is rejected', r.get('success'), False)
            r = create_bonus_via_view(user, employee, month='13')
            check('month=13 is rejected', r.get('success'), False)
            r = create_bonus_via_view(user, employee, month='0')
            check('month=0 is rejected', r.get('success'), False)
            r = create_bonus_via_view(user, employee, year='1800')
            check('year=1800 is rejected', r.get('success'), False)
            check('none of the rejected attempts created a row', Bonus.objects.count(), baseline_count)
            r = create_bonus_via_view(user, employee)
            check('a valid create still succeeds', r.get('success'), True)

            raise Rollback
    except Rollback:
        print("\n(fixtures rolled back)")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
