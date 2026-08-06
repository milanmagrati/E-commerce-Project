"""
Verifies the operator-defined Message Triage Rules in Trendy CRM.

The message types, their chip labels, their priorities, the keywords that
identify them and whether they pull an agent in are all editable per business
from the Chatbot page. This checks that editing them actually changes what the
classifier, the thread chips and the alerts do — and that a business which never
touched the panel behaves exactly as it did before the panel existed.

    python test_crm_triage_rules.py

Touches the real database and cleans up everything it creates. Never spends an
LLM call (the CRM's Gemini key has a very low free-tier quota) — the router's
paid calls are monkeypatched to raise, so reaching one fails the run.
"""
import json
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from trendycrm import ai_router, meta_sync, triage  # noqa: E402
from trendycrm.models import (  # noqa: E402
    CRMChatbotConfig, CRMContact, CRMConversation, CRMIntegration, CRMMessage,
)
from accounts.models import CustomUser  # noqa: E402

FAILURES = []


def check(label, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ''))
    if not condition:
        FAILURES.append(label)


def send(conv, contact, body, external_id):
    return meta_sync.upsert_inbound_message(
        integration=conv.integration, contact=contact, conversation=conv,
        body=body, external_id=external_id, sender_name=contact.name,
        is_outbound=False, created_at=timezone.now(),
    )


def rule(rules, key):
    return triage.rule_by_key(rules, key)


def main():
    created = []

    bot = CRMChatbotConfig.objects.create(name='[test] Triage Bot', business_name='[test] Triage Co')
    created.append(bot)
    other_bot = CRMChatbotConfig.objects.create(name='[test] Other Bot', business_name='[test] Other Co')
    created.append(other_bot)

    contact = CRMContact.objects.create(name='[test] Triage Customer')
    created.append(contact)
    integration = CRMIntegration.objects.create(
        channel_type='facebook', account_name='[test] Triage Page (997)',
        status='connected', chatbot_config=bot)
    created.append(integration)
    other_integration = CRMIntegration.objects.create(
        channel_type='facebook', account_name='[test] Other Page (996)',
        status='connected', chatbot_config=other_bot)
    created.append(other_integration)

    conv = CRMConversation.objects.create(
        contact=contact, integration=integration, channel='facebook', ai_enabled=False)
    created.append(conv)
    other_conv = CRMConversation.objects.create(
        contact=contact, integration=other_integration, channel='facebook', ai_enabled=False)
    created.append(other_conv)

    user = CustomUser.objects.filter(is_superuser=True).first()
    temp_user = None
    if not user:
        temp_user = user = CustomUser.objects.create_superuser(
            username='[test]triageagent', email='t3@example.com', password='x')
        created.append(temp_user)

    def _forbidden(*a, **kw):
        raise AssertionError("triage must never call an LLM")
    real_gemini = ai_router._call_gemini_rest
    real_openai = ai_router._call_openai_chat
    ai_router._call_gemini_rest = _forbidden
    ai_router._call_openai_chat = _forbidden

    client = Client()
    client.force_login(user)
    save_url = f'/trendy-crm/chatbot/{bot.pk}/save-triage/'
    preview_url = f'/trendy-crm/chatbot/{bot.pk}/triage-preview/'

    try:
        print("\n1. A business that never opened the panel behaves exactly as before")
        check("no rules are stored yet", bot.triage_rules == [], str(bot.triage_rules))
        check("but it still resolves to the built-in set",
              len(triage.rules_for(bot)) == len(triage.DEFAULT_RULES))
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
        check("the built-in display map still matches the rules",
              CRMMessage.INTENT_DISPLAY == triage.display_map(triage.DEFAULT_RULES))

        print("\n2. A message type the operator invented classifies and chips")
        rules = triage.default_rules()
        rules.insert(0, {
            'key': 'wholesale', 'label': 'Wholesale', 'priority': 'urgent',
            'match': 'contains', 'keywords': ['bulk order', 'wholesale rate'],
            'alert': True, 'instruction': 'Ask how many units they need.',
            'enabled': True, 'locked': False,
        })
        resp = client.post(save_url, {'triage_rules': json.dumps(rules)},
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("the panel saves the rule set", resp.status_code == 200, str(resp.status_code))
        bot.refresh_from_db()
        check("the custom rule is stored",
              rule(bot.triage_rules, 'wholesale') is not None)

        msg = send(conv, contact, "hi, i want a bulk order of 50", "ext-tri-1")
        check("it classifies as the custom type", msg.ai_intent == 'wholesale', msg.ai_intent)
        msg.apply_triage(triage.display_map(triage.rules_for(bot)))
        check("the chip reads Wholesale", msg.intent_label == 'Wholesale', msg.intent_label)
        check("at urgent priority", msg.intent_priority == 'urgent', msg.intent_priority)

        alert = CRMMessage.open_event(conv, CRMMessage.alert_kind_for('wholesale'))
        check("it raises an alert chip of its own kind", alert is not None)
        check("the chip renders as a custom alert", bool(alert and alert.is_custom_alert))
        check("the conversation is auto-labelled",
              conv.labels.filter(name='🔔 Wholesale').exists())

        print("\n3. The alert reaches the cross-page poller with its own wording")
        payload = client.get('/trendy-crm/alerts/poll/').json()
        entry = next((a for a in payload['alerts'] if a['message_id'] == alert.pk), None)
        check("the custom alert is polled", entry is not None)
        check("it carries the rule's label as the popup title",
              bool(entry) and entry['title'] == 'Wholesale', str(entry))
        check("and a bell icon rather than the lead flame",
              bool(entry) and entry['icon'] == 'fa-bell', str(entry))

        print("\n4. The thread renders the custom chip and the urgent dot")
        thread = client.get(f'/trendy-crm/conversations/ajax/?id={conv.pk}').content.decode()
        check("the thread renders", 'Wholesale' in thread)
        check("urgent gets its own dot colour", 'dot-urgent' in thread)
        check("the priority word is the rule's, not a fixed one", 'Urgent' in thread)

        print("\n5. Renaming and re-prioritising needs no re-analysis")
        renamed = triage.rules_for(bot)
        wholesale = rule(renamed, 'wholesale')
        wholesale['label'] = 'Bulk Buyer'
        wholesale['priority'] = 'medium'
        resp = client.post(save_url, {'triage_rules': json.dumps(renamed)},
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("the rename saves", resp.status_code == 200, str(resp.status_code))
        bot.refresh_from_db()
        stored = CRMMessage.objects.get(pk=msg.pk)
        check("the stored intent is untouched", stored.ai_intent == 'wholesale', stored.ai_intent)
        stored.apply_triage(triage.display_map(triage.rules_for(bot)))
        check("but the chip already reads the new name",
              stored.intent_label == 'Bulk Buyer', stored.intent_label)
        check("and the new priority", stored.intent_priority == 'medium', stored.intent_priority)

        print("\n6. Each business labels by its own rules")
        other_rules = triage.default_rules()
        rule(other_rules, 'purchase_intent')['label'] = 'Hot Lead'
        other_bot.triage_rules = triage.normalize_rules(other_rules)
        other_bot.save(update_fields=['triage_rules'])

        mine = send(conv, contact, "how much is this", "ext-tri-2")
        theirs = send(other_conv, contact, "how much is this", "ext-tri-3")
        mine.apply_triage(triage.display_map(triage.rules_for_conversation(conv)))
        theirs.apply_triage(triage.display_map(triage.rules_for_conversation(other_conv)))
        check("same message, same intent on both",
              mine.ai_intent == theirs.ai_intent == 'purchase_intent')
        check("this business calls it Purchase", mine.intent_label == 'Purchase', mine.intent_label)
        check("the other one calls it Hot Lead", theirs.intent_label == 'Hot Lead', theirs.intent_label)

        print("\n7. The alert switch decides who gets pulled in, not the priority")
        quiet = triage.rules_for(bot)
        rule(quiet, 'purchase_intent')['alert'] = False
        bot.triage_rules = triage.normalize_rules(quiet)
        bot.save(update_fields=['triage_rules'])
        CRMMessage.objects.filter(conversation=conv, is_system=True).delete()

        buy = send(conv, contact, "i want to buy two of these", "ext-tri-4")
        check("the message is still a high-priority purchase",
              buy.ai_intent == 'purchase_intent' and buy.intent_priority == 'high',
              f"{buy.ai_intent} / {buy.intent_priority}")
        check("but no lead chip is raised",
              CRMMessage.open_event(conv, CRMMessage.EVENT_LEAD_DETECTED) is None)

        print("\n8. Ordering is the classifier — the first match wins")
        reordered = triage.rules_for(bot)
        delivery = rule(reordered, 'delivery_query')
        delivery['keywords'] = delivery['keywords'] + ['delivery charge kati ho']
        reordered.remove(delivery)
        reordered.insert(0, delivery)
        check("delivery-first reads a mixed message as delivery",
              triage.classify("delivery charge kati ho", reordered)[0] == 'delivery_query',
              triage.classify("delivery charge kati ho", reordered)[0])
        reordered.remove(delivery)
        reordered.insert(reordered.index(rule(reordered, 'order_status')), delivery)
        check("purchase-first reads it as a buying signal",
              triage.classify("delivery charge kati ho", reordered)[0] == 'purchase_intent',
              triage.classify("delivery charge kati ho", reordered)[0])

        print("\n9. A rule set that would misbehave is refused, with the reason")
        bad_cases = [
            ('a duplicate id', [dict(r) for r in triage.default_rules()]
                               + [{'key': 'greeting', 'label': 'Greeting 2', 'priority': 'low',
                                   'match': 'exact', 'keywords': ['yo yo']}]),
            ('an unknown priority', [dict(r, priority='immediately') if r['key'] == 'greeting' else r
                                     for r in triage.default_rules()]),
            ('a 2-character substring keyword',
             [dict(r, match='contains', keywords=['hi']) if r['key'] == 'greeting' else r
              for r in triage.default_rules()]),
            ('a deleted fallback bucket',
             [r for r in triage.default_rules() if r['key'] != 'general_query']),
            ('a rule with no label',
             [dict(r, label='') if r['key'] == 'greeting' else r for r in triage.default_rules()]),
        ]
        for name, payload_rules in bad_cases:
            resp = client.post(save_url, {'triage_rules': json.dumps(payload_rules)},
                               HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            body = resp.json() if resp['Content-Type'].startswith('application/json') else {}
            check(f"{name} is refused with a reason",
                  resp.status_code == 400 and bool(body.get('message')),
                  f"{resp.status_code}: {body.get('message')}")

        print("\n10. Try it explains where a message landed")
        resp = client.post(preview_url, {
            'message': 'where is my order',
            'triage_rules': json.dumps(triage.default_rules()),
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        check("the preview responds", resp.status_code == 200, str(resp.status_code))
        data = resp.json()
        check("it names the type", data.get('label') == 'Order Status', str(data))
        check("with its priority", data.get('priority') == 'medium', str(data))
        check("and the keyword that matched",
              data.get('matched_keyword') == 'where is my order', str(data))

        resp = client.post(preview_url, {
            'message': 'is the blue one nicer than the red one',
            'triage_rules': json.dumps(triage.default_rules()),
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        data = resp.json()
        check("an unmatched message reports the fallback",
              data.get('key') == 'general_query' and data.get('matched_keyword') == '', str(data))

        print("\n11. The AI is told about the rules it is answering under")
        bot.refresh_from_db()
        custom = triage.rules_for(bot)
        rule(custom, 'greeting')['instruction'] = 'Reply only with "Namaste!" and nothing else.'
        bot.triage_rules = triage.normalize_rules(custom)
        bot.save(update_fields=['triage_rules'])
        prompt = ai_router.build_dynamic_prompt(bot, None, 'greeting')
        check("the operator's instruction reaches the prompt",
              'Namaste!" and nothing else' in prompt)
        check("it replaces the built-in greeting mode",
              'Do NOT list products, prices, or links yet' not in prompt)

        prompt = ai_router.build_dynamic_prompt(bot, None, 'wholesale')
        check("a custom type steers the reply too",
              'Ask how many units they need' in prompt, prompt[-200:])

        prompt = ai_router.build_dynamic_prompt(bot, None, 'product_issue')
        check("a type left alone keeps its built-in mode",
              'SUPPORT MODE ACTIVE' in prompt)

    finally:
        ai_router._call_gemini_rest = real_gemini
        ai_router._call_openai_chat = real_openai
        for conversation in (conv, other_conv):
            CRMMessage.objects.filter(conversation=conversation).delete()
        for obj in reversed(created):
            try:
                obj.delete()
            except Exception as e:
                print(f"  (cleanup skipped {obj!r}: {e})")
        # The auto-applied label is shared taxonomy like any other CRMLabel, but
        # this one is named after a rule only this test ever created.
        from trendycrm.models import CRMLabel
        CRMLabel.objects.filter(name='🔔 Wholesale').delete()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): " + '; '.join(FAILURES))
    else:
        print("All checks passed.")
    return 1 if FAILURES else 0


if __name__ == '__main__':
    raise SystemExit(main())
