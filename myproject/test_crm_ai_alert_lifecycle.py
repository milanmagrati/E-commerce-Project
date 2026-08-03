"""
Verifies the lifecycle of the in-thread "the AI couldn't reply" alert.

The alert used to be written once and left in the conversation forever, so a
30-second rate limit kept flagging a chat long after the bot (or an agent) had
answered it. It is now a *condition*: one chip per conversation, retried in the
background, and retired the moment it stops being true.

    python test_crm_ai_alert_lifecycle.py

Touches the real database and cleans up everything it creates.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

# The test client speaks as 'testserver', which this project's ALLOWED_HOSTS
# doesn't know about — this is a script, not the test runner, so nothing relaxes
# it for us. In-memory only; the deployed setting is untouched.
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from trendycrm import meta_sync  # noqa: E402
from trendycrm import ai_router  # noqa: E402
from trendycrm.models import (  # noqa: E402
    CRMChatbotConfig, CRMContact, CRMConversation, CRMIntegration, CRMMessage,
)
from accounts.models import CustomUser  # noqa: E402

FAILURES = []
SCHEDULED = []


def check(label, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ''))
    if not condition:
        FAILURES.append(label)


def open_chip(conv):
    return CRMMessage.open_event(conv, CRMMessage.EVENT_AI_FAILURE)


def chips(conv):
    return CRMMessage.objects.filter(conversation=conv, is_system=True,
                                     event_kind=CRMMessage.EVENT_AI_FAILURE)


def rate_limited(**kwargs):
    """What route_message returns when a free-tier key is out of quota."""
    return {
        'reply': '', 'intent': 'general_query', 'model_used': '', 'checkout_link': '',
        'open_ticket': False, 'success': False, 'notice': None,
        'error': ('AI provider rate limit reached — your API key has used its quota for '
                  'the moment. Wait a minute and try again, or upgrade the plan. '
                  '(Gemini API error 429: RESOURCE_EXHAUSTED)'),
        'error_kind': 'rate_limit', 'retryable': True,
    }


def answered(**kwargs):
    return {
        'reply': 'Yes, we have that in stock!', 'intent': 'purchase_intent',
        'model_used': 'gemini-flash-latest', 'checkout_link': '', 'open_ticket': False,
        'success': True, 'error': None, 'notice': None, 'error_kind': '', 'retryable': False,
    }


def fake_schedule(integration, contact, conversation, message_text, attachment, attempt):
    """Records the retry instead of arming a real timer, and returns its delay."""
    SCHEDULED.append(attempt)
    return meta_sync.AI_RETRY_DELAYS[attempt]


def main():
    real_route = ai_router.route_message
    real_schedule = meta_sync._schedule_ai_retry
    created = []

    chatbot = CRMChatbotConfig.objects.create(
        name='[test] Alert lifecycle bot', is_active=True, ai_credits=50)
    created.append(chatbot)
    integration = CRMIntegration.objects.create(
        channel_type='facebook', account_name='[test] Page (999)', status='connected',
        chatbot_config=chatbot)
    created.append(integration)
    chatbot.auto_reply_channels = {str(integration.pk): True}
    chatbot.save(update_fields=['auto_reply_channels'])

    # No meta_id: nothing is sent to Facebook, so the test never touches the network.
    contact = CRMContact.objects.create(name='[test] Customer')
    created.append(contact)
    conv = CRMConversation.objects.create(
        contact=contact, integration=integration, channel='facebook', ai_enabled=True)
    created.append(conv)

    user = CustomUser.objects.filter(is_superuser=True).first()
    temp_user = None
    if not user:
        temp_user = user = CustomUser.objects.create_superuser(
            username='[test]alertagent', email='t@example.com', password='x')
        created.append(temp_user)

    ai_router.route_message = rate_limited
    meta_sync._schedule_ai_retry = fake_schedule

    try:
        print("\n1. A failed auto-reply raises one alert and schedules a retry")
        meta_sync.process_incoming_webhook_message(
            integration, contact, conv, "Is the serum in stock?")
        chip = open_chip(conv)
        check("an alert is raised", chip is not None)
        check("it is a resolvable ai_failure event", bool(chip and chip.is_open_event))
        check("it says a retry is coming", bool(chip and chip.event_is_retrying),
              chip.body if chip else '')
        check("the raw provider blob stays out of the thread",
              bool(chip and 'RESOURCE_EXHAUSTED' not in chip.body))
        check("the original message is kept for the retry",
              bool(chip and chip.event_data.get('message_text') == "Is the serum in stock?"))
        check("a background retry was scheduled", SCHEDULED == [0], str(SCHEDULED))

        print("\n2. Further failures update that alert instead of stacking new ones")
        meta_sync.process_incoming_webhook_message(
            integration, contact, conv, "Hello? Anyone there?")
        check("still exactly one alert in the thread", chips(conv).count() == 1,
              f"found {chips(conv).count()}")

        print("\n3. The alert is visible while the problem is live")
        check("it shows in the thread", open_chip(conv).pk in
              set(conv.visible_messages().values_list('pk', flat=True)))

        print("\n4. A successful reply retires it")
        ai_router.route_message = answered
        ok, reason = meta_sync._attempt_ai_reply(
            integration, contact, conv, "Is the serum in stock?", attempt=1)
        check("the retry succeeds", ok, reason)
        check("no alert is left open", open_chip(conv) is None)
        check("it is gone from the thread", not any(
            m.event_kind == CRMMessage.EVENT_AI_FAILURE
            for m in conv.visible_messages()))
        check("the alert is kept for the record, not deleted", chips(conv).count() == 1)
        check("the AI's reply is in the thread",
              conv.messages.filter(is_ai=True, body__icontains='in stock').exists())

        print("\n5. A retry that lands after the alert was cleared does nothing")
        before = conv.messages.count()
        ok, reason = meta_sync._attempt_ai_reply(
            integration, contact, conv, "Is the serum in stock?", attempt=2)
        check("the stale retry is dropped", not ok, reason)
        check("it posted nothing to the customer", conv.messages.count() == before)

        print("\n6. An agent replying clears the alert")
        ai_router.route_message = rate_limited
        meta_sync.process_incoming_webhook_message(integration, contact, conv, "Still waiting")
        check("a fresh alert is up", open_chip(conv) is not None)

        client = Client()
        client.force_login(user)
        resp = client.post(f'/trendy-crm/conversations/{conv.pk}/send/',
                           {'body': "Sorry for the wait — yes, it's in stock."})
        check("the agent's reply is accepted", resp.status_code in (200, 302), str(resp.status_code))
        check("the alert is cleared by the human reply", open_chip(conv) is None)

        print("\n7. The Retry AI button re-runs the message the bot choked on")
        ai_router.route_message = rate_limited
        # Reset the takeover pause the agent's reply just armed, so the bot is
        # allowed to answer again — this tests the retry, not the pause.
        conv.last_human_reply_at = None
        conv.save(update_fields=['last_human_reply_at'])
        meta_sync.process_incoming_webhook_message(
            integration, contact, conv, "What are the delivery charges?")
        chip = open_chip(conv)
        check("an alert is up to retry", chip is not None)

        ai_router.route_message = answered
        resp = client.post(f'/trendy-crm/messages/{chip.pk}/retry-ai/')
        check("the retry endpoint reports success",
              resp.status_code == 200 and resp.json().get('status') == 'ok', resp.content[:200])
        check("the alert is cleared by the successful retry", open_chip(conv) is None)

        print("\n8. Dismissing an alert removes it from the thread")
        ai_router.route_message = rate_limited
        conv.last_human_reply_at = None
        conv.save(update_fields=['last_human_reply_at'])
        meta_sync.process_incoming_webhook_message(integration, contact, conv, "Hello?")
        chip = open_chip(conv)
        resp = client.post(f'/trendy-crm/messages/{chip.pk}/dismiss-alert/')
        check("dismiss succeeds", resp.status_code == 200, str(resp.status_code))
        check("the alert is gone from the thread", open_chip(conv) is None)

        print("\n9. A non-retryable failure is reported as final, with no retry promised")
        SCHEDULED.clear()
        ai_router.route_message = lambda **kw: {
            'reply': '', 'success': False, 'error': 'Chatbot configuration is missing or inactive',
            'error_kind': 'not_configured', 'retryable': False, 'intent': '', 'model_used': '',
            'checkout_link': '', 'open_ticket': False, 'notice': None,
        }
        conv.last_human_reply_at = None
        conv.save(update_fields=['last_human_reply_at'])
        meta_sync.process_incoming_webhook_message(integration, contact, conv, "Anyone?")
        chip = open_chip(conv)
        check("an alert is raised", chip is not None)
        check("it does not claim a retry is coming", not chip.event_is_retrying, chip.body)
        check("nothing was scheduled", SCHEDULED == [], str(SCHEDULED))
        check("it points the agent at Retry AI", 'Retry AI' in chip.body, chip.body)

    finally:
        ai_router.route_message = real_route
        meta_sync._schedule_ai_retry = real_schedule
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
