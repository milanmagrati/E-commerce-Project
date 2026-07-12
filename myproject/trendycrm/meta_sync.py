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
            conversation.save(update_fields=['last_message', 'updated_at'])

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
        'fields': 'id,message,created_time,full_picture,likes.summary(true),comments.summary(true)',
        'limit': 25
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            posts = response.json().get('data', [])
            for p in posts:
                created_time_str = p.get('created_time')
                created_at = parse(created_time_str) if created_time_str else timezone.now()
                
                likes_count = p.get('likes', {}).get('summary', {}).get('total_count', 0)
                comments_count = p.get('comments', {}).get('summary', {}).get('total_count', 0)
                
                CRMSocialPost.objects.update_or_create(
                    meta_post_id=p.get('id'),
                    defaults={
                        'integration': integration,
                        'message': p.get('message', ''),
                        'picture_url': p.get('full_picture', ''),
                        'created_time': created_at,
                        'likes_count': likes_count,
                        'comments_count': comments_count
                    }
                )
    except Exception as e:
        logger.exception("Failed to sync meta posts")

def sync_meta_comments(post):
    if not post.integration.access_token:
        return

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{post.meta_post_id}/comments"
    params = {
        'access_token': post.integration.access_token,
        'fields': 'id,from,message,created_time,like_count,user_likes,can_hide,comments{id,from,message,created_time,like_count}',
        'limit': 100,
        'filter': 'stream',
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

def _process_comment(post, comment_data, parent_id):
    from_data = comment_data.get('from') or {}
    sender_name = (from_data.get('name') or '').strip()
    sender_id = (from_data.get('id') or '').strip()

    # Build a display name — never show raw 'Unknown'
    if not sender_name:
        if sender_id:
            # Use a friendly short ID e.g. "FB User ·7893"
            sender_name = f"Facebook User"
        else:
            sender_name = "Facebook User"

    created_time_str = comment_data.get('created_time')
    created_at = parse(created_time_str) if created_time_str else timezone.now()

    parent_comment = None
    if parent_id:
        parent_comment = CRMSocialComment.objects.filter(meta_comment_id=parent_id).first()

    defaults = {
        'post': post,
        'parent_comment': parent_comment,
        'sender_id': sender_id,
        'message': comment_data.get('message', ''),
        'created_time': created_at,
        'like_count': comment_data.get('like_count', 0),
    }

    obj, created = CRMSocialComment.objects.get_or_create(
        meta_comment_id=comment_data.get('id'),
        defaults={**defaults, 'sender_name': sender_name}
    )

    if not created:
        for k, v in defaults.items():
            setattr(obj, k, v)
        if sender_name != 'Facebook User' or obj.sender_name in ('Unknown', 'Facebook User', ''):
            obj.sender_name = sender_name
        obj.save()

def reply_to_meta_comment(integration, comment_id, message_text):
    if not integration.access_token:
        return False
        
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}/comments"
    params = {'access_token': integration.access_token}
    payload = {'message': message_text}
    
    try:
        response = requests.post(url, params=params, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send comment reply: {e}")
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
