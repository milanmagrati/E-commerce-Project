from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.conf import settings
from django.contrib import messages
import json
import requests
import logging

logger = logging.getLogger(__name__)

def build_absolute_callback(path_name):
    return f"{settings.SITE_URL}{reverse(path_name)}"
from django.utils.timezone import now
import json
import requests
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import urlencode

from .meta_sync import sync_meta_conversations, _process_meta_conversation
from .models import (
    CRMContact, CRMConversation, CRMMessage,
    CRMQuickReply, CRMChatbotConfig, CRMIntegration, CRMTicket
)


# ─── Home ────────────────────────────────────────────────────────────────────
@login_required
def crm_home(request):
    integrations = CRMIntegration.objects.all()
    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)
    total_contacts = CRMContact.objects.count()
    open_tickets = CRMTicket.objects.filter(status='open').count()
    total_conversations = CRMConversation.objects.count()
    connected_channels = integrations.filter(status='connected').count()

    setup_steps = [
        {
            'icon': 'fa-plug',
            'title': 'Connect a social channel',
            'desc': 'Connect any one — WhatsApp, Instagram, Facebook, TikTok, Gmail, Outlook or Zoho Mail — and you\'re set.',
            'action_label': 'Connect channel',
            'action_url': 'trendycrm:integrations',
            'done': connected_channels > 0,
        },
        {
            'icon': 'fa-robot',
            'title': 'Train your AI',
            'desc': 'Add your business identity — name, support email, phone, and an about-us blurb — so the AI can introduce you.',
            'action_label': 'Open knowledge base',
            'action_url': 'trendycrm:chatbot',
            'done': bool(chatbot.business_name),
        },
        {
            'icon': 'fa-comments',
            'title': 'Start a conversation',
            'desc': 'Open a new conversation with a contact and respond from a single unified inbox.',
            'action_label': 'Open conversations',
            'action_url': 'trendycrm:conversations',
            'done': total_conversations > 0,
        },
        {
            'icon': 'fa-user-plus',
            'title': 'Add your first contact',
            'desc': 'Import or manually add contacts so your team can manage leads and customers in one place.',
            'action_label': 'View contacts',
            'action_url': 'trendycrm:contacts',
            'done': total_contacts > 0,
        },
        {
            'icon': 'fa-power-off',
            'title': 'Turn on Trendy AI',
            'desc': 'Master switch. AI starts handling new conversations on connected channels.',
            'action_label': 'Open AI settings',
            'action_url': 'trendycrm:chatbot',
            'done': chatbot.is_active,
        },
    ]
    done_count = sum(1 for s in setup_steps if s['done'])

    context = {
        'setup_steps': setup_steps,
        'done_count': done_count,
        'total_steps': len(setup_steps),
        'chatbot': chatbot,
        'total_contacts': total_contacts,
        'open_tickets': open_tickets,
        'total_conversations': total_conversations,
        'connected_channels': connected_channels,
        'crm_section': 'home',
    }
    return render(request, 'trendycrm/home.html', context)


# ─── Conversations ────────────────────────────────────────────────────────────
@login_required
def crm_conversations(request):
    # Try to auto-sync connected meta channels when opening the inbox
    try:
        active_integrations = CRMIntegration.objects.filter(channel_type__in=['facebook', 'instagram'], status='connected')
        for integ in active_integrations:
            sync_meta_conversations(integ)
    except Exception as e:
        logger.exception("Failed auto-sync on inbox load")

    conversations = CRMConversation.objects.select_related('contact', 'assigned_to', 'integration').all().order_by('-updated_at')
    
    page_filter = request.GET.get('page_filter')
    if page_filter:
        conversations = conversations.filter(integration_id=page_filter)
        
    connected_integrations = CRMIntegration.objects.filter(status='connected')

    active_conv = None
    conv_id = request.GET.get('id')
    messages = []
    if conv_id:
        active_conv = get_object_or_404(CRMConversation, pk=conv_id)
        messages = active_conv.messages.all()

    contacts = CRMContact.objects.all()

    context = {
        'conversations': conversations,
        'active_conv': active_conv,
        'chat_messages': messages,
        'contacts': contacts,
        'connected_integrations': connected_integrations,
        'page_filter': int(page_filter) if page_filter and page_filter.isdigit() else None,
        'crm_section': 'conversations',
    }
    return render(request, 'trendycrm/conversations.html', context)

@login_required
def crm_conversations_ajax(request):
    """
    Returns only the HTML fragment for the active conversation's messages,
    used for the real-time auto-fetch polling.
    """
    conv_id = request.GET.get('id')
    if not conv_id:
        return HttpResponse("")
        
    active_conv = get_object_or_404(CRMConversation, pk=conv_id)
    messages = active_conv.messages.all()
    
    context = {
        'chat_messages': messages,
    }
    return render(request, 'trendycrm/partials/messages_list.html', context)


@login_required
@require_POST
def crm_send_message(request, conv_id):
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    body = request.POST.get('body', '').strip()
    if body:
        CRMMessage.objects.create(
            conversation=conv,
            sender=request.user.get_full_name() or request.user.username,
            body=body,
            is_outbound=True,
        )
        conv.last_message = body
        conv.save(update_fields=['last_message', 'updated_at'])
        
        # Send to Meta if applicable
        if conv.channel in ['facebook', 'instagram'] and conv.contact and conv.contact.meta_id:
            from .models import CRMIntegration
            from .meta_sync import send_meta_message
            if conv.integration:
                send_meta_message(conv.integration, conv.contact.meta_id, body)
            else:
                integrations = CRMIntegration.objects.filter(channel_type=conv.channel, status='connected')
                for integration in integrations:
                    if send_meta_message(integration, conv.contact.meta_id, body):
                        break
                
    from django.urls import reverse
    return redirect(f"{reverse('trendycrm:conversations')}?id={conv_id}")


@login_required
@require_POST
def crm_create_conversation(request):
    from django.urls import reverse
    contact_id = request.POST.get('contact_id')
    contact_name = request.POST.get('contact_name', '').strip()
    channel = request.POST.get('channel', 'web')
    integration_id = request.POST.get('integration_id')
    first_message = request.POST.get('first_message', '').strip()
    
    contact = None
    if contact_id:
        contact = CRMContact.objects.filter(pk=contact_id).first()
    elif contact_name:
        contact, _ = CRMContact.objects.get_or_create(
            name=contact_name,
            defaults={'created_by': request.user}
        )
        
    integration = None
    if integration_id:
        integration = CRMIntegration.objects.filter(pk=integration_id).first()
        
    if contact:
        conv = CRMConversation.objects.create(
            contact=contact,
            channel=channel,
            integration=integration,
            assigned_to=request.user,
            last_message=first_message
        )
        
        if first_message:
            CRMMessage.objects.create(
                conversation=conv,
                sender=request.user.get_full_name() or request.user.username,
                body=first_message,
                is_outbound=True
            )
            
        return redirect(f"{reverse('trendycrm:conversations')}?id={conv.pk}")
    
    return redirect('trendycrm:conversations')


# ─── Chatbot ─────────────────────────────────────────────────────────────────
@login_required
def crm_chatbot(request):
    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)
    integrations = CRMIntegration.objects.all()

    if request.method == 'POST':
        chatbot.is_active = 'is_active' in request.POST
        chatbot.business_name = request.POST.get('business_name', chatbot.business_name)
        chatbot.business_email = request.POST.get('business_email', chatbot.business_email)
        chatbot.business_phone = request.POST.get('business_phone', chatbot.business_phone)
        chatbot.about_blurb = request.POST.get('about_blurb', chatbot.about_blurb)
        chatbot.welcome_message = request.POST.get('welcome_message', chatbot.welcome_message)
        chatbot.save()
        return redirect('trendycrm:chatbot')

    context = {
        'chatbot': chatbot,
        'integrations': integrations,
        'crm_section': 'chatbot',
    }
    return render(request, 'trendycrm/chatbot.html', context)


@login_required
@require_POST
def crm_chatbot_toggle(request):
    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)
    chatbot.is_active = not chatbot.is_active
    chatbot.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'is_active': chatbot.is_active})


# ─── Quick Replies ────────────────────────────────────────────────────────────
@login_required
def crm_quick_replies(request):
    replies = CRMQuickReply.objects.all()
    context = {
        'replies': replies,
        'crm_section': 'quick_replies',
    }
    return render(request, 'trendycrm/quick_replies.html', context)


@login_required
def crm_quick_reply_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        shortcut = request.POST.get('shortcut', '').strip()
        content = request.POST.get('content', '').strip()
        category = request.POST.get('category', '').strip()
        if name and shortcut and content:
            CRMQuickReply.objects.create(
                name=name,
                shortcut=shortcut,
                content=content,
                category=category or None,
                created_by=request.user,
            )
    return redirect('trendycrm:quick_replies')


@login_required
def crm_quick_reply_delete(request, pk):
    reply = get_object_or_404(CRMQuickReply, pk=pk)
    reply.delete()
    return redirect('trendycrm:quick_replies')


# ─── Integrations ─────────────────────────────────────────────────────────────
INTEGRATION_META = {
    'whatsapp': {'label': 'WhatsApp', 'icon': 'fa-whatsapp', 'color': '#25D366', 'group': 'MESSAGING CHANNELS'},
    'instagram': {'label': 'Instagram', 'icon': 'fa-instagram', 'color': '#E1306C', 'group': 'MESSAGING CHANNELS'},
    'facebook': {'label': 'Facebook Messenger', 'icon': 'fa-facebook-messenger', 'color': '#0084FF', 'group': 'MESSAGING CHANNELS'},
    'tiktok': {'label': 'TikTok', 'icon': 'fa-tiktok', 'color': '#010101', 'group': 'MESSAGING CHANNELS'},
    'gmail': {'label': 'Gmail', 'icon': 'fa-envelope', 'color': '#EA4335', 'group': 'MESSAGING CHANNELS'},
    'outlook': {'label': 'Outlook', 'icon': 'fa-envelope-open', 'color': '#0078D4', 'group': 'MESSAGING CHANNELS'},
    'zoho_mail': {'label': 'Zoho Mail', 'icon': 'fa-mail-bulk', 'color': '#E42527', 'group': 'MESSAGING CHANNELS'},
}


@login_required
def crm_integrations(request):
    # Ensure at least one integration record exists per channel for UI
    for key in INTEGRATION_META:
        if not CRMIntegration.objects.filter(channel_type=key).exists():
            CRMIntegration.objects.create(channel_type=key)

    integrations = CRMIntegration.objects.all()
    active_key = request.GET.get('channel', 'instagram')
    active_integrations = integrations.filter(channel_type=active_key)
    connected_integrations = active_integrations.filter(status='connected')
    active_meta = INTEGRATION_META.get(active_key, {})

    channels_with_meta = []
    
    # We want one sidebar item per channel type
    for key, meta in INTEGRATION_META.items():
        # check if this channel has any connected integrations
        is_connected = integrations.filter(channel_type=key, status='connected').exists()
        channels_with_meta.append({
            'channel_type': key,
            'meta': meta,
            'is_active': key == active_key,
            'is_connected': is_connected,
        })

    # Get permissions for active channel
    active_perms = []
    if active_key == 'instagram':
        active_perms = [
            ('Manage messages', 'Read and reply to Direct Messages.'),
            ('Read engagement', 'Access comments on your posts and media.'),
            ('Manage comments', 'Reply to or hide comments on your posts.'),
            ('Public profile', 'Access your username and profile picture.'),
        ]
    elif active_key == 'whatsapp':
        active_perms = [
            ('Send messages', 'Send text and template messages.'),
            ('Receive messages', 'Receive incoming WhatsApp messages.'),
            ('Manage templates', 'Create and manage message templates.'),
        ]
    elif active_key == 'facebook':
        active_perms = [
            ('Manage Messenger', 'Send and receive Facebook messages.'),
            ('Read page inbox', 'Access messages in your Page inbox.'),
            ('Manage page posts', 'Moderate comments on page posts.'),
        ]

    if active_key == 'facebook':
        fb_oauth_url = reverse('trendycrm:facebook_connect')
    elif active_key == 'instagram':
        fb_oauth_url = reverse('trendycrm:instagram_connect')
    elif active_key == 'tiktok':
        fb_oauth_url = reverse('trendycrm:tiktok_connect')
    else:
        fb_oauth_url = ''

    context = {
        'channels_with_meta': channels_with_meta,
        'active_integrations': active_integrations,
        'connected_integrations': connected_integrations,
        'active_meta': active_meta,
        'active_key': active_key,
        'active_perms': active_perms,
        'fb_oauth_url': fb_oauth_url,
        'crm_section': 'integrations',
    }
    return render(request, 'trendycrm/integrations.html', context)


@login_required
@require_POST
def crm_integration_connect(request, channel_key):
    # This handles the manual connect form submission
    account_name = request.POST.get('account_name', '').strip()
    access_token = request.POST.get('access_token', '').strip()
    verify_token = request.POST.get('verify_token', '').strip()

    if account_name and access_token:
        # Create a new integration or use an empty one
        integ = CRMIntegration.objects.filter(channel_type=channel_key, status='not_connected').first()
        if not integ:
            integ = CRMIntegration(channel_type=channel_key)
            
        integ.status = 'connected'
        integ.account_name = account_name
        integ.access_token = access_token
        if verify_token:
            integ.meta['verify_token'] = verify_token
        integ.connected_at = timezone.now()
        integ.save()

    return redirect(reverse('trendycrm:integrations') + f"?channel={channel_key}")

@login_required
@require_POST
def crm_integration_disconnect(request, pk):
    integ = get_object_or_404(CRMIntegration, pk=pk)
    channel_key = integ.channel_type
    
    # Check if there are other integrations for this channel
    other_exists = CRMIntegration.objects.filter(channel_type=channel_key).exclude(pk=pk).exists()
    
    if other_exists:
        integ.delete()
    else:
        # Keep at least one empty record for the UI
        integ.status = 'not_connected'
        integ.account_name = None
        integ.access_token = None
        integ.meta = {}
        integ.connected_at = None
        integ.save()
        
    return redirect(reverse('trendycrm:integrations') + f"?channel={channel_key}")


def connect_facebook(request):
    redirect_uri = build_absolute_callback('trendycrm:facebook_callback')
    params = {
        "client_id": getattr(settings, 'FACEBOOK_APP_ID', '873948152450056'),
        "redirect_uri": redirect_uri,
        "scope": "pages_show_list,pages_manage_metadata,pages_messaging,instagram_basic,instagram_manage_messages",
        "response_type": "code",
    }
    from urllib.parse import urlencode
    auth_url = "https://www.facebook.com/v25.0/dialog/oauth?" + urlencode(params)
    return redirect(auth_url)

def connect_instagram(request):
    redirect_uri = build_absolute_callback('trendycrm:instagram_callback')
    params = {
        "client_id": getattr(settings, 'FACEBOOK_APP_ID', '873948152450056'),
        "redirect_uri": redirect_uri,
        "scope": "pages_show_list,pages_manage_metadata,pages_messaging,instagram_basic,instagram_manage_messages",
        "response_type": "code",
    }
    from urllib.parse import urlencode
    auth_url = "https://www.facebook.com/v25.0/dialog/oauth?" + urlencode(params)
    return redirect(auth_url)

def connect_tiktok(request):
    # Dummy TikTok connect logic to bypass query string issue
    redirect_uri = build_absolute_callback('trendycrm:tiktok_callback')
    # If there was a real TikTok APP ID:
    # client_key = getattr(settings, 'TIKTOK_APP_ID', 'YOUR_TIKTOK_KEY')
    # auth_url = f"https://www.tiktok.com/v2/auth/authorize/?client_key={client_key}&response_type=code&scope=user.info.basic&redirect_uri={redirect_uri}"
    # But for now we just redirect immediately to callback for testing the flow
    return redirect(f"{redirect_uri}?code=dummy_tiktok_code")

def _handle_oauth_callback(request, channel_key):
    # If user denied access, Facebook redirects with error=access_denied
    if 'error' in request.GET:
        logger.warning(f"OAuth error for {channel_key}: {request.GET.get('error_description', request.GET.get('error'))}")
    elif 'code' in request.GET:
        code = request.GET['code']
        fb_client_id = getattr(settings, 'FACEBOOK_APP_ID', getattr(settings, 'FACEBOOK_CLIENT_ID', '873948152450056'))
        fb_app_secret = getattr(settings, 'FACEBOOK_APP_SECRET', '')
        route_name = f'trendycrm:{channel_key}_callback'
        fb_redirect_uri = build_absolute_callback(route_name)
        
        graph_api_version = 'v25.0'
        token_exchange_url = f"https://graph.facebook.com/{graph_api_version}/oauth/access_token?client_id={fb_client_id}&redirect_uri={fb_redirect_uri}&client_secret={fb_app_secret}&code={code}"
        
        try:
            # 1. Exchange code for user access token
            token_resp = requests.get(token_exchange_url)
            if token_resp.status_code == 200:
                token_data = token_resp.json()
                user_access_token = token_data.get('access_token')
                
                if user_access_token:
                    # 2. Fetch pages the user has access to
                    accounts_url = f"https://graph.facebook.com/{graph_api_version}/me/accounts?access_token={user_access_token}"
                    accounts_resp = requests.get(accounts_url)
                    
                    if accounts_resp.status_code == 200:
                        pages = accounts_resp.json().get('data', [])
                        
                        if pages:
                            primary_page = pages[0]
                            integ = CRMIntegration.objects.filter(channel_type=channel_key, status='not_connected').first()
                            if not integ:
                                integ = CRMIntegration(channel_type=channel_key)
                            integ.status = 'connected'
                            page_name = primary_page.get('name', 'Meta Page')
                            page_id = primary_page.get('id', '')
                            integ.account_name = f"{page_name} ({page_id})" if page_id else page_name
                            integ.access_token = primary_page.get('access_token', user_access_token)
                            integ.connected_at = timezone.now()
                            integ.save()
                            logger.info(f"Successfully connected {channel_key} page: {page_name}")
                            
                            # Trigger initial sync of conversations
                            try:
                                sync_meta_conversations(integ)
                            except Exception as e:
                                logger.exception("Failed to run initial sync for meta messages.")
                                
                            messages.success(request, f"{channel_key.title()} connected successfully")
                        else:
                            logger.warning("No professional pages found during Facebook OAuth.")
                            messages.error(request, "No professional pages found.")
                    else:
                        logger.error(f"Failed to fetch accounts: {accounts_resp.text}")
                        messages.error(request, "Failed to fetch accounts from Meta.")
            else:
                logger.error(f"Failed to exchange token: {token_resp.text}")
                messages.error(request, "Failed to exchange token with Meta.")
        except Exception as e:
            logger.exception("Error during Facebook OAuth token exchange")
            messages.error(request, "An unexpected error occurred during connection.")
            
    redirect_url = reverse('trendycrm:integrations') + f"?channel={channel_key}"
    return HttpResponse(f"""
    <html><body>
    <script>
        if (window.opener && !window.opener.closed) {{
            window.opener.location.href = "{redirect_url}";
            window.close();
        }} else {{
            window.location.href = "{redirect_url}";
        }}
    </script>
    <p>Authentication complete. You can close this window.</p>
    </body></html>
    """)

def facebook_callback(request):
    return _handle_oauth_callback(request, 'facebook')

def instagram_callback(request):
    return _handle_oauth_callback(request, 'instagram')

def tiktok_callback(request):
    # Temporary mock connection for TikTok since we just need the explicit route to work
    integ = CRMIntegration.objects.filter(channel_type='tiktok', status='not_connected').first()
    if not integ:
        integ = CRMIntegration(channel_type='tiktok')
    
    if integ:
        integ.status = 'connected'
        integ.account_name = 'TikTok Page'
        integ.connected_at = timezone.now()
        integ.save()
    
    redirect_url = reverse('trendycrm:integrations') + "?channel=tiktok"
    return HttpResponse(f"""
    <html><body>
    <script>
        if (window.opener && !window.opener.closed) {{
            window.opener.location.href = "{redirect_url}";
            window.close();
        }} else {{
            window.location.href = "{redirect_url}";
        }}
    </script>
    <p>Authentication complete. You can close this window.</p>
    </body></html>
    """)


# ─── Webhooks ─────────────────────────────────────────────────────────────────
@csrf_exempt
def meta_webhook(request):
    """
    Webhook endpoint to receive real-time messages from Meta (Facebook/Instagram).
    """
    if request.method == 'GET':
        # Verification request from Meta
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        # In a real app, verify_token should match a secret in settings
        if mode == 'subscribe' and token:
            return HttpResponse(challenge, status=200)
        return HttpResponse('Verification failed', status=403)
        
    elif request.method == 'POST':
        try:
            payload = json.loads(request.body)
            # Trigger a sync for all connected facebook/instagram integrations
            integrations = CRMIntegration.objects.filter(channel_type__in=['facebook', 'instagram'], status='connected')
            for integ in integrations:
                sync_meta_conversations(integ)
            return HttpResponse('EVENT_RECEIVED', status=200)
        except Exception as e:
            logger.exception("Error processing Meta webhook")
            return HttpResponse('ERROR', status=500)
    
    return HttpResponse('Method Not Allowed', status=405)


# ─── Contacts ─────────────────────────────────────────────────────────────────
@login_required
def crm_contacts(request):
    contacts = CRMContact.objects.all()
    context = {
        'contacts': contacts,
        'crm_section': 'contacts',
    }
    return render(request, 'trendycrm/contacts.html', context)


# ─── Analytics ────────────────────────────────────────────────────────────────
@login_required
def crm_analytics(request):
    context = {
        'crm_section': 'analytics',
        'total_contacts': CRMContact.objects.count(),
        'total_conversations': CRMConversation.objects.count(),
        'open_tickets': CRMTicket.objects.filter(status='open').count(),
        'resolved_tickets': CRMTicket.objects.filter(status='resolved').count(),
        'quick_replies': CRMQuickReply.objects.count(),
    }
    return render(request, 'trendycrm/analytics.html', context)


# ─── Tickets ──────────────────────────────────────────────────────────────────
@login_required
def crm_tickets(request):
    tickets = CRMTicket.objects.select_related('contact', 'assigned_to').all()
    context = {
        'tickets': tickets,
        'crm_section': 'tickets',
    }
    return render(request, 'trendycrm/tickets.html', context)
