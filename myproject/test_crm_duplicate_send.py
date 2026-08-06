"""
Verifies the CRM composer can't send the same message twice.

Double-clicking Send posts the composer twice. The browser-side guard stops the
second submit, but this checks the server half: two POSTs carrying the same
client_token must produce exactly one message, while a fresh token still sends.

    python test_crm_duplicate_send.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402

# Django's test client posts as 'testserver', which this project's real
# ALLOWED_HOSTS has no reason to carry.
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from accounts.models import CustomUser  # noqa: E402
from trendycrm.models import CRMContact, CRMConversation, CRMMessage  # noqa: E402


def main():
    user, _ = CustomUser.objects.get_or_create(
        username='dupe_send_probe',
        defaults={'role': 'administrator', 'is_staff': True},
    )
    contact = CRMContact.objects.create(name='Dupe Send Probe', created_by=user)
    # 'web' channel on purpose: no Meta integration, so no outbound API call.
    conv = CRMConversation.objects.create(contact=contact, channel='web',
                                          assigned_to=user)

    client = Client()
    client.force_login(user)
    url = reverse('trendycrm:send_message', args=[conv.pk])

    def outbound():
        return CRMMessage.objects.filter(conversation=conv, is_outbound=True)

    failures = []
    try:
        token = 'probe-token-1'
        client.post(url, {'body': 'Namaste hajur', 'client_token': token})
        client.post(url, {'body': 'Namaste hajur', 'client_token': token})
        count = outbound().count()
        if count == 1:
            print('PASS: double POST with the same token sent 1 message')
        else:
            failures.append(f'same token twice -> expected 1 message, got {count}')

        client.post(url, {'body': 'Namaste hajur', 'client_token': 'probe-token-2'})
        count = outbound().count()
        if count == 2:
            print('PASS: a new token still sends, even with identical text')
        else:
            failures.append(f'second distinct token -> expected 2 messages, got {count}')

        # Legacy/tokenless callers must keep working rather than being deduped
        # against each other on a NULL token.
        client.post(url, {'body': 'no token here'})
        client.post(url, {'body': 'no token here either'})
        count = outbound().count()
        if count == 4:
            print('PASS: tokenless posts are unaffected')
        else:
            failures.append(f'tokenless posts -> expected 4 messages, got {count}')
    finally:
        CRMMessage.objects.filter(conversation=conv).delete()
        conv.delete()
        contact.delete()
        user.delete()

    if failures:
        print('\nFAILED:')
        for f in failures:
            print('  -', f)
        raise SystemExit(1)
    print('\nAll checks passed.')


if __name__ == '__main__':
    main()
