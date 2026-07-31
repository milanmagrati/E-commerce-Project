"""
Verification script for the Trendy CRM Chatbot pages (chatbot list + chatbot detail).

Exercises the bugs fixed in this pass:
  1. Business cards no longer render the literal string "None" for an empty about_blurb.
  2. Every channel type resolves to a real Font Awesome class (prefix + icon).
  3. Master AI toggle applies the *requested* state instead of blind-flipping.
  4. Support email / phone can be cleared once set; a bad email is rejected.
  5. Knowledge Base sections (tone, offerings, FAQs, playbook) reach the AI prompt.
  6. AI credit usage is deducted and written to the credit history.
  7. Creating businesses back-to-back after a delete does not reuse a name.

Run:  python test_crm_chatbot_fixes.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

from trendycrm.models import CRMChatbotConfig, CRMIntegration, CRMCreditLog  # noqa: E402
from trendycrm.views import INTEGRATION_META, channel_meta, charge_ai_credits  # noqa: E402
from trendycrm.ai_router import build_dynamic_prompt  # noqa: E402

# Standalone run (not the test runner), so the dev ALLOWED_HOSTS applies.
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

PASS, FAIL = [], []


def check(name, condition, detail=''):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -> {detail}" if detail and not condition else ''))


def get_admin_client():
    User = get_user_model()
    user = User.objects.filter(is_superuser=True).first()
    if not user:
        raise SystemExit("No superuser found — create one with `python manage.py createsuperuser`.")
    c = Client()
    c.force_login(user)
    return c, user


def main():
    client, user = get_admin_client()

    bot = CRMChatbotConfig.objects.create(
        name='ZZ Verify AI', business_name='ZZ Verify Co', is_active=False,
        business_email='old@example.com', business_phone='+9779800000000',
    )
    print(f"\nUsing throwaway business #{bot.pk}\n")

    try:
        # ── 1. List page: no literal "None" description ───────────────────────
        print("1) Business list rendering")
        resp = client.get('/trendy-crm/chatbots/')
        html = resp.content.decode('utf-8', 'replace')
        check('list page returns 200', resp.status_code == 200, resp.status_code)
        check('no literal "None" description rendered',
              '>None<' not in html and 'cb-desc">\n                None' not in html)
        check('shows the fallback copy for an empty knowledge base',
              'No business description set yet' in html)
        check('card counts are labelled "assigned", not "connected"',
              'channels assigned' in html or 'No channels assigned' in html)
        check('no broken fab class for the mail channels',
              'fab fa-gmail' not in html and 'fab fa-zoho_mail' not in html and 'fab fa-outlook' not in html)

        # ── 2. Channel icon metadata ─────────────────────────────────────────
        print("\n2) Channel icon metadata")
        for ct, _label in CRMIntegration.CHANNEL_TYPE_CHOICES:
            meta = channel_meta(ct)
            check(f'{ct}: has a prefix + icon + colour',
                  bool(meta.get('prefix')) and bool(meta.get('icon')) and bool(meta.get('color')),
                  meta)
        check('mail channels use solid (fas) glyphs, not brand',
              all(INTEGRATION_META[k]['prefix'] == 'fas' for k in ('gmail', 'outlook', 'zoho_mail')))
        check('unknown channel type still gets a visible icon',
              channel_meta('carrier_pigeon')['icon'] == 'fa-plug')

        # ── 3. Master toggle applies the requested state ──────────────────────
        print("\n3) Master AI toggle")
        url = f'/trendy-crm/chatbot/{bot.pk}/toggle/'
        r = client.post(url, {'enabled': 'true'})
        bot.refresh_from_db()
        check('enabled=true turns the AI on', r.json()['is_active'] is True and bot.is_active is True)
        r = client.post(url, {'enabled': 'true'})
        bot.refresh_from_db()
        check('repeating enabled=true is idempotent (does not flip back off)',
              r.json()['is_active'] is True and bot.is_active is True)
        r = client.post(url, {'enabled': 'false'})
        bot.refresh_from_db()
        check('enabled=false pauses the AI', r.json()['is_active'] is False and bot.is_active is False)
        r = client.post(url, {})
        bot.refresh_from_db()
        check('legacy call with no state still flips', bot.is_active is True)

        # ── 4. Knowledge Base saving ─────────────────────────────────────────
        print("\n4) Knowledge Base save")
        save_url = f'/trendy-crm/chatbot/{bot.pk}/save-knowledge/'
        payload = {
            'business_name': 'ZZ Verify Co',
            'business_email': '',
            'business_phone': '',
            'about_blurb': 'We sell verification widgets.',
            'welcome_message': 'Hi there!',
            'tone_voice': 'Warm and concise. Never use corporate jargon.',
            'offerings': '1. Widget A (NPR 500)\n2. Widget B (NPR 900)',
            'faq_text': 'Q: Do you deliver?\nA: Yes, nationwide in 3 days.',
            'playbook': 'ANGRY CUSTOMER: apologize first, never argue.',
        }
        r = client.post(save_url, payload, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        bot.refresh_from_db()
        check('save returns ok', r.status_code == 200 and r.json().get('status') == 'ok', r.content[:200])
        check('support email can be cleared', bot.business_email is None, repr(bot.business_email))
        check('support phone can be cleared', bot.business_phone is None, repr(bot.business_phone))
        check('all five knowledge sections persisted',
              all([bot.about_blurb, bot.tone_voice, bot.offerings, bot.faq_text, bot.playbook]))

        bad = dict(payload, business_email='not-an-email')
        r = client.post(save_url, bad, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        bot.refresh_from_db()
        check('invalid email is rejected with 400', r.status_code == 400, r.status_code)
        check('invalid email is not persisted', bot.business_email is None, repr(bot.business_email))

        # ── 5. Knowledge reaches the AI prompt ───────────────────────────────
        print("\n5) Knowledge Base reaches the AI system prompt")
        prompt = build_dynamic_prompt(bot, page_profile=None, intent='general_query')
        check('offerings are in the prompt', 'Widget A' in prompt)
        check('FAQs are in the prompt', 'nationwide in 3 days' in prompt)
        check('brand voice is in the prompt', 'corporate jargon' in prompt)
        check('playbook is in the prompt', 'apologize first' in prompt)
        check('about blurb is in the prompt', 'verification widgets' in prompt)
        check('house tone from the dropdown is in the prompt', 'HOUSE TONE' in prompt)

        bot.primary_language = 'ne'
        bot.save(update_fields=['primary_language'])
        ne_prompt = build_dynamic_prompt(bot, page_profile=None, intent='general_query')
        check('Nepali setting asks for romanized Nepali', 'romanized Nepali' in ne_prompt)

        # ── 6. AI credits ────────────────────────────────────────────────────
        print("\n6) AI credit tracking")
        start = bot.ai_credits
        charge_ai_credits(bot, action='manual_test', model_used='gemini-flash-latest', description='verify run')
        bot.refresh_from_db()
        check('balance decremented by 1', bot.ai_credits == start - 1, f'{start} → {bot.ai_credits}')
        log = CRMCreditLog.objects.filter(chatbot_config=bot).first()
        check('a credit log row was written', log is not None)
        check('usage is recorded as negative', log is not None and log.credits_used == -1)

        r = client.get(f'/trendy-crm/chatbot/{bot.pk}/credit-history/',
                       HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        data = r.json()
        check('credit history endpoint returns the entry',
              r.status_code == 200 and len(data['logs']) == 1 and data['balance'] == bot.ai_credits)
        check('history rows carry a formatted timestamp',
              bool(data['logs'][0]['created_at']) and data['logs'][0]['created_at'] != '—')

        # ── 7. Detail page renders ───────────────────────────────────────────
        print("\n7) Chatbot detail page")
        resp = client.get(f'/trendy-crm/chatbot/{bot.pk}/')
        html = resp.content.decode('utf-8', 'replace')
        check('detail page returns 200', resp.status_code == 200, resp.status_code)
        check('dead "AI Model" select is gone', 'name="ai_model"' not in html)
        check('Model Routing selects are present',
              'name="text_provider"' in html and 'name="image_provider"' in html)
        check('credit bar is rendered', 'id="creditBalance"' in html)
        check('credit history modal is wired', 'openCreditHistory()' in html)
        check('Apply Changes awaits both saves', 'Promise.all' in html)
        check('toggle posts the desired state', "fd.append('enabled'" in html)
        check('no broken fab class for the mail channels',
              'fab fa-gmail' not in html and 'fab fa-zoho_mail' not in html and 'fab fa-outlook' not in html)

        # ── 7b. Router: intent parsing + response length ──────────────────────
        # Both were silently degrading every AI reply: the classifier was capped at
        # 20 tokens (Gemini spends those on hidden reasoning, so it returned a
        # fragment and everything fell back to general_query), and response_length
        # was used as a hard token cut-off, truncating replies mid-word.
        print("\n7b) AI router robustness (offline)")
        from trendycrm import ai_router  # noqa: E402

        original_call = ai_router._call_gemini_rest
        canned = {}
        ai_router._call_gemini_rest = lambda *a, **k: canned['text']
        try:
            for raw, expected in [
                ('purchase_intent', 'purchase_intent'),
                ('  Purchase_Intent  ', 'purchase_intent'),
                ('Category: product_issue.', 'product_issue'),
                ('purchase intent', 'purchase_intent'),
                ('"spam noise"', 'spam_noise'),
                ('general query', 'general_query'),
                ('total gibberish', 'general_query'),
            ]:
                canned['text'] = raw
                got = ai_router.classify_intent('x', {'gemini_api_key': 'k'})
                check(f'classifier parses {raw!r} -> {expected}', got == expected, got)
        finally:
            ai_router._call_gemini_rest = original_call

        for stored, floor in (('100', 400), ('500', 900), ('1500', 2200)):
            bot.response_length = stored
            tokens, instruction = ai_router._length_profile(bot)
            check(f'response_length {stored} gets {floor} tokens + a style instruction',
                  tokens == floor and bool(instruction), (tokens, instruction))
        check('a blank creativity_level does not blow up',
              ai_router._temperature(type('C', (), {'creativity_level': ''})()) == 0.7)

        # ── 8. Unique default business names ─────────────────────────────────
        print("\n8) New business naming")
        created = []
        for _ in range(3):
            r = client.post('/trendy-crm/chatbot/create/')
            new_id = int(r.url.rstrip('/').split('/')[-1])
            created.append(CRMChatbotConfig.objects.get(pk=new_id))
        names = [b.business_name for b in created]
        check('three fresh businesses get distinct names', len(set(names)) == 3, names)

        middle = created[1]
        middle_name = middle.business_name
        middle.delete()
        r = client.post('/trendy-crm/chatbot/create/')
        replacement = CRMChatbotConfig.objects.get(pk=int(r.url.rstrip('/').split('/')[-1]))
        created = [created[0], created[2], replacement]
        check('after deleting one, the next create does not duplicate a live name',
              CRMChatbotConfig.objects.filter(business_name=replacement.business_name).count() == 1,
              f'freed {middle_name!r}, got {replacement.business_name!r}')

        for b in created:
            b.delete()

    finally:
        CRMCreditLog.objects.filter(chatbot_config=bot).delete()
        bot.delete()
        print(f"\nCleaned up throwaway business #{bot.pk}")

    print(f"\n{'=' * 60}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == '__main__':
    main()
