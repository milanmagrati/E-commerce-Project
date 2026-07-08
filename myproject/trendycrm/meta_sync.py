import requests
import logging
from django.utils import timezone
from dateutil.parser import parse
from .models import CRMIntegration, CRMContact, CRMConversation, CRMMessage

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
    
    # Alternatively we can just fetch /me/conversations if we use the Page token
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/conversations"
    params = {
        'access_token': integration.access_token,
        'fields': 'id,updated_time,participants,messages.limit(20){id,message,created_time,from,to}',
        'limit': 10
    }
    
    try:
        response = requests.get(url, params=params)
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
            
        # Create message (we don't have a unique msg_id field on CRMMessage, so we might get duplicates if we sync multiple times,
        # but for this MVP sync we'll check if a message with exact body and time exists)
        if not CRMMessage.objects.filter(conversation=conversation, body=body, sender=sender_name).exists():
            CRMMessage.objects.create(
                conversation=conversation,
                sender=sender_name,
                body=body,
                is_outbound=is_outbound,
                created_at=created_at
            )
            
            # Update last_message
            conversation.last_message = body
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
        response = requests.post(url, params=params, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send Meta message: {e} - Response: {getattr(e.response, 'text', '')}")
        return False
