"""Heal payslips written before the bonus_total_included bookkeeping existed.

Runs as part of `manage.py migrate`, so deploying is all that is needed --
there is no separate data-fix script to remember to run against production.

Two populations exist in any database written before that bookkeeping landed:

  AT RISK    generated with a bonus folded into gross_salary but no marker
             recorded, so the next payslip download would read the missing
             marker as zero and add the same bonus a second time.

  CORRUPTED  that second addition already happened, so gross_salary and
             net_salary are inflated by exactly one bonus amount.

heal_payslip_bonus_snapshot() reconciles both against a recomputed baseline and
stamps `gross_before_bonus` so later reconciliations are exact. Payslips that
match neither shape are left untouched and logged, since a manual adjustment
can move gross legitimately and guessing would do more harm than the bug.

The whole pass is best-effort: a failure here must never block a deploy, and
_sync_bonus_to_payslip() heals each payslip lazily anyway the first time it is
touched. Finalized payslips are skipped, as everywhere else.
"""
import logging

from django.db import migrations

logger = logging.getLogger('hrm')


def heal_existing_payslips(apps, schema_editor):
    # Deliberately the real model + helper, not the historical model: this is a
    # one-time forward correction that needs the live payroll engine.
    try:
        from hrm.models import Payslip
        from hrm.views import heal_payslip_bonus_snapshot
    except Exception as exc:
        logger.warning('0057: skipped payslip healing, imports unavailable: %s', exc)
        return

    tally = {}
    for slip in Payslip.objects.select_related('employee', 'payroll_run').filter(
        is_finalized=False
    ).iterator():
        try:
            outcome = heal_payslip_bonus_snapshot(slip)
        except Exception as exc:
            logger.warning('0057: could not heal payslip %s: %s', slip.pk, exc)
            outcome = 'error'
        tally[outcome] = tally.get(outcome, 0) + 1

    if tally:
        logger.info('0057: payslip bonus healing complete — %s', tally)


def noop_reverse(apps, schema_editor):
    """Nothing to undo: this corrects wrong numbers, and putting the wrong
    numbers back would be the bug, not a rollback."""


class Migration(migrations.Migration):

    dependencies = [
        ('hrm', '0056_attendanceregularization_pre_regularization_state'),
    ]

    operations = [
        migrations.RunPython(heal_existing_payslips, noop_reverse),
    ]
