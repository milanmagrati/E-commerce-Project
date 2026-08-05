"""
Re-runs the message-type analysis over CRM messages already in the database.

The analysis used to have four buckets, so every greeting and sign-off in the
existing history is stored as 'general_query' and renders as "General Inquiry ·
Low". This rewrites those to what the current classifier says, so old threads
show the same chips new ones do.

    python manage.py reclassify_crm_intents --dry-run
    python manage.py reclassify_crm_intents

Uses only the free keyword pass — it never spends an LLM call, so it is safe to
run against the whole history on a free-tier key. It does not raise lead or
complaint chips for historical messages; it only relabels.
"""
from django.core.management.base import BaseCommand

from trendycrm.ai_router import _keyword_intent
from trendycrm.models import CRMConversation


class Command(BaseCommand):
    help = "Re-analyse stored CRM messages so their intent/priority chips match the current classifier."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        scanned = inbound_changed = reply_changed = 0

        for conv in CRMConversation.objects.all().iterator():
            # The verdict on the customer message the AI is currently answering.
            # Reset per conversation so one thread can't leak into the next.
            pending = ''
            for msg in conv.messages.filter(is_system=False).order_by('created_at', 'pk'):
                scanned += 1

                if not msg.is_outbound:
                    verdict = _keyword_intent(msg.body) if (msg.body or '').strip() else ''
                    # An inconclusive verdict is not an improvement on whatever is
                    # stored — leave it, and let the reply below keep its intent too.
                    pending = verdict
                    if verdict and msg.ai_intent != verdict:
                        self._write(msg, verdict, dry_run)
                        inbound_changed += 1
                elif msg.is_ai and pending and msg.ai_intent != pending:
                    # An AI reply carries the intent of the message it answered.
                    self._write(msg, pending, dry_run)
                    reply_changed += 1

        verb = "would update" if dry_run else "updated"
        self.stdout.write(self.style.SUCCESS(
            f"Scanned {scanned} messages; {verb} {inbound_changed} customer messages "
            f"and {reply_changed} AI replies."
        ))
        if dry_run:
            self.stdout.write("Dry run — nothing was written.")

    def _write(self, msg, intent, dry_run):
        self.stdout.write(f"  #{msg.pk} {msg.ai_intent or '(none)'} -> {intent}: {(msg.body or '')[:60]!r}")
        if not dry_run:
            msg.ai_intent = intent
            msg.save(update_fields=['ai_intent'])
