"""
Verifies auto-detection of "potential lead" / "complaint" messages in Trendy CRM.

Every inbound message should be classified by the free keyword pass, label the
conversation, and raise a dismissible in-thread chip plus a cross-page poll
count — all without ever touching a paid LLM call (the CRM's Gemini key has a
very low free-tier quota).

    python test_crm_lead_detection.py

Touches the real database and cleans up everything it creates.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from trendycrm import meta_sync  # noqa: E402
from trendycrm import ai_router  # noqa: E402
from trendycrm.models import (  # noqa: E402
    CRMContact, CRMConversation, CRMIntegration, CRMMessage,
)
from accounts.models import CustomUser  # noqa: E402

FAILURES = []


def check(label, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ''))
    if not condition:
        FAILURES.append(label)


def open_chip(conv, kind):
    return CRMMessage.open_event(conv, kind)


def send(conv, contact, body, external_id):
    """Wraps upsert_inbound_message with the boilerplate args it needs."""
    return meta_sync.upsert_inbound_message(
        integration=conv.integration, contact=contact, conversation=conv,
        body=body, external_id=external_id, sender_name=contact.name,
        is_outbound=False, created_at=timezone.now(),
    )


def main():
    created = []

    contact = CRMContact.objects.create(name='[test] Lead Customer')
    created.append(contact)
    integration = CRMIntegration.objects.create(
        channel_type='facebook', account_name='[test] Lead Page (998)', status='connected')
    created.append(integration)
    # ai_enabled left False and no chatbot_config: keeps the AI auto-reply path
    # from ever engaging, so this test only exercises detection, not replies.
    conv = CRMConversation.objects.create(
        contact=contact, integration=integration, channel='facebook', ai_enabled=False)
    created.append(conv)

    user = CustomUser.objects.filter(is_superuser=True).first()
    temp_user = None
    if not user:
        temp_user = user = CustomUser.objects.create_superuser(
            username='[test]leadagent', email='t2@example.com', password='x')
        created.append(temp_user)

    # Fail loudly if lead detection ever reaches a paid call — it must only
    # ever use the free keyword pass, regardless of CRM_LLM_INTENT_CLASSIFIER.
    def _forbidden(*a, **kw):
        raise AssertionError("lead detection must never call an LLM")
    real_gemini = ai_router._call_gemini_rest
    real_openai = ai_router._call_openai_chat
    ai_router._call_gemini_rest = _forbidden
    ai_router._call_openai_chat = _forbidden

    try:
        print("\n1. A buying-interest message is flagged as a potential lead")
        msg = send(conv, contact, "how much for this, i need this product", "ext-lead-1")
        check("message was stored", msg is not None)
        check("ai_intent is purchase_intent", msg.ai_intent == 'purchase_intent', msg.ai_intent)
        check("the Potential Lead label is applied",
              conv.labels.filter(name=meta_sync.LEAD_LABEL_NAME).exists())
        chip = open_chip(conv, CRMMessage.EVENT_LEAD_DETECTED)
        check("a lead chip is open", chip is not None)
        check("it shows in the thread", bool(chip and chip.is_open_event))

        print("\n2. A second buying-interest message doesn't stack a new chip")
        send(conv, contact, "please send me the price too", "ext-lead-2")
        count = CRMMessage.objects.filter(
            conversation=conv, is_system=True, event_kind=CRMMessage.EVENT_LEAD_DETECTED).count()
        check("still exactly one lead chip", count == 1, f"found {count}")

        print("\n3. A complaint message is flagged separately from a lead")
        send(conv, contact, "this item arrived broken, i want a refund", "ext-complaint-1")
        check("the Complaint label is applied",
              conv.labels.filter(name=meta_sync.COMPLAINT_LABEL_NAME).exists())
        complaint_chip = open_chip(conv, CRMMessage.EVENT_COMPLAINT_DETECTED)
        check("a complaint chip is open", complaint_chip is not None)
        check("the lead chip is untouched by the complaint",
              open_chip(conv, CRMMessage.EVENT_LEAD_DETECTED) is not None)

        print("\n4. Routine chatter raises nothing")
        before_labels = set(conv.labels.values_list('pk', flat=True))
        msg = send(conv, contact, "hi", "ext-routine-1")
        check("no new label was added", set(conv.labels.values_list('pk', flat=True)) == before_labels)
        check("a bare greeting reads as a greeting", msg.ai_intent == 'greeting', msg.ai_intent)
        check("and carries no priority", msg.intent_priority == 'low', msg.intent_priority)

        print("\n4a. Every message type gets its own label and priority")
        matrix = [
            ("hi there",                  'greeting',        'Greeting',     'low'),
            ("ok thanks",                 'closing_thanks',  'Thanks',       'low'),
            ("where is my order",         'order_status',    'Order Status', 'medium'),
            ("do you deliver to pokhara", 'delivery_query',  'Delivery',     'medium'),
            ("i need this product",       'purchase_intent', 'Purchase',     'high'),
            ("it arrived broken",         'product_issue',   'Issue',        'high'),
        ]
        for text, intent, label, priority in matrix:
            verdict = ai_router._keyword_intent(text)
            probe = CRMMessage(ai_intent=verdict)
            check(f"{text!r} -> {label} / {priority}",
                  verdict == intent and probe.intent_label == label
                  and probe.intent_priority == priority,
                  f"got {verdict!r} -> {probe.intent_label} / {probe.intent_priority}")

        # A greeting only counts when it is the whole message — a question
        # attached to one must outrank the hello.
        check("'hi, how much is this' is a purchase, not a greeting",
              ai_router._keyword_intent("hi, how much is this") == 'purchase_intent',
              ai_router._keyword_intent("hi, how much is this"))
        # 'hi' inside a word must not match the whole-message greeting set.
        check("'this fits?' is not read as a greeting",
              ai_router._keyword_intent("this fits?") != 'greeting')

        print("\n4b. An attachment-only message is not mislabelled as spam")
        msg = meta_sync.upsert_inbound_message(
            integration=integration, contact=contact, conversation=conv,
            body='', external_id='ext-photo-1', sender_name=contact.name,
            is_outbound=False, created_at=timezone.now(),
            attachments={'type': 'image', 'url': 'http://example.invalid/x.jpg', 'name': 'x.jpg'},
        )
        check("the photo message was stored", msg is not None)
        check("a bare photo is not tagged spam_noise", msg.ai_intent == '', msg.ai_intent)

        print("\n4c. A medium-priority question is analysed but not escalated")
        before_labels = set(conv.labels.values_list('pk', flat=True))
        msg = send(conv, contact, "where is my order, any tracking id?", "ext-status-1")
        check("it reads as an order-status question", msg.ai_intent == 'order_status', msg.ai_intent)
        check("priority is medium", msg.intent_priority == 'medium', msg.intent_priority)
        check("it raises no lead/complaint label",
              set(conv.labels.values_list('pk', flat=True)) == before_labels)

        print("\n5. Dismissing the lead alert resolves it, and the poll count reflects it")
        client = Client()
        client.force_login(user)

        resp = client.get('/trendy-crm/alerts/poll/')
        check("poll endpoint responds", resp.status_code == 200, str(resp.status_code))
        payload = resp.json()
        before_count = payload['count']
        check("poll count includes both open chips", before_count >= 2, before_count)
        check("each alert carries the id the poller de-dupes on",
              all('message_id' in a for a in payload['alerts']), str(payload['alerts'][:1]))

        lead_chip = open_chip(conv, CRMMessage.EVENT_LEAD_DETECTED)
        resp = client.post(f'/trendy-crm/messages/{lead_chip.pk}/dismiss-alert/')
        check("dismiss succeeds", resp.status_code == 200, str(resp.status_code))
        check("the lead chip is resolved", open_chip(conv, CRMMessage.EVENT_LEAD_DETECTED) is None)

        resp = client.get('/trendy-crm/alerts/poll/')
        after_count = resp.json()['count']
        check("poll count dropped by one after dismissal", after_count == before_count - 1,
              f"{before_count} -> {after_count}")

        print("\n6. The sidebar list badges the flagged conversation")
        resp = client.get('/trendy-crm/conversations/list-ajax/')
        check("sidebar ajax responds", resp.status_code == 200, str(resp.status_code))
        sidebar = resp.content.decode()
        check("the lead badge is rendered", 'title="Potential lead"' in sidebar)
        check("the complaint badge is rendered", 'title="Possible complaint"' in sidebar)

        print("\n7. The customer's own high-priority message carries a chip")
        # Also a regression guard: this thread contains the attachment-only
        # message from 4b, whose type is set but whose file was never
        # downloaded. Rendering .url on that used to raise ValueError and 500.
        resp = client.get(f'/trendy-crm/conversations/ajax/?id={conv.pk}')
        check("thread renders with a file-less attachment present",
              resp.status_code == 200, str(resp.status_code))
        thread = resp.content.decode()
        check("the inbound priority chip is rendered", 'ai-chips-inbound' in thread)
        check("it reads as a Purchase signal", 'Purchase' in thread)
        check("the medium question is chipped too", 'Order Status' in thread)
        check("medium priority gets its own dot colour", 'dot-medium' in thread)
        check("the chip shows the priority word, not a fixed label",
              'dot-high"></span>High' in thread)
        # One chip per above-routine inbound message, and none for the greeting
        # or the photo — derived from the rows rather than hardcoded.
        expected = sum(
            1 for m in conv.messages.filter(is_outbound=False, is_system=False)
            if m.ai_intent and m.intent_priority != 'low'
        )
        check("exactly one chip per above-routine message, none for chatter",
              thread.count('ai-chips-inbound') == expected,
              f"expected {expected}, found {thread.count('ai-chips-inbound')}")

    finally:
        ai_router._call_gemini_rest = real_gemini
        ai_router._call_openai_chat = real_openai
        # The lead/complaint labels are shared, app-wide taxonomy (like any
        # other CRMLabel created via +Add label) — not test fixtures — so they
        # are intentionally left in place rather than deleted here.
        CRMMessage.objects.filter(conversation=conv).delete()
        for obj in reversed(created):
            try:
                obj.delete()
            except Exception as e:
                print(f"  (cleanup skipped {obj!r}: {e})")

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): " + '; '.join(FAILURES))
    else:
        print("All checks passed.")
    return 1 if FAILURES else 0


if __name__ == '__main__':
    raise SystemExit(main())
