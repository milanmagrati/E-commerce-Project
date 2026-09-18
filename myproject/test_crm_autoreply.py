"""
Verification script for the Trendy CRM AI auto-reply pipeline.

Follows this repo's convention: a standalone root-level script that calls
django.setup() and exercises real models against the real DB. Everything runs
inside a transaction that is rolled back at the end, so it creates no permanent
rows. No external API calls are made — the AI router and the Meta send functions
are monkeypatched.

    python test_crm_autoreply.py
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from trendycrm import ai_router, meta_sync, views as crm_views
from trendycrm.ai_router import build_dynamic_prompt, classify_intent
from trendycrm.meta_sync import (
    HUMAN_TAKEOVER_MINUTES,
    process_incoming_webhook_message,
    upsert_inbound_message,
)
from trendycrm.models import (
    CRMChatbotConfig,
    CRMContact,
    CRMConversation,
    CRMIntegration,
    CRMMessage,
    CRMTicket,
)

# The gates log at INFO on every skip; that's noise for a test run.
logging.disable(logging.CRITICAL)

PASS, FAIL = [], []


def check(name, condition, detail=''):
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ''))


class _Rollback(Exception):
    """Sentinel used to unwind the test transaction."""


# ─── Fakes ────────────────────────────────────────────────────────────────────
class FakeRouter:
    """Stands in for ai_router.route_message so no API call is made."""

    def __init__(self):
        self.calls = []
        self.result = {
            'reply': 'Namaste! Hoodie ko price Rs. 1,590 bata suru hunchha.',
            'intent': 'purchase_intent',
            'model_used': 'test-model',
            'checkout_link': '',
            'open_ticket': False,
            'success': True,
            'error': None,
            'notice': None,
        }

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


def fake_send(integration, recipient_id, message_text=None, **kwargs):
    """Stands in for send_meta_message — never touches the Graph API."""
    fake_send.sent.append((integration.pk, recipient_id, message_text))
    return True, None


fake_send.sent = []


def main():
    print("\n=== 1. Intent classification (keyword pre-pass, no API call) ===")
    cfg = {'gemini_api_key': '', 'openai_api_key': ''}  # force the offline path

    cases = [
        ("My order arrived broken, I want a refund", 'product_issue'),
        ("The jacket is torn, I want my money back", 'product_issue'),
        ("Mero saman bigriyo, paisa firta chahiyo", 'product_issue'),
        ("How much is the hoodie?", 'purchase_intent'),
        ("Kati ho yo t-shirt ko price?", 'purchase_intent'),
        ("Cash on delivery cha?", 'purchase_intent'),
        ("", 'spam_noise'),
        ("!!!???", 'spam_noise'),
    ]
    for text, expected in cases:
        got = classify_intent(text, cfg)
        check(f"{text[:38]!r} -> {expected}", got == expected, f"got {got!r}")

    print("\n=== 1b. Rate-limit retry policy ===")
    from trendycrm.ai_router import _gemini_retry_delay

    class _Resp:
        def __init__(self, body):
            self._b = body

        def json(self):
            return self._b

    def _429(quota_id, delay):
        return _Resp({'error': {'code': 429, 'details': [
            {'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
             'violations': [{'quotaId': quota_id}]},
            {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': delay},
        ]}})

    check("per-minute throttle is retried",
          _gemini_retry_delay(_429('GenerateRequestsPerMinutePerProjectPerModel-FreeTier',
                                   '29s')) == 30.0)
    check("per-day cap is not retried (waiting can't help)",
          _gemini_retry_delay(_429('GenerateRequestsPerDayPerProjectPerModel-FreeTier',
                                   '56s')) is None)
    check("a 429 with no detail is not retried",
          _gemini_retry_delay(_Resp({'error': {'code': 429}})) is None)

    print("\n=== 2. Seeded knowledge base reaches the prompt ===")
    bot = CRMChatbotConfig.objects.order_by('pk').first()
    if not bot:
        print("  SKIP  no chatbot config exists — run: python manage.py seed_crm_chatbot")
    else:
        prompt = build_dynamic_prompt(bot, None, 'general_query')
        check("prompt includes business name",
              bool(bot.business_name) and bot.business_name in prompt)
        check("prompt includes offerings",
              bool(bot.offerings) and bot.offerings[:40] in prompt)
        check("prompt includes FAQs",
              bool(bot.faq_text) and bot.faq_text[:40] in prompt)
        check("prompt includes playbook",
              bool(bot.playbook) and bot.playbook[:40] in prompt)
        check("prompt is substantial (KB is not empty)", len(prompt) > 1500,
              f"prompt is only {len(prompt)} chars — knowledge base looks empty")

    print("\n=== 3. Auto-reply gates ===")
    router = FakeRouter()
    orig_router = ai_router.route_message
    orig_send = meta_sync.send_meta_message
    orig_charge = crm_views.charge_ai_credits

    ai_router.route_message = router
    meta_sync.send_meta_message = fake_send
    crm_views.charge_ai_credits = lambda *a, **k: None

    try:
        with transaction.atomic():
            bot = CRMChatbotConfig.objects.create(
                name='TEST BOT', is_active=True, business_name='Test Co',
                offerings='Test item — Rs. 100', ai_credits=50,
            )
            integ = CRMIntegration.objects.create(
                channel_type='facebook', status='connected',
                account_name='Test Page (999999)', access_token='test-token',
            )
            contact = CRMContact.objects.create(name='Test Customer', meta_id='psid-test-1')
            conv = CRMConversation.objects.create(
                contact=contact, integration=integ, channel='facebook',
                account_id='999999', status='open',
            )

            def replied():
                """True if a new AI message was written for this conversation."""
                return CRMMessage.objects.filter(conversation=conv, is_ai=True).exists()

            def reset():
                CRMMessage.objects.filter(conversation=conv).delete()
                router.calls.clear()
                fake_send.sent.clear()

            # --- gate: no chatbot assigned to the integration
            reset()
            process_incoming_webhook_message(integ, contact, conv, "hello")
            check("skips when no chatbot is assigned", not replied())

            integ.chatbot_config = bot
            integ.save(update_fields=['chatbot_config'])

            # --- gate: channel not enabled in auto_reply_channels
            reset()
            process_incoming_webhook_message(integ, contact, conv, "hello")
            check("skips when channel not in auto_reply_channels", not replied())

            bot.auto_reply_channels = {str(integ.pk): True}
            bot.save(update_fields=['auto_reply_channels'])

            # --- gate: master switch off
            reset()
            bot.is_active = False
            bot.save(update_fields=['is_active'])
            integ.refresh_from_db()
            process_incoming_webhook_message(integ, contact, conv, "hello")
            check("skips when the bot is paused", not replied())
            bot.is_active = True
            bot.save(update_fields=['is_active'])
            integ.refresh_from_db()

            # --- gate: outbound messages
            reset()
            process_incoming_webhook_message(integ, contact, conv, "hi", is_outbound=True)
            check("never replies to our own outbound message", not replied())

            # --- gate: conversation resolved
            reset()
            conv.status = 'resolved'
            conv.save(update_fields=['status'])
            process_incoming_webhook_message(integ, contact, conv, "hello")
            check("skips when the conversation is resolved", not replied())
            conv.status = 'open'
            conv.save(update_fields=['status'])

            # --- gate: per-conversation AI switch
            reset()
            conv.ai_enabled = False
            conv.save(update_fields=['ai_enabled'])
            process_incoming_webhook_message(integ, contact, conv, "hello")
            check("skips when AI is disabled on the conversation", not replied())
            conv.ai_enabled = True
            conv.save(update_fields=['ai_enabled'])

            # --- gate: assigned to a human
            reset()
            from accounts.models import CustomUser
            staff = CustomUser.objects.first()
            if staff:
                conv.assigned_to = staff
                conv.save(update_fields=['assigned_to'])
                process_incoming_webhook_message(integ, contact, conv, "hello")
                check("skips when a human is assigned", not replied())
                conv.assigned_to = None
                conv.save(update_fields=['assigned_to'])
            else:
                print("  SKIP  no user exists to test the assignment gate")

            # --- gate: staff replied recently
            reset()
            conv.last_human_reply_at = timezone.now() - timedelta(
                minutes=HUMAN_TAKEOVER_MINUTES - 1)
            conv.save(update_fields=['last_human_reply_at'])
            process_incoming_webhook_message(integ, contact, conv, "hello")
            check(f"skips while a human replied < {HUMAN_TAKEOVER_MINUTES}m ago", not replied())

            # --- the pause expires
            reset()
            conv.last_human_reply_at = timezone.now() - timedelta(
                minutes=HUMAN_TAKEOVER_MINUTES + 1)
            conv.save(update_fields=['last_human_reply_at'])
            process_incoming_webhook_message(integ, contact, conv, "hello")
            check(f"replies again after the {HUMAN_TAKEOVER_MINUTES}m pause expires", replied())
            conv.last_human_reply_at = None
            conv.save(update_fields=['last_human_reply_at'])

            # --- gate: out of credits
            reset()
            bot.ai_credits = 0
            bot.save(update_fields=['ai_credits'])
            integ.refresh_from_db()
            process_incoming_webhook_message(integ, contact, conv, "hello")
            check("skips when AI credits are exhausted", not replied())
            bot.ai_credits = 50
            bot.save(update_fields=['ai_credits'])
            integ.refresh_from_db()

            print("\n=== 4. Happy path ===")
            reset()
            process_incoming_webhook_message(integ, contact, conv, "How much is the hoodie?")
            msg = CRMMessage.objects.filter(conversation=conv, is_outbound=True).first()
            check("router was called once", len(router.calls) == 1,
                  f"got {len(router.calls)} calls")
            check("reply was delivered to the channel", len(fake_send.sent) == 1)
            check("reply stored as an outbound message", msg is not None)
            check("reply flagged is_ai", bool(msg and msg.is_ai))
            check("reply status is 'sent'", bool(msg and msg.status == 'sent'))
            check("sender names the model",
                  bool(msg and 'Trendy AI' in msg.sender), f"sender={msg.sender if msg else None}")
            conv.refresh_from_db()
            check("conversation preview updated",
                  conv.last_message == router.result['reply'])

            print("\n=== 5. Failed delivery is recorded, not hidden ===")
            reset()
            meta_sync.send_meta_message = lambda *a, **k: (False, 'Outside 24h window')
            process_incoming_webhook_message(integ, contact, conv, "How much is the hoodie?")
            msg = CRMMessage.objects.filter(conversation=conv, is_outbound=True).first()
            check("undelivered reply is marked failed",
                  bool(msg and msg.status == 'failed'),
                  f"status={msg.status if msg else None}")
            meta_sync.send_meta_message = fake_send

            print("\n=== 6. A failed AI call stays silent ===")
            # route_message returns success=False plus a generic apology string
            # when the provider is down or out of quota. That apology must never
            # reach a customer.
            reset()
            router.result = {
                'reply': "I'm sorry, I'm having trouble processing your request right now.",
                'intent': 'general_query', 'model_used': '', 'checkout_link': '',
                'open_ticket': True, 'success': False,
                'error': 'AI provider rate limit reached', 'notice': None,
            }
            process_incoming_webhook_message(integ, contact, conv, "How much is the hoodie?")
            check("no message sent when the AI call failed",
                  not CRMMessage.objects.filter(conversation=conv, is_outbound=True).exists())
            check("nothing delivered to the channel", len(fake_send.sent) == 0)
            check("no ticket opened from a failed AI call",
                  not CRMTicket.objects.filter(
                      description__contains='How much is the hoodie?').exists())
            bot.refresh_from_db()
            check("the failure reason is recorded for the operator",
                  'rate limit' in (bot.last_error or '').lower(),
                  f"last_error={bot.last_error!r}")
            check("the failure is timestamped", bot.last_error_at is not None)

            router.result['success'] = True
            router.result['open_ticket'] = False
            router.result['reply'] = 'Namaste! Hoodie ko price Rs. 1,590 bata suru hunchha.'

            # A later success must clear the banner.
            reset()
            process_incoming_webhook_message(integ, contact, conv, "How much is the hoodie?")
            bot.refresh_from_db()
            check("a successful reply clears the recorded failure", bot.last_error == '',
                  f"last_error={bot.last_error!r}")

            print("\n=== 7. Deduplication by external_id ===")
            reset()
            now = timezone.now()
            first = upsert_inbound_message(
                integration=integ, contact=contact, conversation=conv,
                body='Duplicate test', external_id='mid.abc123',
                sender_name='Test Customer', is_outbound=False, created_at=now,
            )
            second = upsert_inbound_message(
                integration=integ, contact=contact, conversation=conv,
                body='Duplicate test', external_id='mid.abc123',
                sender_name='Test Customer', is_outbound=False, created_at=now,
            )
            stored = CRMMessage.objects.filter(
                conversation=conv, external_id='mid.abc123').count()
            check("first upsert creates a message", first is not None)
            check("second upsert is ignored", second is None)
            check("exactly one row stored", stored == 1, f"found {stored}")

            # Same text, different provider id = a genuinely different message.
            upsert_inbound_message(
                integration=integ, contact=contact, conversation=conv,
                body='Duplicate test', external_id='mid.different',
                sender_name='Test Customer', is_outbound=False, created_at=now,
            )
            check("a distinct external_id is stored separately",
                  CRMMessage.objects.filter(conversation=conv, body='Duplicate test').count() == 2)

            print("\n=== 8. Old messages don't trigger replays ===")
            reset()
            upsert_inbound_message(
                integration=integ, contact=contact, conversation=conv,
                body='Ancient message', external_id='mid.old',
                sender_name='Test Customer', is_outbound=False,
                created_at=timezone.now() - timedelta(hours=6),
            )
            check("backfilled history is stored but not auto-replied to",
                  not CRMMessage.objects.filter(conversation=conv, is_ai=True).exists())

            print("\n=== 9. ai_status drives both the gate and the badge ===")
            reset()
            conv.status = 'open'
            conv.ai_enabled = True
            conv.assigned_to = None
            conv.last_human_reply_at = None
            conv.save()

            state = conv.ai_status(integration=integ)
            check("live conversation reports 'on'", state['state'] == 'on', str(state))

            conv.ai_enabled = False
            conv.save(update_fields=['ai_enabled'])
            off = conv.ai_status(integration=integ)
            check("toggle off reports 'off'", off['state'] == 'off', str(off))
            check("'off' is resumable from the chat", off['resumable'] is True)
            conv.ai_enabled = True
            conv.save(update_fields=['ai_enabled'])

            # The regression this whole section exists for: the badge used to read
            # "AI On" during the takeover pause, because it only looked at the flag.
            conv.last_human_reply_at = timezone.now() - timedelta(
                minutes=HUMAN_TAKEOVER_MINUTES - 2)
            conv.save(update_fields=['last_human_reply_at'])
            paused = conv.ai_status(integration=integ)
            check("takeover pause reports 'paused', not 'on'",
                  paused['state'] == 'paused', str(paused))
            check("paused state exposes a resume instant", bool(paused['resumes_at']))
            check("countdown is within the takeover window",
                  0 < paused['resumes_in'] <= HUMAN_TAKEOVER_MINUTES * 60,
                  f"resumes_in={paused['resumes_in']}")
            check("paused-by-human is resumable", paused['resumable'] is True)

            # resumes_at must not drift between reads, or the polled fragment would
            # differ on every fetch and the inbox would rebuild the thread each time.
            check("resumes_at is stable across reads",
                  conv.ai_status(integration=integ)['resumes_at'] == paused['resumes_at'])

            # And the gate must agree with the badge, since they now share one read.
            check("the gate skips exactly when the badge says paused", not replied())

            conv.last_human_reply_at = None
            conv.save(update_fields=['last_human_reply_at'])

            bot.is_active = False
            bot.save(update_fields=['is_active'])
            integ.refresh_from_db()
            unavailable = conv.ai_status(integration=integ)
            check("inactive chatbot reports 'unavailable'",
                  unavailable['state'] == 'unavailable', str(unavailable))
            check("'unavailable' is not resumable from the chat",
                  unavailable['resumable'] is False)
            bot.is_active = True
            bot.save(update_fields=['is_active'])
            integ.refresh_from_db()

            print("\n=== 10. A silent bot leaves a visible trace ===")
            reset()
            router.result.update({'success': False, 'reply': None,
                                  'error': 'AI provider was overloaded.'})
            process_incoming_webhook_message(integ, contact, conv, "are you there?")
            sys_msgs = CRMMessage.objects.filter(conversation=conv, is_system=True)
            check("a failed AI call posts a system chip", sys_msgs.count() == 1,
                  f"got {sys_msgs.count()}")
            chip = sys_msgs.first()
            check("the chip is flagged as a warning",
                  bool(chip) and chip.system_level == 'warning')
            check("the chip quotes the provider reason",
                  bool(chip) and 'overloaded' in chip.body)
            check("the chip is never sent to the customer",
                  bool(chip) and not chip.is_outbound and len(fake_send.sent) == 0)
            check("the chip is not mistaken for an AI reply",
                  bool(chip) and not chip.is_ai)
            router.result.update({'success': True, 'error': None,
                                  'reply': 'Namaste! How can I help?'})

            print("\n=== 11. AI replies carry their intent into the thread ===")
            reset()
            router.result['intent'] = 'purchase_intent'
            process_incoming_webhook_message(integ, contact, conv, "How much is the hoodie?")
            ai_msg = CRMMessage.objects.filter(conversation=conv, is_ai=True).first()
            check("the reply stores the classified intent",
                  bool(ai_msg) and ai_msg.ai_intent == 'purchase_intent',
                  f"got {getattr(ai_msg, 'ai_intent', None)!r}")
            check("intent renders as a chip label",
                  bool(ai_msg) and ai_msg.intent_label == 'Purchase')
            check("purchase intent is high priority",
                  bool(ai_msg) and ai_msg.intent_priority == 'high')
            router.result['intent'] = 'general_query'

            print("\n=== 12. Take over / hand back ===")
            reset()
            from accounts.models import CustomUser
            from django.test import RequestFactory
            agent = CustomUser.objects.first()
            if not agent:
                print("  SKIP  no user exists to test take-over")
            else:
                conv.assigned_to = None
                conv.ai_enabled = True
                conv.last_human_reply_at = None
                conv.save()
                check("bot owns the chat before take-over",
                      conv.ai_status(integration=integ)['state'] == 'on')

                rf = RequestFactory()
                req = rf.post(f'/trendy-crm/conversations/{conv.pk}/take-over/')
                req.user = agent
                resp = crm_views.crm_take_over_conversation(req, conv.pk)
                check("take-over returns ok", resp.status_code == 200)
                conv.refresh_from_db()
                state = conv.ai_status(integration=integ)
                check("take-over claims the conversation", conv.assigned_to_id == agent.pk)
                check("take-over pauses the AI", state['state'] == 'paused', str(state))
                check("a paused-by-takeover chat can be handed back",
                      state['resumable'] is True)
                check("take-over is recorded in the thread",
                      CRMMessage.objects.filter(conversation=conv, is_system=True,
                                                body__contains='took over').exists())
                check("the AI stays silent after a take-over", not replied())

                # Hand back: the claim must be released too, or ai_status would
                # keep reporting 'paused' and the toggle would appear to do nothing.
                req = rf.post(f'/trendy-crm/conversations/{conv.pk}/toggle-ai/',
                              {'action': 'resume'})
                req.user = agent
                crm_views.crm_toggle_conversation_ai(req, conv.pk)
                conv.refresh_from_db()
                back = conv.ai_status(integration=integ)
                check("hand-back releases the assignment", conv.assigned_to_id is None)
                check("hand-back returns the chat to the AI",
                      back['state'] == 'on', str(back))
                reset()
                process_incoming_webhook_message(integ, contact, conv, "hello again")
                check("the AI replies again after hand-back", replied())

            raise _Rollback()

    except _Rollback:
        pass
    finally:
        ai_router.route_message = orig_router
        meta_sync.send_meta_message = orig_send
        crm_views.charge_ai_credits = orig_charge

    print("\n" + "=" * 60)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\n  Failures:")
        for f in FAIL:
            print(f"    - {f}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
