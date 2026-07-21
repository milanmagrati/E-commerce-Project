#!/usr/bin/env python
"""
Standalone verification for the Comment Automation / Comment-to-DM flow.

Exercises the real models against the DB (rolled back at the end so nothing is
persisted) with the Meta Graph API network calls monkeypatched, proving:

  1. A matching CommentAutomation fires a public reply AND a private DM.
  2. The public reply is stored locally with the page's name and the real FB
     reply id (so a later sync won't re-import it as a new top-level comment).
  3. The failed-reply path is reported as a failure (regression guard for the
     tuple-truthiness bug where (False, None) was treated as success).
  4. The Facebook 'feed' webhook handler routes a new comment through the
     automation engine.

Run:  python test_comment_automation.py
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.db import transaction
from django.utils import timezone

import trendycrm.meta_sync as meta_sync
import trendycrm.views as crm_views
from trendycrm.models import (
    CRMIntegration, CRMSocialPost, CRMSocialComment,
    CommentAutomation, CRMMessage,
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(label, cond):
    results.append(cond)
    print(f"  [{PASS if cond else FAIL}] {label}")


# ── Network stubs ──────────────────────────────────────────────────────────────
sent_public_replies = []
sent_dms = []
sent_send_api = []
reply_should_succeed = {'value': True}
private_dm_should_succeed = {'value': True}


def fake_reply(integration, comment_id, message_text):
    sent_public_replies.append((comment_id, message_text))
    if reply_should_succeed['value']:
        return True, f"fbreply_{comment_id}_{len(sent_public_replies)}"
    return False, None


def fake_private_reply(integration, comment_id, message_text):
    # New contract: returns (success, error). send_comment_dm() unpacks this.
    if private_dm_should_succeed['value']:
        sent_dms.append((comment_id, message_text))
        return True, None
    return False, '[10903] You cannot reply to this comment.'


def fake_send_meta_message(integration, recipient_id, message_text):
    # Stub for the Send-API fallback path inside send_comment_dm().
    sent_send_api.append((recipient_id, message_text))
    return True


# Patch the names imported into views._check_comment_automations
meta_sync.reply_to_meta_comment = fake_reply
meta_sync.reply_to_meta_comment_privately = fake_private_reply
meta_sync.send_meta_message = fake_send_meta_message
crm_views.reply_to_meta_comment = fake_reply
# Keep sync from hitting the network inside the webhook handler
meta_sync.sync_meta_posts = lambda integration: None


print("\n" + "=" * 70)
print("COMMENT AUTOMATION VERIFICATION")
print("=" * 70)

try:
    with transaction.atomic():
        integ = CRMIntegration.objects.create(
            channel_type='facebook', status='connected',
            account_name='Routine Of Karma Production (123456789)',
            access_token='TESTTOKEN',
        )
        post = CRMSocialPost.objects.create(
            integration=integ, meta_post_id='123456789_987654321',
            message='International airport side view pokhara',
            created_time=timezone.now(),
        )
        comment = CRMSocialComment.objects.create(
            post=post, meta_comment_id='cmt_1',
            sender_name='Facebook User', sender_id='555000111',
            message='Chayapoto', created_time=timezone.now(),
        )
        auto = CommentAutomation.objects.create(
            integration=integ, name='kajal', trigger_keyword='chayapoto',
            match_type='exact', public_reply='this is coming',
            send_dm=True, dm_message='Here is your link 👉 https://shop/x',
            is_active=True,
        )

        # ── 1. Matching automation fires public reply + DM ──────────────────
        print("\n1) Matching keyword fires public reply + DM")
        crm_views._check_comment_automations(integ, comment)

        check("public reply sent to Graph API", sent_public_replies == [('cmt_1', 'this is coming')])
        check("private DM sent to Graph API", sent_dms == [('cmt_1', 'Here is your link 👉 https://shop/x')])

        reply_child = CRMSocialComment.objects.filter(parent_comment=comment).first()
        check("public reply stored locally as child comment", reply_child is not None)
        check("reply stored with page name", reply_child and reply_child.sender_name == 'Routine Of Karma Production')
        check("reply stored with real FB id (not blank/dupe)",
              reply_child and reply_child.meta_comment_id.startswith('fbreply_'))

        dm_msg = CRMMessage.objects.filter(body='Here is your link 👉 https://shop/x', is_outbound=True).first()
        check("DM persisted to a conversation thread", dm_msg is not None)

        auto.refresh_from_db()
        check("trigger_count incremented", auto.trigger_count == 1)
        check("last_triggered_at set", auto.last_triggered_at is not None)

        # ── 2. Non-matching comment does NOT fire ───────────────────────────
        print("\n2) Non-matching keyword does not fire")
        sent_public_replies.clear()
        sent_dms.clear()
        other = CRMSocialComment.objects.create(
            post=post, meta_comment_id='cmt_2',
            sender_name='Facebook User', sender_id='555000222',
            message='nice view', created_time=timezone.now(),
        )
        crm_views._check_comment_automations(integ, other)
        check("no public reply for non-matching comment", sent_public_replies == [])
        check("no DM for non-matching comment", sent_dms == [])

        # ── 3. Failed Graph reply is reported as failure ────────────────────
        print("\n3) Failed public reply is NOT recorded as success")
        reply_should_succeed['value'] = False
        before = CRMSocialComment.objects.filter(parent_comment=other).count()
        crm_views._check_comment_automations(
            integ,
            CRMSocialComment.objects.create(
                post=post, meta_comment_id='cmt_3',
                sender_name='Facebook User', sender_id='555000333',
                message='chayapoto', created_time=timezone.now(),
            ),
        )
        after = CRMSocialComment.objects.filter(post=post, sender_name='Routine Of Karma Production').count()
        # exactly one page reply exists (from test 1); the failed one was not stored
        check("failed reply not stored as a local child comment", after == 1)
        reply_should_succeed['value'] = True

        # ── 4. Facebook 'feed' webhook routes a new comment ─────────────────
        print("\n4) Facebook feed webhook triggers automation on a new comment")
        sent_public_replies.clear()
        sent_dms.clear()
        value = {
            'item': 'comment', 'verb': 'add',
            'from': {'id': '555000444', 'name': 'Jane Doe'},
            'post_id': '123456789_987654321',
            'comment_id': 'cmt_webhook_1',
            'parent_id': '123456789_987654321',
            'message': 'chayapoto',
            'created_time': int(timezone.now().timestamp()),
        }
        meta_sync.handle_feed_comment_webhook('123456789', value)
        check("webhook created the incoming comment", CRMSocialComment.objects.filter(meta_comment_id='cmt_webhook_1').exists())
        check("webhook fired the public reply", sent_public_replies == [('cmt_webhook_1', 'this is coming')])
        check("webhook fired the DM", sent_dms == [('cmt_webhook_1', 'Here is your link 👉 https://shop/x')])

        # ── 5. Page's own comment via webhook is ignored (no loop) ──────────
        print("\n5) Page's own comment via webhook is ignored (loop guard)")
        sent_public_replies.clear()
        own = {
            'item': 'comment', 'verb': 'add',
            'from': {'id': '123456789', 'name': 'Routine Of Karma Production'},
            'post_id': '123456789_987654321',
            'comment_id': 'cmt_own_1',
            'parent_id': 'cmt_webhook_1',
            'message': 'this is coming',
            'created_time': int(timezone.now().timestamp()),
        }
        meta_sync.handle_feed_comment_webhook('123456789', own)
        check("no reply fired for page's own comment", sent_public_replies == [])

        # ── 6. Private-reply fails -> Send API fallback delivers the DM ───────
        print("\n6) Private Replies fails -> falls back to Send API, still logs to inbox")
        private_dm_should_succeed['value'] = False
        sent_dms.clear()
        sent_send_api.clear()
        fb_comment = CRMSocialComment.objects.create(
            post=post, meta_comment_id='cmt_fallback',
            sender_name='Ram', sender_id='555000999',
            message='chayapoto', created_time=timezone.now(),
        )
        res = crm_views._check_comment_automations(integ, fb_comment)
        fired_rule = res[0] if res else {}
        check("private reply was attempted and failed", sent_dms == [])
        check("Send API fallback delivered the DM", sent_send_api == [('555000999', 'Here is your link 👉 https://shop/x')])
        check("rule result reports dm_sent=True via fallback", fired_rule.get('dm_sent') is True and fired_rule.get('dm_method') == 'send_api')
        check("fallback DM logged to a conversation",
              CRMMessage.objects.filter(conversation__contact__meta_id='555000999', is_outbound=True).exists())

        # ── 7. Both channels fail -> surfaced error, no false success ─────────
        print("\n7) Both DM channels fail -> real error surfaced, no false 'sent'")
        meta_sync.send_meta_message = lambda integration, rid, txt: False
        no_psid = CRMSocialComment.objects.create(
            post=post, meta_comment_id='cmt_nopsid',
            sender_name='Anon', sender_id='',  # no PSID -> no fallback possible
            message='chayapoto', created_time=timezone.now(),
        )
        res2 = crm_views._check_comment_automations(integ, no_psid)
        r2 = res2[0] if res2 else {}
        check("dm_sent is False when all channels fail", r2.get('dm_sent') is False)
        check("real Facebook error is surfaced", '10903' in (r2.get('dm_error') or ''))
        meta_sync.send_meta_message = fake_send_meta_message
        private_dm_should_succeed['value'] = True

        # Roll everything back — this is a verification script, not a fixture.
        transaction.set_rollback(True)

except Exception as e:
    import traceback
    traceback.print_exc()
    results.append(False)

print("\n" + "=" * 70)
total, passed = len(results), sum(1 for r in results if r)
print(f"RESULT: {passed}/{total} checks passed")
print("=" * 70)
raise SystemExit(0 if passed == total else 1)
