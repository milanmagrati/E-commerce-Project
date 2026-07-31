"""
Verifies the new optional Identity fields on the Business Knowledge Base:
address, cities served, and the Facebook/Instagram/TikTok/WhatsApp handles.

Checks the whole round trip — POST to the save endpoint -> stored on
CRMChatbotConfig -> injected into the AI system prompt -> rendered back into the
Chatbot page — because a knowledge field that saves but never reaches the model
is just a text box that does nothing.

Run: python test_chatbot_identity_fields.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.conf import settings
from django.db import transaction
from django.test import Client
from django.urls import reverse

# The Django test client talks to 'testserver', which this project's real
# ALLOWED_HOSTS doesn't include.
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from accounts.models import CustomUser
from trendycrm.ai_router import build_dynamic_prompt
from trendycrm.models import CRMChatbotConfig
from trendycrm.views import _parse_city_list

results = []


def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


print("\n" + "=" * 70)
print("BUSINESS KNOWLEDGE BASE — IDENTITY FIELDS")
print("=" * 70)

try:
    with transaction.atomic():
        user = CustomUser.objects.create_user(
            username='kb_field_probe', password='x', role='administrator', is_superuser=True,
        )
        bot = CRMChatbotConfig.objects.create(name='Probe Bot', business_name='Trendy Shop')

        client = Client()
        client.force_login(user)
        save_url = reverse('trendycrm:chatbot_save_knowledge', args=[bot.pk])

        base_post = {
            'business_name': 'Trendy Shop',
            'business_email': 'support@trendy.com',
            'business_phone': '+977 9800000000',
            'about_blurb': 'We sell skincare.',
            'welcome_message': 'Hi!',
        }

        # ── 1. All new fields save ─────────────────────────────────────────────
        print("\n1) New Identity fields save")
        resp = client.post(save_url, dict(base_post, **{
            'business_address': 'Putalisadak, Kathmandu 44600',
            'cities_served': '["Kathmandu", "Lalitpur", "Pokhara"]',
            'social_facebook': 'facebook.com/trendyshop',
            'social_instagram': 'instagram.com/trendyshop',
            'social_tiktok': 'tiktok.com/@trendyshop',
            'social_whatsapp': '+977 9812345678',
        }), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        bot.refresh_from_db()
        check("save endpoint returns ok", resp.status_code == 200 and resp.json().get('status') == 'ok')
        check("address stored", bot.business_address == 'Putalisadak, Kathmandu 44600')
        check("cities stored as a list", bot.cities_served == ['Kathmandu', 'Lalitpur', 'Pokhara'])
        check("facebook stored", bot.social_facebook == 'facebook.com/trendyshop')
        check("instagram stored", bot.social_instagram == 'instagram.com/trendyshop')
        check("tiktok stored", bot.social_tiktok == 'tiktok.com/@trendyshop')
        check("whatsapp stored", bot.social_whatsapp == '+977 9812345678')

        # ── 2. The AI actually receives them ───────────────────────────────────
        print("\n2) Fields reach the AI system prompt")
        prompt = build_dynamic_prompt(bot)
        check("address is in the prompt", 'Putalisadak, Kathmandu 44600' in prompt)
        check("every city is in the prompt", all(c in prompt for c in ('Kathmandu', 'Lalitpur', 'Pokhara')))
        check("uncovered areas are guarded against", "don't currently cover it" in prompt)
        check("socials are in the prompt", all(
            s in prompt for s in ('facebook.com/trendyshop', 'instagram.com/trendyshop',
                                  'tiktok.com/@trendyshop', '+977 9812345678')))

        # ── 3. Every field is optional ─────────────────────────────────────────
        print("\n3) All new fields are optional")
        resp = client.post(save_url, base_post, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        bot.refresh_from_db()
        check("saves with all new fields blank", resp.status_code == 200)
        check("address cleared", bot.business_address is None)
        check("cities cleared to empty list", bot.cities_served == [])
        check("socials cleared", not any([bot.social_facebook, bot.social_instagram,
                                          bot.social_tiktok, bot.social_whatsapp]))
        blank_prompt = build_dynamic_prompt(bot)
        check("prompt omits empty sections", 'Business address' not in blank_prompt
              and 'Cities/areas we serve' not in blank_prompt
              and 'Our official channels' not in blank_prompt)

        # ── 4. Existing Identity fields still work ─────────────────────────────
        print("\n4) Existing fields are unaffected")
        check("business name kept", bot.business_name == 'Trendy Shop')
        check("support email kept", bot.business_email == 'support@trendy.com')
        check("about blurb kept", bot.about_blurb == 'We sell skincare.')

        # ── 5. City parsing is forgiving ───────────────────────────────────────
        print("\n5) Cities parse from JSON or plain text")
        check("parses a JSON array", _parse_city_list('["Kathmandu","Pokhara"]') == ['Kathmandu', 'Pokhara'])
        check("parses comma-separated fallback (no JS)",
              _parse_city_list('Kathmandu, Pokhara , Butwal') == ['Kathmandu', 'Pokhara', 'Butwal'])
        check("drops duplicates case-insensitively",
              _parse_city_list('["Kathmandu","kathmandu","KATHMANDU"]') == ['Kathmandu'])
        check("drops blanks", _parse_city_list('["Kathmandu", "", "  "]') == ['Kathmandu'])
        check("empty input is an empty list", _parse_city_list('') == [] and _parse_city_list(None) == [])
        check("malformed JSON doesn't explode", _parse_city_list('["Kathmandu"') == ['["Kathmandu"'])

        # ── 6. The form renders the saved values back ──────────────────────────
        print("\n6) Chatbot page renders the fields")
        bot.business_address = 'Putalisadak, Kathmandu'
        bot.cities_served = ['Kathmandu', 'Pokhara']
        bot.social_tiktok = 'tiktok.com/@trendyshop'
        bot.save()
        html = client.get(reverse('trendycrm:chatbot', args=[bot.pk])).content.decode()
        check("address input rendered", 'name="business_address"' in html and 'Putalisadak, Kathmandu' in html)
        check("city tag input rendered", 'id="cityTagContainer"' in html and 'Add city and press Enter' in html)
        check("saved cities rendered as tags", 'data-val="Pokhara"' in html)
        check("hidden city field seeded for non-JS submit", 'id="citiesServedHidden"' in html)
        check("all four social inputs rendered", all(
            f'name="social_{s}"' in html for s in ('facebook', 'instagram', 'tiktok', 'whatsapp')))

        transaction.set_rollback(True)

except Exception:
    import traceback
    traceback.print_exc()
    results.append(False)

print("\n" + "=" * 70)
print(f"RESULT: {sum(results)}/{len(results)} checks passed")
print("=" * 70)
