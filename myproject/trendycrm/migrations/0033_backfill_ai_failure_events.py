from django.db import migrations
from django.utils import timezone


# Text every previously-written failure chip starts with. They were plain log
# lines with no kind, so nothing could find them again to retire them — which is
# exactly why they are still sitting in threads that were answered long ago.
LEGACY_PREFIX = "The AI couldn't reply to this message"


def backfill(apps, schema_editor):
    CRMMessage = apps.get_model('trendycrm', 'CRMMessage')
    now = timezone.now()

    legacy = CRMMessage.objects.filter(
        is_system=True, event_kind='', body__startswith=LEGACY_PREFIX,
    )
    for chip in legacy.iterator():
        chip.event_kind = 'ai_failure'
        # Anything the conversation moved past — an AI reply or an agent's — is a
        # condition that ended. Retire it. A chip with nothing after it is still
        # a genuinely unanswered customer, so it stays up and now carries the
        # Retry/Dismiss actions.
        answered = CRMMessage.objects.filter(
            conversation_id=chip.conversation_id,
            is_outbound=True,
            created_at__gt=chip.created_at,
        ).exists()
        if answered:
            chip.resolved_at = now
        chip.save(update_fields=['event_kind', 'resolved_at'])


def unbackfill(apps, schema_editor):
    CRMMessage = apps.get_model('trendycrm', 'CRMMessage')
    CRMMessage.objects.filter(is_system=True, event_kind='ai_failure').update(
        event_kind='', resolved_at=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('trendycrm', '0032_crmmessage_event_data_crmmessage_event_kind_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
