"""
Verifies outbound Meta message delivery reports real failures.

Covers the two bugs behind "Failed to send message to Meta. Please check your
page connection and permissions.":

  1. A page whose token Graph rejects (code 190 — typically the page was left
     unticked during the last Facebook Login, so its token silently died) kept
     showing as "Connected" forever and every send produced the same opaque
     toast. It now flips to status='error' and the toast quotes Graph's reason.
  2. Attachments were sent as `payload.url` pointing at this server's own
     /media/ path. Meta fetches that URL from their side, so anything not
     publicly reachable (local dev, protected media) could never be delivered.
     The bytes are uploaded as multipart instead.

Run: python test_meta_send_failures.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.core.files.base import ContentFile
from django.db import transaction

from trendycrm import views as crm_views
from trendycrm import meta_sync
from trendycrm.models import CRMIntegration, CRMContact, CRMConversation, CRMMessage

results = []


def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


TOKEN_ERROR = {
    'error': {
        'message': ("Any of the pages_read_engagement, pages_manage_metadata, "
                    "pages_read_user_content, pages_manage_ads, pages_show_list or "
                    "pages_messaging permission(s) must be granted before "
                    "impersonating a user's page."),
        'type': 'OAuthException',
        'code': 190,
    }
}

print("\n" + "=" * 70)
print("META SEND FAILURE REPORTING")
print("=" * 70)

original_post = meta_sync.requests.post

try:
    with transaction.atomic():
        integ = CRMIntegration.objects.create(
            channel_type='facebook',
            status='connected',
            account_name='Routine Of Karma Production (103317018509730)',
            access_token='PAGE_TOKEN_THAT_GRAPH_REJECTS',
        )
        contact = CRMContact.objects.create(name='Milan Magrati', meta_id='5725655340816619')
        conv = CRMConversation.objects.create(contact=contact, channel='facebook', integration=integ)

        # ── 1. Rejected token -> real error returned, page flagged ─────────────
        print("\n1) Graph rejects the page token")
        meta_sync.requests.post = lambda *a, **kw: FakeResponse(400, TOKEN_ERROR)

        ok, err = meta_sync.send_meta_message(integ, contact.meta_id, 'hhhh')
        check("send reports failure", ok is False)
        check("Graph's own reason is returned", 'pages_messaging' in (err or ''))
        check("error code is included", '[190]' in (err or ''))

        integ.refresh_from_db()
        check("integration flagged as needing reconnect", integ.status == 'error')
        check("reason stored on the integration", 'pages_messaging' in integ.meta.get('token_error', ''))
        check("flag is timestamped", bool(integ.meta.get('token_error_at')))

        # ── 2. The toast names the page and how to fix it ──────────────────────
        print("\n2) User-facing failure message is actionable")
        toast = crm_views._delivery_failure_message(integ, err)
        check("names the page it failed on", 'Routine Of Karma Production' in toast)
        check("quotes the provider reason", 'pages_messaging' in toast)
        check("tells the user to reconnect", 'reconnect' in toast.lower() and 'Integrations' in toast)

        # ── 3. A recovered page clears the flag on the next success ────────────
        print("\n3) Successful send clears the reconnect flag")
        meta_sync.requests.post = lambda *a, **kw: FakeResponse(200, {'message_id': 'mid.1'})
        ok, err = meta_sync.send_meta_message(integ, contact.meta_id, 'hhhh')
        integ.refresh_from_db()
        check("send succeeds", ok is True and err is None)
        check("status back to connected", integ.status == 'connected')
        check("stored error cleared", 'token_error' not in integ.meta)

        # ── 4. Attachments upload bytes, never a local /media/ URL ─────────────
        print("\n4) Attachments are uploaded, not linked")
        captured = {}

        def capture_post(url, **kw):
            captured.update(kw)
            captured['url'] = url
            return FakeResponse(200, {'message_id': 'mid.2'})

        meta_sync.requests.post = capture_post
        msg = CRMMessage.objects.create(
            conversation=conv, sender='Milan', body='', is_outbound=True,
            attachment=ContentFile(b'RIFFfake-audio', name='voice.mp3'),
            attachment_type='audio', attachment_name='voice.mp3',
        )
        ok, err = meta_sync.send_meta_message(
            integ, contact.meta_id, '', attachment_file=msg.attachment, attachment_type='audio',
        )
        check("attachment send succeeds", ok is True)
        check("file is uploaded as multipart", 'filedata' in (captured.get('files') or {}))
        check("no unreachable local URL is handed to Meta",
              '127.0.0.1' not in str(captured.get('data')) and 'localhost' not in str(captured.get('data')))
        check("declared as an audio attachment",
              '"type": "audio"' in (captured.get('data') or {}).get('message', ''))
        msg.attachment.delete(save=False)

        # ── 5. Text typed alongside an attachment isn't dropped ────────────────
        print("\n5) A caption sent with an attachment still reaches the customer")
        sent_bodies = []

        def capture_both(url, **kw):
            if 'files' in kw:
                sent_bodies.append(('attachment', (kw.get('data') or {}).get('message', '')))
            else:
                sent_bodies.append(('text', ((kw.get('json') or {}).get('message') or {}).get('text')))
            return FakeResponse(200, {'message_id': 'mid.3'})

        meta_sync.requests.post = capture_both
        msg2 = CRMMessage.objects.create(
            conversation=conv, sender='Milan', body='Here is the receipt', is_outbound=True,
            attachment=ContentFile(b'%PDF-fake', name='receipt.pdf'),
            attachment_type='document', attachment_name='receipt.pdf',
        )
        ok, err = meta_sync.send_meta_message(
            integ, contact.meta_id, 'Here is the receipt',
            attachment_file=msg2.attachment, attachment_type='document',
        )
        check("send succeeds", ok is True)
        check("attachment went first", sent_bodies and sent_bodies[0][0] == 'attachment')
        check("the typed caption followed as its own message",
              ('text', 'Here is the receipt') in sent_bodies)
        msg2.attachment.delete(save=False)

        # ── 6. A flagged page keeps receiving inbound messages ────────────────
        # Dropping webhooks for a page we merely can't *send* from would lose
        # real customer enquiries — far worse than the send failure itself.
        print("\n6) Flagging a page never blocks inbound webhooks")
        integ.status = 'error'
        integ.save(update_fields=['status'])
        resolved = meta_sync._resolve_integration_by_page('103317018509730')
        check("errored page still resolves for inbound routing",
              resolved is not None and resolved.pk == integ.pk)

        wa = CRMIntegration.objects.create(
            channel_type='whatsapp', status='error', account_name='WA Number',
            access_token='tok', meta={'phone_number_id': '5551234'},
        )
        check("errored WhatsApp number still resolves for inbound routing",
              meta_sync._resolve_whatsapp_integration('5551234') == wa)

        # ── 7. No connected account at all -> still an explicit reason ─────────
        print("\n7) Missing token is reported, not swallowed")
        integ.access_token = ''
        ok, err = meta_sync.send_meta_message(integ, contact.meta_id, 'hhhh')
        check("failure explains the missing token", ok is False and 'access token' in (err or '').lower())

        transaction.set_rollback(True)

except Exception:
    import traceback
    traceback.print_exc()
    results.append(False)
finally:
    meta_sync.requests.post = original_post

print("\n" + "=" * 70)
print(f"RESULT: {sum(results)}/{len(results)} checks passed")
print("=" * 70)
