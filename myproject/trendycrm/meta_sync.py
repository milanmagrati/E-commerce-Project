import requests
import logging
from django.utils import timezone
from dateutil.parser import parse
from .models import CRMIntegration, CRMContact, CRMConversation, CRMMessage, CRMSocialPost, CRMSocialComment

logger = logging.getLogger(__name__)


GRAPH_API_VERSION = "v25.0"

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
        'fields': 'id,updated_time,participants,messages.limit(20){id,message,created_time,from,to}',
        'limit': 10
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            logger.error(f"Failed to fetch conversations from Meta: {response.text}")
            return
            
        data = response.json()
        conversations = data.get('data', [])
        
        for conv_data in conversations:
            _process_meta_conversation(integration, conv_data)
            
    except Exception as e:
        logger.exception("Exception occurred while syncing Meta conversations.")

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
        
    contact_name = contact_data.get('name', 'Unknown User')
    contact_meta_id = contact_data.get('id')
    
    # Create or get Contact (using a rudimentary matching by name for now, in a real system we'd use Meta ID)
    contact, created = CRMContact.objects.get_or_create(
        name=contact_name,
        defaults={'status': 'new', 'meta_id': contact_meta_id}
    )
    if not created and contact_meta_id and contact.meta_id != contact_meta_id:
        contact.meta_id = contact_meta_id
        contact.save(update_fields=['meta_id'])
    
    # For Facebook Pages, the token is a Page Access Token.
    page_id = integration.account_name.split('(')[-1].strip(')') if '(' in integration.account_name else None
    
    # Create or get Conversation
    conversation, conv_created = CRMConversation.objects.get_or_create(
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
    if conversation.integration != integration:
        conversation.integration = integration
        conversation.save(update_fields=['integration'])
    
    # Process Messages (they come ordered newest to oldest, so we reverse to save them oldest to newest)
    messages.reverse()
    for msg in messages:
        msg_id = msg.get('id')
        body = msg.get('message', '')
        if not body:
            continue
            
        if body.startswith("You are responding to a user comment to a post on your Page."):
            continue
            
        sender_name = msg.get('from', {}).get('name', 'Unknown')
        is_outbound = (sender_name == our_page_name)
        
        created_time_str = msg.get('created_time')
        try:
            created_at = parse(created_time_str) if created_time_str else timezone.now()
        except:
            created_at = timezone.now()
            
        from datetime import timedelta
        # Check if message already exists to avoid duplicates.
        # For outbound messages, the local sender name is the CRM user, but Facebook returns the Page name.
        time_threshold_start = created_at - timedelta(minutes=1)
        time_threshold_end = created_at + timedelta(minutes=1)
        
        if is_outbound:
            exists = CRMMessage.objects.filter(
                conversation=conversation, 
                body=body, 
                is_outbound=True,
                created_at__range=(time_threshold_start, time_threshold_end)
            ).exists()
        else:
            exists = CRMMessage.objects.filter(
                conversation=conversation, 
                body=body, 
                sender=sender_name,
                is_outbound=False,
                created_at__range=(time_threshold_start, time_threshold_end)
            ).exists()
            
        if not exists:
            CRMMessage.objects.create(
                conversation=conversation,
                sender=sender_name,
                body=body,
                is_outbound=is_outbound,
                created_at=created_at
            )
            
            # Update last_message
            conversation.last_message = body
            # Only update updated_at if the new message is newer than current updated_at or if it doesn't exist
            if not conversation.updated_at or created_at > conversation.updated_at:
                conversation.updated_at = created_at
            if not is_outbound:
                conversation.is_read = False
            conversation.save(update_fields=['last_message', 'updated_at', 'is_read'])

def send_meta_message(integration, recipient_id, message_text):
    """
    Sends a message via the Meta Graph API to the recipient PSID.
    """
    if not integration.access_token:
        logger.error(f"Cannot send Meta message for {integration.channel_type}: No access token.")
        return False

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    params = {'access_token': integration.access_token}
    payload = {
        'recipient': {'id': recipient_id},
        'messaging_type': 'RESPONSE',
        'message': {'text': message_text}
    }
    
    try:
        response = requests.post(url, params=params, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send Meta message: {e} - Response: {getattr(e.response, 'text', '')}")
        return False

def sync_meta_posts(integration):
    if not integration.access_token:
        return
        
    page_id = integration.parsed_id
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/posts" if page_id else f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/posts"
    params = {
        'access_token': integration.access_token,
        'fields': 'id,message,created_time,full_picture,comments.limit(10).summary(true).order(reverse_chronological){id,from,message,created_time},reactions.type(LIKE).limit(0).summary(total_count).as(like),reactions.type(LOVE).limit(0).summary(total_count).as(love),reactions.type(HAHA).limit(0).summary(total_count).as(haha),reactions.type(WOW).limit(0).summary(total_count).as(wow),reactions.type(SAD).limit(0).summary(total_count).as(sad),reactions.type(ANGRY).limit(0).summary(total_count).as(angry),reactions.type(CARE).limit(0).summary(total_count).as(care)',
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
                my_reaction = old_post.reactions_data.get('my_reaction') if old_post and isinstance(old_post.reactions_data, dict) else None
                
                reactions_data = {
                    'LIKE': p.get('like', {}).get('summary', {}).get('total_count', 0),
                    'LOVE': p.get('love', {}).get('summary', {}).get('total_count', 0),
                    'HAHA': p.get('haha', {}).get('summary', {}).get('total_count', 0),
                    'WOW': p.get('wow', {}).get('summary', {}).get('total_count', 0),
                    'SAD': p.get('sad', {}).get('summary', {}).get('total_count', 0),
                    'ANGRY': p.get('angry', {}).get('summary', {}).get('total_count', 0),
                    'CARE': p.get('care', {}).get('summary', {}).get('total_count', 0),
                }
                if my_reaction:
                    reactions_data['my_reaction'] = my_reaction
                    
                likes_count = sum(v for k, v in reactions_data.items() if k != 'my_reaction' and isinstance(v, (int, float)))
                
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
        'fields': 'id,from,message,created_time,can_hide,reactions.type(LIKE).limit(0).summary(total_count).as(like),reactions.type(LOVE).limit(0).summary(total_count).as(love),reactions.type(HAHA).limit(0).summary(total_count).as(haha),reactions.type(WOW).limit(0).summary(total_count).as(wow),reactions.type(SAD).limit(0).summary(total_count).as(sad),reactions.type(ANGRY).limit(0).summary(total_count).as(angry),reactions.type(CARE).limit(0).summary(total_count).as(care),comments{id,from,message,created_time,reactions.type(LIKE).limit(0).summary(total_count).as(like),reactions.type(LOVE).limit(0).summary(total_count).as(love),reactions.type(HAHA).limit(0).summary(total_count).as(haha),reactions.type(WOW).limit(0).summary(total_count).as(wow),reactions.type(SAD).limit(0).summary(total_count).as(sad),reactions.type(ANGRY).limit(0).summary(total_count).as(angry),reactions.type(CARE).limit(0).summary(total_count).as(care)}',
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
    my_reaction = old_comment.reactions_data.get('my_reaction') if old_comment and isinstance(old_comment.reactions_data, dict) else None

    reactions_data = {
        'LIKE': comment_data.get('like', {}).get('summary', {}).get('total_count', 0),
        'LOVE': comment_data.get('love', {}).get('summary', {}).get('total_count', 0),
        'HAHA': comment_data.get('haha', {}).get('summary', {}).get('total_count', 0),
        'WOW': comment_data.get('wow', {}).get('summary', {}).get('total_count', 0),
        'SAD': comment_data.get('sad', {}).get('summary', {}).get('total_count', 0),
        'ANGRY': comment_data.get('angry', {}).get('summary', {}).get('total_count', 0),
        'CARE': comment_data.get('care', {}).get('summary', {}).get('total_count', 0),
    }
    if my_reaction:
        reactions_data['my_reaction'] = my_reaction
        
    like_count = sum(v for k, v in reactions_data.items() if k != 'my_reaction' and isinstance(v, (int, float)))

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
    Returns boolean indicating success.
    """
    if not integration.access_token:
        return False
        
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}/private_replies"
    params = {'access_token': integration.access_token}
    payload = {'message': message_text}
    
    try:
        response = requests.post(url, params=params, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send private reply for comment {comment_id}: {e}")
        return False
        
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


# ─── AI Router Integration ─────────────────────────────────────────────────────
def process_incoming_webhook_message(integration, contact, conversation, message_text, is_outbound=False):
    """
    Called when a new INBOUND message arrives via webhook.
    If the chatbot is active and the message is inbound (from customer),
    routes the message through the Multi-Model AI Engine and sends an auto-reply.

    Args:
        integration: CRMIntegration instance (the page that received the message)
        contact: CRMContact instance
        conversation: CRMConversation instance
        message_text: str — the customer's raw message text
        is_outbound: bool — only process inbound (customer) messages
    """
    if is_outbound:
        return  # Never auto-reply to our own outbound messages

    try:
        from .models import CRMChatbotConfig, CRMMessage
        chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)

        if not chatbot.is_active:
            logger.info("Chatbot is inactive — skipping AI auto-reply")
            return

        # Check that this channel is enabled for auto-reply
        enabled_channels = chatbot.auto_reply_channels or {}
        channel_key = integration.channel_type
        if not enabled_channels.get(channel_key, False):
            logger.info(f"Auto-reply disabled for channel: {channel_key}")
            return

        from .ai_router import route_message
        result = route_message(
            message_text=message_text,
            integration=integration,
            chatbot_config=chatbot,
            input_type='text',
        )

        if result.get('success') and result.get('reply'):
            reply_text = result['reply']
            # Append checkout link if it was a purchase intent and not already in reply
            checkout = result.get('checkout_link', '')
            if checkout and result.get('intent') == 'purchase_intent' and checkout not in reply_text:
                reply_text += f"\n\n👉 Order here: {checkout}"

            # Send the AI reply via Meta API
            if contact and contact.meta_id:
                send_meta_message(integration, contact.meta_id, reply_text)

            # Save the AI reply as an outbound message in the conversation
            CRMMessage.objects.create(
                conversation=conversation,
                sender=f"Trendy AI ({result.get('model_used', 'ai')})",
                body=reply_text,
                is_outbound=True,
            )
            conversation.last_message = reply_text
            conversation.updated_at = timezone.now()
            conversation.save(update_fields=['last_message', 'updated_at'])

            logger.info(
                f"AI auto-reply sent | intent={result.get('intent')} | "
                f"model={result.get('model_used')} | channel={channel_key}"
            )

        # If the AI flagged a product issue, auto-create a support ticket
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

    except Exception as e:
        logger.exception(f"process_incoming_webhook_message failed: {e}")


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
        chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)

        if not chatbot.is_active:
            logger.info("Chatbot is inactive — skipping Comment-to-DM funnel")
            return

        result = process_comment_to_dm(comment, integration, chatbot_config=chatbot)
        logger.info(
            f"Comment-to-DM funnel result | comment={comment.meta_comment_id} | "
            f"public_reply={result.get('public_reply_sent')} | dm={result.get('dm_sent')} | "
            f"intent={result.get('intent')} | model={result.get('model_used')}"
        )
    except Exception as e:
        logger.exception(f"trigger_comment_to_dm failed: {e}")

