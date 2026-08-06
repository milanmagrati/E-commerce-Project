import json
import logging
import mimetypes
import requests
from django.utils import timezone
from dateutil.parser import parse
from . import triage
from .models import CRMIntegration, CRMContact, CRMConversation, CRMMessage, CRMSocialPost, CRMSocialComment

logger = logging.getLogger(__name__)


GRAPH_API_VERSION = "v25.0"

# How recent an inbound message must be (relative to now) to trigger an AI
# auto-reply during a conversation sync. Keeps a full/backfill sync (e.g. right
# after OAuth connect, or the "sync everything" webhook fallback) from replaying
# AI replies onto old conversation history.
RECENT_MESSAGE_WINDOW_MINUTES = 10

# Auto-applied labels for messages the keyword pass flags as important, so the
# label filter and sidebar badges (see views.py) have a stable name to key off.
LEAD_LABEL_NAME = '🔥 Potential Lead'
COMPLAINT_LABEL_NAME = '⚠️ Complaint'

# Re-exported from models, which owns it now that the inbox UI reads it too.
# Imported from here by existing callers/scripts, so the name stays available.
from .models import HUMAN_TAKEOVER_MINUTES  # noqa: E402,F401

# Statuses that still count as "this account is ours" when routing *inbound*
# traffic. A page flagged 'error' has a token Graph won't let us send with, but
# Meta keeps delivering its webhooks regardless (the subscription is app-level,
# not token-level) — dropping those messages would lose real customer enquiries,
# which is far worse than the send failure that caused the flag.
LIVE_INTEGRATION_STATUSES = ['connected', 'error']

REACTION_TYPES = ['LIKE', 'LOVE', 'HAHA', 'WOW', 'SAD', 'ANGRY', 'CARE']
REACTION_FIELDS = ','.join(
    f'reactions.type({r}).limit(0).summary(total_count).as({r.lower()})' for r in REACTION_TYPES
)


def _build_reactions_data(graph_node, existing_obj=None):
    """
    Build the {'LIKE': n, 'LOVE': n, ...} reactions dict (plus total like_count)
    from a Graph API post/comment node that was fetched with REACTION_FIELDS.
    Preserves 'my_reaction' from an existing local object, since Graph doesn't
    return it back to us on these summary-only reaction edges.
    """
    reactions_data = {r: graph_node.get(r.lower(), {}).get('summary', {}).get('total_count', 0) for r in REACTION_TYPES}
    my_reaction = existing_obj.reactions_data.get('my_reaction') if existing_obj and isinstance(existing_obj.reactions_data, dict) else None
    if my_reaction:
        reactions_data['my_reaction'] = my_reaction
    like_count = sum(v for k, v in reactions_data.items() if k != 'my_reaction' and isinstance(v, (int, float)))
    return reactions_data, like_count

def sync_meta_conversations(integration):
    """
    Fetches the latest conversations from Meta Graph API for a given integration 
    (Facebook or Instagram) and syncs them to the local database.
    """
    if not integration.access_token:
        logger.error(f"Cannot sync {integration.channel_type}: No access token.")
        return

    # For Facebook Pages, the token is a Page Access Token.
    page_id = integration.account_name.split('(')[-1].strip(')') if '(' in integration.account_name else None
    
    # Use the explicit page_id if available, otherwise fallback to /me
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/conversations" if page_id else f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/conversations"
    params = {
        'access_token': integration.access_token,
        # `attachments` is needed so photo-only messages aren't invisible to us —
        # without it Graph returns an empty `message` and the customer looks silent.
        'fields': (
            'id,updated_time,participants,'
            'messages.limit(20){id,message,created_time,from,to,attachments}'
        ),
        'limit': 10
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            err = _extract_graph_error(response)
            logger.error(f"Failed to fetch conversations from Meta: {err}")
            _flag_integration_auth_error(integration, response, err)
            return

        clear_integration_auth_error(integration)
        data = response.json()
        conversations = data.get('data', [])
        
        for conv_data in conversations:
            _process_meta_conversation(integration, conv_data)
            
    except Exception as e:
        logger.exception("Exception occurred while syncing Meta conversations.")

def get_or_create_contact_conversation(integration, contact_meta_id, contact_name='Unknown User'):
    """
    Resolves (or creates) the CRMContact + CRMConversation pair for one customer
    on one page. Shared by the Graph poller and the live webhook.
    """
    # Match by Meta user ID (PSID) first — this is the stable, unique identifier.
    # Falling back to name matching would merge different customers who share a
    # display name into the same contact/conversation.
    if contact_meta_id:
        contact, created = CRMContact.objects.get_or_create(
            meta_id=contact_meta_id,
            defaults={'status': 'new', 'name': contact_name}
        )
        # Don't overwrite a real name with the placeholder the webhook uses
        # before we've resolved the sender's profile.
        if (not created and contact_name and contact_name != 'Unknown User'
                and contact.name != contact_name):
            contact.name = contact_name
            contact.save(update_fields=['name'])
    else:
        # No id from Graph API (shouldn't normally happen) — last-resort name match.
        contact, created = CRMContact.objects.get_or_create(
            name=contact_name,
            defaults={'status': 'new'}
        )

    # For Facebook Pages, the token is a Page Access Token. `or None` keeps the
    # lookup key byte-identical to the previous inline parse — an empty string
    # here would not match existing rows and would fork every conversation.
    page_id = integration.parsed_id or None

    conversation, _ = CRMConversation.objects.get_or_create(
        contact=contact,
        channel=integration.channel_type,
        account_id=page_id,
        defaults={
            'integration': integration,
            'status': 'open',
            'subject': f"{integration.get_channel_type_display()} Chat"
        }
    )

    # If this is an existing conversation but the integration was reconnected (meaning the old integration was deleted),
    # we want to update the integration pointer to the active one so replies keep working.
    if conversation.integration_id != integration.pk:
        conversation.integration = integration
        conversation.save(update_fields=['integration'])

    return contact, conversation


def _process_meta_conversation(integration, conv_data):
    """
    Processes a single conversation node from the Meta Graph API.
    """
    participants = conv_data.get('participants', {}).get('data', [])
    messages = conv_data.get('messages', {}).get('data', [])
    
    if not participants or not messages:
        return
        
    # Identify the "other" person in the conversation (not our page)
    # The integration account_name typically has the page_name (page_id)
    # We'll just assume the first participant that isn't our page is the contact
    our_page_name = integration.account_name.split(' (')[0] if ' (' in integration.account_name else integration.account_name
    
    contact_data = None
    for p in participants:
        if p.get('name') != our_page_name:
            contact_data = p
            break
            
    if not contact_data:
        # Fallback to the first participant if we can't figure it out
        contact_data = participants[0]
        
    contact, conversation = get_or_create_contact_conversation(
        integration,
        contact_meta_id=contact_data.get('id'),
        contact_name=contact_data.get('name', 'Unknown User'),
    )

    # Process Messages (they come ordered newest to oldest, so we reverse to save them oldest to newest)
    messages.reverse()
    for msg in messages:
        sender_name = msg.get('from', {}).get('name', 'Unknown')
        is_outbound = (sender_name == our_page_name)

        created_time_str = msg.get('created_time')
        try:
            created_at = parse(created_time_str) if created_time_str else timezone.now()
        except Exception:
            created_at = timezone.now()

        upsert_inbound_message(
            integration=integration,
            contact=contact,
            conversation=conversation,
            body=msg.get('message', '') or '',
            external_id=msg.get('id') or '',
            sender_name=sender_name,
            is_outbound=is_outbound,
            created_at=created_at,
            attachments=_extract_graph_attachment(msg),
        )


def _extract_graph_attachment(msg):
    """
    Pulls the first usable attachment off a Graph message node into a simple
    {'type', 'url', 'name'} dict, or None. Graph nests these differently for
    photos vs files, hence the several lookups.
    """
    data = (msg.get('attachments') or {}).get('data') or []
    if not data:
        return None
    att = data[0]
    mime = att.get('mime_type') or ''
    url = (
        (att.get('image_data') or {}).get('url')
        or (att.get('image_data') or {}).get('preview_url')
        or att.get('file_url')
        or (att.get('video_data') or {}).get('url')
    )
    if not url:
        return None
    if mime.startswith('image/') or att.get('image_data'):
        att_type = 'image'
    elif mime.startswith('audio/'):
        att_type = 'audio'
    else:
        att_type = 'document'
    return {'type': att_type, 'url': url, 'name': att.get('name') or ''}


def _alert_presentation(rule):
    """
    (label name, chip kind, colour, chip text) for a rule that wants an agent.

    The two original alerting types keep their exact wording, colours, labels
    and event kinds — everything built around them (the sidebar badges, the
    label filter, the dismiss flow) still has to recognise them. Anything the
    operator added announces itself by its own label.
    """
    key = rule['key']
    if key == 'purchase_intent':
        return (LEAD_LABEL_NAME, CRMMessage.EVENT_LEAD_DETECTED, '#ea580c',
                "🔥 Potential lead — this customer is showing buying interest.")
    if key == 'product_issue':
        return (COMPLAINT_LABEL_NAME, CRMMessage.EVENT_COMPLAINT_DETECTED, '#dc2626',
                "⚠️ Possible complaint — this customer reported an issue.")

    color = triage.PRIORITY_COLORS.get(rule['priority'], '#6366f1')
    return (
        f"🔔 {rule['label']}",
        CRMMessage.alert_kind_for(key),
        color,
        f"🔔 {rule['label']} — this message is set to {rule['priority']} priority.",
    )


def _flag_important_message(conversation, verdict, rules=None):
    """
    Auto-labels the conversation and raises an in-thread chip when the message
    type the keyword pass found is one the business marked "alert an agent".
    Kept independent of the AI-reply engine so it still runs when auto-reply is
    off — a missed lead shouldn't depend on whether the bot happened to be
    allowed to answer.

    Unlike EVENT_AI_FAILURE, these chips don't self-resolve: a lead doesn't
    stop being a lead once someone answers it, so they stay open until an
    agent dismisses them.
    """
    from .models import CRMLabel

    rule = triage.rule_by_key(rules if rules is not None else triage.DEFAULT_RULES, verdict)
    if not rule or not rule.get('alert'):
        return

    label_name, kind, color_hex, text = _alert_presentation(rule)
    label, _ = CRMLabel.objects.get_or_create(name=label_name, defaults={'color_hex': color_hex})
    conversation.labels.add(label)
    CRMMessage.log_event(conversation, text, level='info', kind=kind)


def upsert_inbound_message(integration, contact, conversation, body, external_id,
                           sender_name, is_outbound, created_at, attachments=None):
    """
    Idempotently stores one message and, when it's a genuinely new inbound one,
    hands it to the AI auto-reply engine.

    Shared by the Graph poller and the live webhook so both paths use exactly one
    dedupe rule and one auto-reply trigger. Returns the CRMMessage when a new row
    was created, else None.
    """
    from datetime import timedelta

    body = body or ''
    if body.startswith("You are responding to a user comment to a post on your Page."):
        return None

    # Nothing to store at all — no text and no attachment.
    if not body and not attachments:
        return None

    # Prefer the provider's own message id: it's exact, and it makes the webhook
    # and the poller safe to run against the same message.
    if external_id:
        if CRMMessage.objects.filter(external_id=external_id).exists():
            return None
    else:
        # Locally-composed messages coming back from Graph have no id we stored,
        # so fall back to the original body + ±1 minute heuristic for those.
        window = (created_at - timedelta(minutes=1), created_at + timedelta(minutes=1))
        dupe = CRMMessage.objects.filter(
            conversation=conversation, body=body, is_system=False,
            is_outbound=is_outbound, created_at__range=window,
        )
        if not is_outbound:
            dupe = dupe.filter(sender=sender_name)
        if dupe.exists():
            return None

    # An outbound message we already stored locally will come back from Graph
    # with an id we've never seen. Match it on content so the operator doesn't
    # see their own message twice, and backfill the id we just learned.
    if is_outbound and external_id:
        window = (created_at - timedelta(minutes=2), created_at + timedelta(minutes=2))
        local = CRMMessage.objects.filter(
            conversation=conversation, body=body, is_outbound=True,
            is_system=False, external_id='', created_at__range=window,
        ).first()
        if local:
            local.external_id = external_id
            local.save(update_fields=['external_id'])
            return None

    attachment_type = (attachments or {}).get('type', '') or ''
    message = CRMMessage.objects.create(
        conversation=conversation,
        sender=sender_name,
        body=body,
        is_outbound=is_outbound,
        created_at=created_at,
        external_id=external_id or '',
        attachment_type=attachment_type,
        attachment_name=(attachments or {}).get('name', '') or '',
    )

    conversation.last_message = body or f"[{attachment_type or 'attachment'}]"
    if not conversation.updated_at or created_at > conversation.updated_at:
        conversation.updated_at = created_at
    if not is_outbound:
        conversation.is_read = False
    conversation.save(update_fields=['last_message', 'updated_at', 'is_read'])

    # Lead/complaint detection runs on every inbound message regardless of
    # whether AI auto-reply is enabled or in its recency window — a missed
    # lead shouldn't depend on the bot's ability to answer. Deliberately uses
    # only the free keyword pass (never classify_intent/route_message), so
    # this can never spend one of the scarce Gemini free-tier calls.
    # Skipped for attachment-only messages: the keyword pass reads an empty body
    # as spam_noise, and a customer sending just a product photo is not spam.
    if not is_outbound and (body or '').strip():
        try:
            from .ai_router import _keyword_intent
            # The rules of the business whose page received this message: what
            # its types are called, which keywords identify them, and which of
            # them are worth pulling an agent in for.
            rules = triage.rules_for(getattr(integration, 'chatbot_config', None))
            verdict = _keyword_intent(body, rules)
            if verdict:
                message.ai_intent = verdict
                message.save(update_fields=['ai_intent'])
            _flag_important_message(conversation, verdict, rules)
        except Exception:
            logger.exception("Lead/complaint detection failed during message upsert")

    # Route genuinely new inbound messages through the AI auto-reply engine.
    # Guarded by recency so a full/backfill sync (e.g. right after OAuth connect)
    # doesn't replay AI replies onto old history.
    if not is_outbound and (timezone.now() - created_at) <= timedelta(minutes=RECENT_MESSAGE_WINDOW_MINUTES):
        try:
            process_incoming_webhook_message(
                integration, contact, conversation, body,
                is_outbound=False, attachment=attachments,
            )
        except Exception:
            logger.exception("process_incoming_webhook_message failed during message upsert")

    return message

def _open_attachment(attachment_file):
    """
    Returns (filename, file object, mimetype) for a Django FieldFile so it can be
    uploaded to Graph as multipart form data. Uploading the bytes is the only
    thing that works when MEDIA_URL is not publicly reachable (local dev, or any
    deployment where /media/ sits behind auth) — Meta fetches `payload.url`
    itself, so a 127.0.0.1 link can never be downloaded on their side.
    """
    name = (getattr(attachment_file, 'name', '') or 'attachment').rsplit('/', 1)[-1]
    mimetype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    attachment_file.open('rb')
    return name, attachment_file, mimetype


def _handle_send_response(integration, response, context):
    """Shared success/failure handling for a Graph send call. Returns (ok, error)."""
    if response.status_code == 200:
        clear_integration_auth_error(integration)
        return True, None

    err = _extract_graph_error(response)
    logger.error(
        f"{context} failed on {integration.account_name or integration.channel_type}: "
        f"HTTP {response.status_code} - {err}"
    )
    _flag_integration_auth_error(integration, response, err)
    return False, err


def send_meta_message(integration, recipient_id, message_text=None, attachment_file=None, attachment_type=None):
    """
    Sends a message via the Meta Graph API to the recipient PSID. Uploads an
    attachment (image/audio/file) when attachment_file is given, plain text
    otherwise. Messenger's attachment payload has no caption slot, so when both
    are present they go out as two messages — attachment first, then the text.
    Dropping the text would silently lose whatever the agent typed alongside it.

    Returns (success: bool, error: str | None) — the error string carries the
    real Graph message so the UI can explain *why* delivery failed instead of
    showing a generic "check your permissions".
    """
    if not integration.access_token:
        logger.error(f"Cannot send Meta message for {integration.channel_type}: No access token.")
        return False, 'No access token on this page — reconnect the integration.'

    if not attachment_file and not message_text:
        return False, 'Nothing to send.'

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    params = {'access_token': integration.access_token}
    recipient = {'id': recipient_id}

    if attachment_file:
        meta_attachment_type = {'image': 'image', 'audio': 'audio', 'document': 'file'}.get(attachment_type, 'file')
        try:
            name, fh, mimetype = _open_attachment(attachment_file)
            try:
                # Graph wants the JSON parts stringified alongside the binary part.
                response = requests.post(
                    url, params=params, timeout=60,
                    data={
                        'recipient': json.dumps(recipient),
                        'messaging_type': 'RESPONSE',
                        'message': json.dumps({
                            'attachment': {'type': meta_attachment_type, 'payload': {'is_reusable': True}}
                        }),
                    },
                    files={'filedata': (name, fh, mimetype)},
                )
            finally:
                # Leaving the handle open locks the media file on Windows.
                attachment_file.close()
        except Exception as e:
            logger.error(f"Failed to send Meta attachment: {e}")
            return False, str(e)

        ok, err = _handle_send_response(integration, response, 'Meta attachment send')
        if not ok or not message_text:
            return ok, err

    try:
        response = requests.post(
            url, params=params, timeout=10,
            json={'recipient': recipient, 'messaging_type': 'RESPONSE', 'message': {'text': message_text}},
        )
    except Exception as e:
        logger.error(f"Failed to send Meta message: {e}")
        return False, str(e)

    return _handle_send_response(integration, response, 'Meta message send')


def send_whatsapp_message(integration, recipient_wa_id, message_text=None, attachment_file=None, attachment_type=None):
    """
    Sends a message via the WhatsApp Cloud API from the connected phone number.
    Uploads an attachment (image/audio/document) to the media endpoint first when
    attachment_file is given, plain text otherwise. Image/document attachments
    carry message_text as a caption; WhatsApp audio has no caption slot, so there
    the text follows as its own message rather than being dropped.
    Returns (success: bool, error: str | None).
    """
    if not integration.access_token:
        logger.error("Cannot send WhatsApp message: No access token.")
        return False, 'No access token on this number — reconnect the integration.'

    phone_number_id = (integration.meta or {}).get('phone_number_id')
    if not phone_number_id:
        logger.error("Cannot send WhatsApp message: integration has no phone_number_id.")
        return False, 'This WhatsApp integration has no phone number ID — reconnect it.'

    if not attachment_file and not message_text:
        return False, 'Nothing to send.'

    headers = {'Authorization': f'Bearer {integration.access_token}'}
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"

    def _post(payload, context):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
        except Exception as e:
            logger.error(f"{context} failed: {e}")
            return False, str(e)
        return _handle_send_response(integration, response, context)

    text_still_pending = bool(message_text)

    if attachment_file:
        wa_type = {'image': 'image', 'audio': 'audio', 'document': 'document'}.get(attachment_type, 'document')
        # Cloud API takes an uploaded media ID (or a public link); upload the
        # bytes so local/protected MEDIA files still go through.
        media_id, media_err = _upload_whatsapp_media(integration, phone_number_id, attachment_file, headers)
        if not media_id:
            return False, media_err
        media_payload = {'id': media_id}
        if message_text and wa_type in ('image', 'document'):
            media_payload['caption'] = message_text
            text_still_pending = False
        ok, err = _post({
            'messaging_product': 'whatsapp',
            'to': recipient_wa_id,
            'type': wa_type,
            wa_type: media_payload,
        }, 'WhatsApp attachment send')
        if not ok or not text_still_pending:
            return ok, err

    return _post({
        'messaging_product': 'whatsapp',
        'to': recipient_wa_id,
        'type': 'text',
        'text': {'body': message_text},
    }, 'WhatsApp message send')


def _upload_whatsapp_media(integration, phone_number_id, attachment_file, headers):
    """Uploads a file to the WhatsApp Cloud API media endpoint. Returns (media_id, error)."""
    try:
        name, fh, mimetype = _open_attachment(attachment_file)
        try:
            response = requests.post(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/media",
                headers=headers,
                data={'messaging_product': 'whatsapp', 'type': mimetype},
                files={'file': (name, fh, mimetype)},
                timeout=60,
            )
        finally:
            attachment_file.close()
    except Exception as e:
        logger.error(f"WhatsApp media upload failed: {e}")
        return None, str(e)

    if response.status_code == 200:
        return response.json().get('id'), None

    err = _extract_graph_error(response)
    logger.error(f"WhatsApp media upload failed: HTTP {response.status_code} - {err}")
    _flag_integration_auth_error(integration, response, err)
    return None, err


def _resolve_whatsapp_integration(phone_number_id):
    """Find the WhatsApp integration that owns the given phone_number_id."""
    if not phone_number_id:
        return None
    return CRMIntegration.objects.filter(
        channel_type='whatsapp', status__in=LIVE_INTEGRATION_STATUSES,
        meta__phone_number_id=str(phone_number_id),
    ).first()


def process_whatsapp_message_webhook(value):
    """
    Processes a single WhatsApp Cloud API webhook 'messages' change payload
    (entry[].changes[].value where field == 'messages'), creating/updating the
    contact, conversation and message locally, then routing genuinely new
    inbound messages through the same AI auto-reply engine used for FB/IG.
    """
    from datetime import timedelta, datetime, timezone as dt_timezone

    try:
        metadata = value.get('metadata', {}) or {}
        phone_number_id = metadata.get('phone_number_id')
        integration = _resolve_whatsapp_integration(phone_number_id)
        if not integration:
            logger.warning(f"WhatsApp webhook: no connected integration for phone_number_id {phone_number_id}")
            return

        contacts_by_wa_id = {
            c.get('wa_id'): c.get('profile', {}).get('name', '')
            for c in (value.get('contacts') or [])
        }

        for msg in (value.get('messages') or []):
            wa_id = msg.get('from')
            if not wa_id:
                continue

            msg_type = msg.get('type')
            if msg_type == 'text':
                body = msg.get('text', {}).get('body', '')
            elif msg_type:
                body = f"[{msg_type} message]"
            else:
                body = ''
            if not body:
                continue

            contact_name = contacts_by_wa_id.get(wa_id) or wa_id
            contact, created = CRMContact.objects.get_or_create(
                meta_id=wa_id,
                defaults={'status': 'new', 'name': contact_name, 'phone': wa_id}
            )
            if not created:
                update_fields = []
                if contact_name and contact.name != contact_name:
                    contact.name = contact_name
                    update_fields.append('name')
                if not contact.phone:
                    contact.phone = wa_id
                    update_fields.append('phone')
                if update_fields:
                    contact.save(update_fields=update_fields)

            conversation, conv_created = CRMConversation.objects.get_or_create(
                contact=contact,
                channel='whatsapp',
                account_id=phone_number_id,
                defaults={
                    'integration': integration,
                    'status': 'open',
                    'subject': 'WhatsApp Chat',
                }
            )
            if conversation.integration != integration:
                conversation.integration = integration
                conversation.save(update_fields=['integration'])

            timestamp = msg.get('timestamp')
            try:
                created_at = datetime.fromtimestamp(int(timestamp), tz=dt_timezone.utc) if timestamp else timezone.now()
            except (TypeError, ValueError):
                created_at = timezone.now()

            # Dedupe on WhatsApp's own message id (WhatsApp redelivers the same
            # webhook event on retry) and route through the shared upsert, which
            # also fires the auto-reply.
            upsert_inbound_message(
                integration=integration,
                contact=contact,
                conversation=conversation,
                body=body,
                external_id=msg.get('id') or '',
                sender_name=contact_name,
                is_outbound=False,
                created_at=created_at,
            )
    except Exception:
        logger.exception("process_whatsapp_message_webhook failed")


def sync_meta_posts(integration):
    if not integration.access_token:
        return
        
    page_id = integration.parsed_id
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/posts" if page_id else f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/posts"
    params = {
        'access_token': integration.access_token,
        'fields': f'id,message,created_time,full_picture,comments.limit(10).summary(true).order(reverse_chronological){{id,from,message,created_time}},{REACTION_FIELDS}',
        'limit': 25
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            posts = response.json().get('data', [])
            for p in posts:
                created_time_str = p.get('created_time')
                created_at = parse(created_time_str) if created_time_str else timezone.now()
                
                comments_count = p.get('comments', {}).get('summary', {}).get('total_count', 0)
                
                old_post = CRMSocialPost.objects.filter(meta_post_id=p.get('id')).first()
                reactions_data, likes_count = _build_reactions_data(p, old_post)

                post_obj, _ = CRMSocialPost.objects.update_or_create(
                    meta_post_id=p.get('id'),
                    defaults={
                        'integration': integration,
                        'message': p.get('message', ''),
                        'picture_url': p.get('full_picture', ''),
                        'created_time': created_at,
                        'likes_count': likes_count,
                        'reactions_data': reactions_data,
                        'comments_count': comments_count
                    }
                )
                
                recent_comments = p.get('comments', {}).get('data', [])
                for c in recent_comments:
                    _process_comment(post_obj, c, None)
    except Exception as e:
        logger.exception("Failed to sync meta posts")

def sync_meta_comments(post):
    if not post.integration.access_token:
        return

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{post.meta_post_id}/comments"
    params = {
        'access_token': post.integration.access_token,
        'fields': f'id,from,message,created_time,can_hide,{REACTION_FIELDS},comments{{id,from,message,created_time,{REACTION_FIELDS}}}',
        'limit': 100,
        'filter': 'toplevel',
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            comments = data.get('data', [])
            for c in comments:
                _process_comment(post, c, None)
                replies = c.get('comments', {}).get('data', [])
                for r in replies:
                    _process_comment(post, r, c.get('id'))
        else:
            logger.warning(f"sync_meta_comments got {response.status_code}: {response.text[:300]}")
    except Exception as e:
        logger.exception("Failed to sync meta comments")

def _fetch_user_name(sender_id, access_token):
    """
    Secondary Graph API call to fetch the real display name for a user ID.
    Returns the name string, or '' if lookup fails or is not permitted.
    """
    if not sender_id or not access_token:
        return ''
    try:
        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{sender_id}"
        resp = requests.get(url, params={'fields': 'name', 'access_token': access_token}, timeout=5)
        if resp.status_code == 200:
            name = resp.json().get('name', '').strip()
            return name
    except Exception as e:
        logger.debug(f"Secondary name lookup failed for {sender_id}: {e}")
    return ''


def _process_comment(post, comment_data, parent_id):
    from_data = comment_data.get('from') or {}
    sender_name = (from_data.get('name') or '').strip()
    sender_id = (from_data.get('id') or '').strip()

    # Build a display name — try a secondary lookup before falling back
    if not sender_name and sender_id:
        sender_name = _fetch_user_name(sender_id, post.integration.access_token)

    if not sender_name:
        sender_name = "Facebook User"

    created_time_str = comment_data.get('created_time')
    created_at = parse(created_time_str) if created_time_str else timezone.now()

    parent_comment = None
    if parent_id:
        parent_comment = CRMSocialComment.objects.filter(meta_comment_id=parent_id).first()

    old_comment = CRMSocialComment.objects.filter(meta_comment_id=comment_data.get('id')).first()
    reactions_data, like_count = _build_reactions_data(comment_data, old_comment)

    defaults = {
        'post': post,
        'parent_comment': parent_comment,
        'sender_id': sender_id,
        'message': comment_data.get('message', ''),
        'created_time': created_at,
        'like_count': like_count,
        'reactions_data': reactions_data,
    }

    comment_meta_id = comment_data.get('id')
    if not comment_meta_id:
        logger.warning("_process_comment: comment has no id, skipping")
        return

    obj, created = CRMSocialComment.objects.get_or_create(
        meta_comment_id=comment_meta_id,
        defaults={**defaults, 'sender_name': sender_name}
    )

    if not created:
        for k, v in defaults.items():
            setattr(obj, k, v)
        # Always update name if we now have a better one
        if sender_name and sender_name != 'Facebook User':
            obj.sender_name = sender_name
        elif obj.sender_name in ('Unknown', 'Facebook User', '') and sender_name:
            obj.sender_name = sender_name
        obj.save()
    else:
        # Mark post as unread if this is a new comment from someone else
        if not sender_id or str(sender_id) != str(post.integration.parsed_id):
            post.is_read = False
            post.save(update_fields=['is_read'])

        # NEW top-level comment → trigger the Comment-to-DM sales funnel
        # (Only for root comments, not replies to our own page comments)
        if not parent_id:
            try:
                trigger_comment_to_dm(obj, post.integration)
            except Exception as e:
                logger.error(f"trigger_comment_to_dm raised an exception: {e}")

            # ManyChat-style keyword automations
            try:
                from trendycrm.views import _check_comment_automations
                _check_comment_automations(post.integration, obj)
            except Exception as e:
                logger.error(f"_check_comment_automations raised an exception: {e}")


def reply_to_meta_comment(integration, comment_id, message_text):
    """
    Post a reply to a Facebook comment.
    Returns (success: bool, new_comment_id: str | None).
    The new_comment_id is the real Facebook ID of the reply — callers should
    store it so the next sync doesn't re-import the reply as a top-level comment.
    """
    if not integration.access_token:
        return False, None
        
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}/comments"
    params = {'access_token': integration.access_token}
    payload = {'message': message_text}
    
    try:
        response = requests.post(url, params=params, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        new_id = data.get('id')  # Facebook returns {"id": "<new_comment_id>"}
        return True, new_id
    except Exception as e:
        logger.error(f"Failed to send comment reply: {e}")
        return False, None
        

def reply_to_meta_comment_privately(integration, comment_id, message_text):
    """
    Send a direct message (DM) to a user who commented, using the Private Replies API.
    This bypasses the usual 24-hour PSID window requirement by linking the DM to their comment.
    Returns (success: bool, error: str | None). The error string carries the real
    Facebook Graph error so callers/UI can explain *why* a DM did not go out.
    """
    if not integration.access_token:
        return False, 'No access token on this page — reconnect the integration.'

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}/private_replies"
    params = {'access_token': integration.access_token}
    payload = {'message': message_text}

    try:
        response = requests.post(url, params=params, json=payload, timeout=10)
        if response.status_code == 200:
            return True, None
        # Surface the actual Graph error (e.g. "already sent a private reply",
        # "outside the allowed window", missing pages_messaging permission…).
        err = _extract_graph_error(response)
        logger.warning(
            f"private_replies failed for comment {comment_id}: "
            f"HTTP {response.status_code} - {err}"
        )
        return False, err
    except Exception as e:
        logger.error(f"Failed to send private reply for comment {comment_id}: {e}")
        return False, str(e)


def _extract_graph_error(response):
    """Pull a human-readable message out of a Graph API error response."""
    try:
        data = response.json()
        err = data.get('error', {})
        msg = err.get('message') or ''
        code = err.get('code')
        sub = err.get('error_subcode')
        detail = err.get('error_user_msg') or ''
        parts = [p for p in (msg, detail) if p]
        label = ' | '.join(parts) if parts else (response.text or '')[:300]
        if code:
            label = f"[{code}{'/' + str(sub) if sub else ''}] {label}"
        return label
    except Exception:
        return (getattr(response, 'text', '') or '')[:300]


def _flag_integration_auth_error(integration, response, error_label):
    """
    Marks an integration as needing a reconnect when Graph rejects its token.

    Code 190 (OAuthException) means the page token itself is dead or the page was
    never granted to this app — e.g. the user re-ran Facebook Login and only
    ticked *some* of their pages, silently invalidating the tokens of the ones
    they left out. Nothing else notices that, so the page keeps looking
    "Connected" while every send fails; flagging it here is what surfaces the
    reconnect prompt on the Integrations page.
    """
    try:
        code = (response.json().get('error') or {}).get('code')
    except Exception:
        return
    if code != 190:
        return

    integration.status = 'error'
    meta = integration.meta if isinstance(integration.meta, dict) else {}
    meta['token_error'] = error_label
    meta['token_error_at'] = timezone.now().isoformat()
    integration.meta = meta
    integration.save(update_fields=['status', 'meta'])
    logger.error(
        f"Marking {integration.channel_type} integration "
        f"'{integration.account_name}' as needing reconnect: {error_label}"
    )


def clear_integration_auth_error(integration):
    """Undo _flag_integration_auth_error once the page talks to Graph again."""
    if integration.status != 'error':
        return
    integration.status = 'connected'
    meta = integration.meta if isinstance(integration.meta, dict) else {}
    meta.pop('token_error', None)
    meta.pop('token_error_at', None)
    integration.meta = meta
    integration.save(update_fields=['status', 'meta'])


def send_comment_dm(integration, comment_id, message_text, sender_id=None):
    """
    Deliver a DM to someone who commented on a post.

    Strategy:
      1. Private Replies API (best for comment→DM — no 24h window needed).
      2. If that fails and we have the commenter's PSID, fall back to the
         standard Messenger Send API.

    Returns (success: bool, method: str, error: str | None).
    """
    ok, err = reply_to_meta_comment_privately(integration, comment_id, message_text)
    if ok:
        return True, 'private_reply', None

    # Fallback — only possible when Facebook gave us a usable PSID for the sender.
    if sender_id:
        sent, send_err = send_meta_message(integration, sender_id, message_text)
        if sent:
            logger.info(f"DM delivered via Send API fallback for comment {comment_id}")
            return True, 'send_api', None
        return False, 'send_api', send_err or err or 'Send API delivery failed.'

    return False, 'private_reply', err


def record_outbound_dm(integration, comment, dm_text):
    """
    Persist an outbound DM (sent to a commenter) into the CRM inbox so it shows
    up on the Conversations page as an outgoing message in the right thread.
    Creates/links the CRMContact and CRMConversation as needed.
    Returns the CRMConversation.
    """
    page_name = integration.parsed_name or integration.account_name or 'Page'
    sender_id = comment.sender_id or ''
    contact_name = comment.sender_name or 'Facebook User'
    contact_meta_id = sender_id if sender_id else f"commenter_{comment.meta_comment_id}"

    contact, _ = CRMContact.objects.get_or_create(
        meta_id=contact_meta_id,
        defaults={'name': contact_name},
    )
    # Backfill a real name if the contact was first seen without one
    if contact.name in ('', 'Facebook User', 'Unknown User') and contact_name not in ('', 'Facebook User'):
        contact.name = contact_name
        contact.save(update_fields=['name'])

    page_id = integration.parsed_id or None
    conversation, _ = CRMConversation.objects.get_or_create(
        contact=contact,
        channel=integration.channel_type,
        account_id=page_id,
        defaults={
            'integration': integration,
            'status': 'open',
            'subject': f"{integration.get_channel_type_display()} Chat",
        },
    )
    # Keep the reply routable even if the page was reconnected under a new integration
    if conversation.integration_id != integration.pk:
        conversation.integration = integration
        conversation.save(update_fields=['integration'])

    CRMMessage.objects.create(
        conversation=conversation,
        sender=page_name,
        body=dm_text,
        is_outbound=True,
        created_at=timezone.now(),
    )
    conversation.last_message = dm_text
    conversation.updated_at = timezone.now()
    conversation.save(update_fields=['last_message', 'updated_at'])
    return conversation
        
def hide_meta_comment(integration, comment_id, is_hidden=True):
    if not integration.access_token:
        return False
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}"
    params = {'access_token': integration.access_token}
    payload = {'is_hidden': is_hidden}
    try:
        response = requests.post(url, params=params, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to hide comment: {e}")
        return False
        
def react_meta_object(integration, object_id, reaction_type='LIKE'):
    if not integration.access_token:
        return False
    try:
        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{object_id}/reactions"
        if reaction_type == 'DELETE':
            res = requests.delete(url, params={'access_token': integration.access_token}, timeout=10)
            if res.status_code == 200:
                return True
            url_likes = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{object_id}/likes"
            res_likes = requests.delete(url_likes, params={'access_token': integration.access_token}, timeout=10)
            return res_likes.status_code == 200
        else:
            res = requests.post(url, params={'access_token': integration.access_token, 'type': reaction_type}, timeout=10)
            if res.status_code == 200:
                return True
            # Fallback to likes endpoint if reactions endpoint is not supported
            url_likes = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{object_id}/likes"
            res_likes = requests.post(url_likes, params={'access_token': integration.access_token}, timeout=10)
            res_likes.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Failed to react to object: {e}")
        return False

def delete_meta_comment(integration, comment_id):
    if not integration.access_token:
        return False
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}"
    params = {'access_token': integration.access_token}
    try:
        response = requests.delete(url, params=params, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to delete comment: {e}")
        return False


def handle_messenger_event(page_id, sender_id, event):
    """
    Handles one inbound Facebook/Instagram Messenger event straight from the
    webhook payload, rather than waiting for the next Graph poll to notice it.

    Meta delivers the message body in the webhook; the previous implementation
    discarded it and used the webhook only as a signal to re-poll, which cost
    seconds of latency and silently dropped anything the poll's 10-conversation
    page didn't cover.
    """
    from datetime import datetime, timezone as dt_timezone

    try:
        integration = _resolve_integration_by_page(page_id)
        if not integration:
            logger.warning(f"handle_messenger_event: no integration for page {page_id}")
            return

        message = event.get('message') or {}
        # Our own sends are echoed back to us; storing them would duplicate the
        # message the composer already wrote.
        if message.get('is_echo'):
            return

        mid = message.get('mid') or ''
        body = message.get('text') or ''

        attachment = None
        for att in (message.get('attachments') or []):
            url = (att.get('payload') or {}).get('url')
            if not url:
                continue
            att_type = att.get('type')
            attachment = {
                'type': att_type if att_type in ('image', 'audio') else 'document',
                'url': url,
                'name': (att.get('payload') or {}).get('title') or '',
            }
            break

        if not body and not attachment:
            return

        timestamp = event.get('timestamp')
        try:
            created_at = (datetime.fromtimestamp(int(timestamp) / 1000.0, tz=dt_timezone.utc)
                          if timestamp else timezone.now())
        except (TypeError, ValueError):
            created_at = timezone.now()

        contact, conversation = get_or_create_contact_conversation(
            integration, contact_meta_id=sender_id, contact_name='Unknown User',
        )

        upsert_inbound_message(
            integration=integration,
            contact=contact,
            conversation=conversation,
            body=body,
            external_id=mid,
            sender_name=contact.name or 'Customer',
            is_outbound=False,
            created_at=created_at,
            attachments=attachment,
        )
    except Exception:
        logger.exception("handle_messenger_event failed")


def _download_image_b64(url, max_bytes=10 * 1024 * 1024):
    """
    Fetches an inbound image attachment and returns (base64_str, mime_type) for
    the vision path, or (None, None) if it can't be read. Meta's CDN URLs are
    pre-signed and short-lived, so this has to happen while handling the message.
    """
    if not url:
        return None, None
    try:
        import base64
        resp = requests.get(url, timeout=15, stream=True)
        if not resp.ok:
            logger.warning(f"Could not download inbound image ({resp.status_code})")
            return None, None
        content = resp.raw.read(max_bytes + 1, decode_content=True)
        if len(content) > max_bytes:
            logger.warning("Inbound image exceeds 10MB — skipping vision routing")
            return None, None
        mime = (resp.headers.get('Content-Type') or 'image/jpeg').split(';')[0].strip()
        if not mime.startswith('image/'):
            mime = 'image/jpeg'
        return base64.b64encode(content).decode('utf-8'), mime
    except Exception as e:
        logger.error(f"Failed to download inbound image: {e}")
        return None, None


def _record_bot_status(chatbot, error=None):
    """
    Stores (or clears) the reason the last auto-reply attempt failed, so the
    Chatbot page can explain a silent bot. Never raises — status bookkeeping must
    not break the reply path.
    """
    try:
        if error:
            chatbot.last_error = str(error)[:1000]
            chatbot.last_error_at = timezone.now()
        elif not chatbot.last_error:
            return  # already clean, skip the write
        else:
            chatbot.last_error = ''
            chatbot.last_error_at = None
        chatbot.save(update_fields=['last_error', 'last_error_at'])
    except Exception:
        logger.exception("Could not record chatbot status")


# How long to wait before each automatic re-attempt of a failed auto-reply, in
# seconds. Rate limits on a free-tier key are per-minute and provider overloads
# clear in seconds, so a short ladder recovers most failures without a human ever
# seeing them; anything still failing after ~6 minutes needs a person, not a
# fourth attempt. Only failures the router marks retryable use this.
AI_RETRY_DELAYS = [30, 90, 240]


def _short_failure_reason(reason):
    """
    Trims the raw provider blob off a router error for the in-thread chip.

    The full text ('... (Gemini API error 429: RESOURCE_EXHAUSTED - You exceeded
    your current quota, please check your plan and billing details...)') belongs
    on the Chatbot page where an operator is debugging. In the inbox it buries
    the one thing the agent needs to know: the bot is not answering this customer.
    """
    reason = (reason or '').strip()
    head = reason.split(' (', 1)[0].strip()
    return head or reason


def _cancel_ai_failure_chip(conversation, note=None):
    """
    Retires the open failure chip because it no longer describes reality — the
    AI answered, a human did, or the bot was stood down on this chat.
    """
    from .models import CRMMessage
    try:
        if CRMMessage.resolve_events(conversation, CRMMessage.EVENT_AI_FAILURE) and note:
            logger.info(f"Cleared AI failure chip on conversation #{conversation.pk} — {note}")
    except Exception:
        logger.exception("Could not clear AI failure chip")


def _schedule_ai_retry(integration, contact, conversation, message_text, attachment, attempt):
    """
    Re-runs a failed auto-reply after AI_RETRY_DELAYS[attempt] seconds.

    Runs on a timer thread rather than inline: the caller may be a webhook Meta
    is waiting on, and sleeping there risks the delivery being retried or
    dropped. Ids are re-resolved inside the thread because the objects the
    request built will be stale (or their connection closed) by the time it runs.
    """
    import threading
    from django.db import connection

    delay = AI_RETRY_DELAYS[attempt]

    def _run():
        try:
            from .models import CRMConversation, CRMContact
            conv = CRMConversation.objects.filter(pk=conversation.pk).first()
            integ = CRMIntegration.objects.filter(pk=integration.pk).first()
            if not conv or not integ:
                return
            cont = CRMContact.objects.filter(pk=contact.pk).first() if contact else None
            _attempt_ai_reply(integ, cont, conv, message_text,
                              attachment=attachment, attempt=attempt + 1)
        except Exception:
            logger.exception("Scheduled AI auto-reply retry failed")
        finally:
            connection.close()

    timer = threading.Timer(delay, _run)
    timer.daemon = True
    timer.start()
    logger.info(
        f"Scheduled AI auto-reply retry {attempt + 1}/{len(AI_RETRY_DELAYS)} "
        f"on conversation #{conversation.pk} in {delay}s"
    )
    return delay


def _record_ai_failure(conversation, chatbot, reason, retryable, attempt,
                       message_text, attachment, integration):
    """
    Puts (or updates) the single chip that explains a silent bot, and starts the
    background retry ladder when the failure is one that clears by itself.

    There is at most one open chip per conversation: a provider outage fails
    every attempt the same way, and stacking a warning per attempt would bury
    the customer's message under the explanation for why nobody answered it.
    """
    from .models import CRMMessage

    _record_bot_status(chatbot, error=reason)

    retries_left = retryable and attempt < len(AI_RETRY_DELAYS)
    if retries_left:
        delay = _schedule_ai_retry(integration, _conversation_contact(conversation), conversation,
                                   message_text, attachment, attempt)
        text = (
            f"The AI hasn't been able to reply yet — {_short_failure_reason(reason)} "
            f"Retrying automatically in {_humanize_seconds(delay)} "
            f"(attempt {attempt + 2} of {len(AI_RETRY_DELAYS) + 1}). "
            f"You can reply manually at any time — that cancels the retry."
        )
    else:
        text = (
            f"The AI couldn't reply to this message — {reason} "
            + (f"Retried {attempt} time{'s' if attempt != 1 else ''} without success. "
               if attempt else "")
            + "Reply manually, or use Retry AI once the provider recovers."
        )

    CRMMessage.log_event(
        conversation,
        text,
        level='warning',
        kind=CRMMessage.EVENT_AI_FAILURE,
        data={
            'reason': reason,
            'retrying': bool(retries_left),
            'retryable': bool(retryable),
            'message_text': message_text,
            'attachment': attachment or None,
            'integration_id': integration.pk if integration else None,
        },
        retry_count=attempt,
    )


def _conversation_contact(conversation):
    """The conversation's contact, or None — retries reload it by conversation."""
    return conversation.contact if conversation.contact_id else None


def _humanize_seconds(seconds):
    """'30 seconds' / '2 minutes' — chip text, so approximate is fine."""
    if seconds < 60:
        return f"{int(seconds)} seconds"
    minutes = round(seconds / 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


# ─── AI Router Integration ─────────────────────────────────────────────────────
def process_incoming_webhook_message(integration, contact, conversation, message_text,
                                     is_outbound=False, attachment=None):
    """
    Called when a new INBOUND message arrives via webhook or Graph poll.
    If the chatbot is active and the message is inbound (from customer),
    routes the message through the Multi-Model AI Engine and sends an auto-reply.

    Args:
        integration: CRMIntegration instance (the page that received the message)
        contact: CRMContact instance
        conversation: CRMConversation instance
        message_text: str — the customer's raw message text
        is_outbound: bool — only process inbound (customer) messages
        attachment: optional {'type','url','name'} dict for photo messages
    """
    if is_outbound:
        return  # Never auto-reply to our own outbound messages
    _attempt_ai_reply(integration, contact, conversation, message_text, attachment=attachment)


def _attempt_ai_reply(integration, contact, conversation, message_text,
                      attachment=None, attempt=0, manual=False):
    """
    One attempt at answering an inbound message with the AI.

    Shared by the first, webhook-driven attempt and by every later one — the
    scheduled retries and the agent's Retry AI button — so a retry re-runs the
    exact same gates rather than a looser copy of them. In particular a retry
    re-reads `ai_status`: if an agent took the chat over during the backoff, the
    bot must stay out of it, and the warning it left behind is no longer the
    agent's problem to act on.

    `attempt` is 0 for the original try. Returns (ok, reason).
    """
    try:
        from .models import CRMMessage

        # The open failure chip *is* the outstanding work item. If it's gone, the
        # message has been dealt with — an agent replied, or someone dismissed the
        # alert — and this retry has nothing left to do. Checking the chip rather
        # than the takeover timer matters because the last rung of the ladder can
        # land after a 5-minute human pause has already expired, which would let
        # the bot answer a customer the agent has just finished answering.
        if attempt and not CRMMessage.open_event(conversation, CRMMessage.EVENT_AI_FAILURE):
            logger.info(
                f"Dropping AI retry on conversation #{conversation.pk} — the alert "
                f"was already cleared."
            )
            return False, 'That alert has already been cleared.'

        chatbot = integration.chatbot_config

        if not chatbot:
            logger.info(f"No chatbot assigned to channel account: {integration.account_name or integration.channel_type}")
            return False, 'No chatbot is assigned to this channel.'

        # ── Gate: chatbot config, channel enablement, and human-takeover guards ──
        # All of it lives on the conversation so the inbox badge shows exactly the
        # state the bot is acting on. The bot must never talk over a staff member
        # who is handling the chat.
        ai_state = conversation.ai_status(integration=integration)
        if ai_state['state'] != 'on':
            logger.info(
                f"Skipping auto-reply on conversation #{conversation.pk} "
                f"[{ai_state['state']}] — {ai_state['reason']}"
            )
            if attempt:
                # A retry that arrives after a human has stepped in has nothing
                # left to warn about — the chat is being handled.
                _cancel_ai_failure_chip(conversation, note=ai_state['reason'])
            return False, ai_state['reason']

        # A photo-only message has no text to classify; send it down the vision
        # path instead, which the router already supports.
        image_data, image_mime, input_type = None, None, 'text'
        if attachment and attachment.get('type') == 'image':
            image_data, image_mime = _download_image_b64(attachment.get('url'))
            if image_data:
                input_type = 'image'
                if not message_text:
                    message_text = "The customer sent this image. Respond helpfully."
        if not message_text:
            logger.info("Inbound message has no text and no readable image — skipping auto-reply")
            return False, 'The message had no text and no readable image.'

        from .ai_router import route_message
        result = route_message(
            message_text=message_text,
            integration=integration,
            chatbot_config=chatbot,
            input_type=input_type,
            image_data=image_data,
            image_mime=image_mime,
        )

        if result.get('success') and result.get('reply'):
            reply_text = result['reply']
            # Append checkout link if it was a purchase intent and not already in reply
            checkout = result.get('checkout_link', '')
            if checkout and result.get('intent') == 'purchase_intent' and checkout not in reply_text:
                reply_text += f"\n\n👉 Order here: {checkout}"

            # Send the AI reply via the channel's messaging API
            delivered = True
            if contact and contact.meta_id:
                send_fn = send_whatsapp_message if integration.channel_type == 'whatsapp' else send_meta_message
                delivered, send_error = send_fn(integration, contact.meta_id, reply_text)
                if not delivered:
                    logger.error(f"AI auto-reply could not be delivered: {send_error}")

            # Save the AI reply as an outbound message in the conversation
            CRMMessage.objects.create(
                conversation=conversation,
                sender=f"Trendy AI ({result.get('model_used', 'ai')})",
                body=reply_text,
                is_outbound=True,
                is_ai=True,
                ai_intent=result.get('intent', '') or '',
                status='sent' if delivered else 'failed',
            )
            conversation.last_message = reply_text
            conversation.updated_at = timezone.now()
            conversation.save(update_fields=['last_message', 'updated_at'])

            logger.info(
                f"AI auto-reply sent | intent={result.get('intent')} | "
                f"model={result.get('model_used')} | channel={integration.channel_type} | "
                f"attempt={attempt + 1}"
            )

            # The customer has an answer, so any warning about the bot failing to
            # produce one is now false. Retiring it is the whole point of tracking
            # the chip as a condition rather than a log line.
            _cancel_ai_failure_chip(conversation, note='the AI replied')

            # Bill the reply against this business's AI credits so the balance and
            # credit history on the Chatbot page reflect real usage.
            from .views import charge_ai_credits
            charge_ai_credits(
                chatbot,
                action='auto_reply',
                model_used=result.get('model_used', ''),
                description=f"{integration.channel_type}: {message_text}",
            )

            _record_bot_status(chatbot, error=None)

            # If the AI flagged a handoff trigger, auto-create a support ticket.
            # Only on a successful route — a failed API call shouldn't spawn
            # tickets on every retry.
            if result.get('open_ticket'):
                try:
                    from .models import CRMTicket
                    CRMTicket.objects.create(
                        title=f"Product Issue from {contact.name if contact else 'Customer'}",
                        description=f"Message: {message_text}",
                        contact=contact,
                        priority='medium',
                        status='open',
                    )
                    logger.info("Auto-created support ticket for product issue")
                except Exception as e:
                    logger.error(f"Failed to auto-create ticket: {e}")

            return True, ''

        reason = result.get('error') or 'The AI returned an empty reply.'
        logger.error(
            f"AI auto-reply not generated | success={result.get('success')} | "
            f"attempt={attempt + 1} | error={reason}"
        )
        # Surface it on the Chatbot page and in the conversation itself, and line
        # up a retry when the failure is the kind that clears by itself. Without
        # the chip the agent sees the customer's message sitting unanswered with
        # no clue the bot even tried, which is how a provider outage turns into a
        # lost lead; without the retry, a 30-second rate limit costs a reply.
        _record_ai_failure(
            conversation, chatbot, reason,
            # A manual retry is the agent asking for one attempt now, not for the
            # background ladder to start over on their behalf.
            retryable=bool(result.get('retryable')) and not manual,
            attempt=attempt,
            message_text=message_text,
            attachment=attachment,
            integration=integration,
        )
        return False, reason

    except Exception as e:
        logger.exception(f"AI auto-reply attempt failed: {e}")
        return False, str(e)


def retry_ai_reply_from_event(event):
    """
    Runs the Retry AI action behind a failure chip, using the message the bot
    originally choked on rather than whatever happens to be last in the thread.

    Synchronous: the agent clicked a button and is waiting to find out whether
    the customer got an answer. Returns (ok, reason).
    """
    from .models import CRMMessage

    if not event.is_open_event or event.event_kind != CRMMessage.EVENT_AI_FAILURE:
        return False, 'That alert has already been cleared.'

    data = event.event_data or {}
    conversation = event.conversation
    integration = (
        CRMIntegration.objects.filter(pk=data.get('integration_id')).first()
        or conversation.integration
    )
    if not integration:
        return False, 'The channel this chat came from is no longer connected.'

    message_text = data.get('message_text') or ''
    if not message_text:
        return False, "The original message is no longer available to retry."

    return _attempt_ai_reply(
        integration,
        _conversation_contact(conversation),
        conversation,
        message_text,
        attachment=data.get('attachment'),
        # Counts as one more attempt on the same failure, so the chip's history
        # ("retried 3 times") stays truthful across manual and automatic tries.
        attempt=event.retry_count + 1,
        manual=True,
    )


def _resolve_integration_by_page(page_id):
    """Find the FB/IG integration that owns the given page id."""
    if not page_id:
        return None
    page_id = str(page_id)
    qs = CRMIntegration.objects.filter(
        channel_type__in=['facebook', 'instagram'], status__in=LIVE_INTEGRATION_STATUSES
    )
    # Fast path: account_name stores "Name (page_id)"
    integ = qs.filter(account_name__contains=f"({page_id})").first()
    if integ:
        return integ
    # Fallback: compare the parsed id property
    for candidate in qs:
        if str(candidate.parsed_id) == page_id:
            return candidate
    return None


def handle_feed_comment_webhook(page_id, value):
    """
    Process a single Facebook Page 'feed' webhook change of item type 'comment'.

    Resolves the integration by page id, ensures the parent post exists locally,
    then runs it through _process_comment — which, for a NEW top-level comment,
    fires both the Comment-to-DM funnel and the keyword CommentAutomation rules
    (public reply + optional private DM).

    Only 'add' verbs are processed, and comments authored by the page itself are
    ignored so our own auto-replies don't trigger an endless loop.
    """
    try:
        if not isinstance(value, dict):
            return
        if value.get('item') != 'comment':
            return
        if value.get('verb') not in ('add',):
            return

        from_data = value.get('from') or {}
        sender_id = str(from_data.get('id') or '')
        # Ignore the page's own comments/replies (prevents auto-reply loops)
        if sender_id and str(page_id) and sender_id == str(page_id):
            return

        integration = _resolve_integration_by_page(page_id)
        if not integration:
            logger.warning(f"feed webhook: no connected integration for page {page_id}")
            return

        post_meta_id = value.get('post_id')
        comment_id = value.get('comment_id')
        if not post_meta_id or not comment_id:
            logger.warning("feed webhook: missing post_id or comment_id")
            return

        post = CRMSocialPost.objects.filter(meta_post_id=post_meta_id).first()
        if not post:
            # Parent post not synced yet — try a sync, then fall back to a stub.
            try:
                sync_meta_posts(integration)
            except Exception:
                logger.exception("feed webhook: sync_meta_posts failed")
            post = CRMSocialPost.objects.filter(meta_post_id=post_meta_id).first()
        if not post:
            post = CRMSocialPost.objects.create(
                integration=integration,
                meta_post_id=post_meta_id,
                message='',
                created_time=timezone.now(),
            )

        # parent_id == post_id means this is a top-level comment
        parent_id = value.get('parent_id')
        if parent_id in (None, '', post_meta_id):
            parent_id = None

        created_str = None
        created_ts = value.get('created_time')
        if created_ts:
            try:
                from datetime import datetime, timezone as _dt_tz
                created_str = datetime.fromtimestamp(int(created_ts), tz=_dt_tz.utc).isoformat()
            except Exception:
                created_str = None

        comment_data = {
            'id': comment_id,
            'from': from_data,
            'message': value.get('message', ''),
            'created_time': created_str,
        }
        _process_comment(post, comment_data, parent_id)
        logger.info(
            f"feed webhook processed comment {comment_id} on post {post_meta_id} (page {page_id})"
        )
    except Exception as e:
        logger.exception(f"handle_feed_comment_webhook failed: {e}")


def trigger_comment_to_dm(comment, integration):
    """
    Entry point for the Comment-to-DM sales funnel.
    Called whenever a NEW comment is saved from a webhook.
    Checks if automation is enabled for this page, then fires the full 3-step funnel.

    Args:
        comment: CRMSocialComment instance
        integration: CRMIntegration instance
    """
    try:
        from .ai_router import process_comment_to_dm
        from .models import CRMChatbotConfig
        chatbot = integration.chatbot_config
        
        if not chatbot:
            logger.info("No chatbot assigned to channel account - skipping Comment-to-DM funnel")
            return

        if not chatbot.is_active:
            logger.info(f"Chatbot '{chatbot.name}' is inactive — skipping Comment-to-DM funnel")
            return

        result = process_comment_to_dm(comment, integration, chatbot_config=chatbot)
        logger.info(
            f"Comment-to-DM funnel result | comment={comment.meta_comment_id} | "
            f"public_reply={result.get('public_reply_sent')} | dm={result.get('dm_sent')} | "
            f"intent={result.get('intent')} | model={result.get('model_used')}"
        )
    except Exception as e:
        logger.exception(f"trigger_comment_to_dm failed: {e}")

