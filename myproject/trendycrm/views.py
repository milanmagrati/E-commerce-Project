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
    conversations = CRMConversation.objects.select_related('contact', 'assigned_to').all()
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
        'messages': messages,
        'contacts': contacts,
        'crm_section': 'conversations',
    }
    return render(request, 'trendycrm/conversations.html', context)


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
    return redirect(f"{request.path}?id={conv_id}")


@login_required
@require_POST
def crm_create_conversation(request):
    from django.urls import reverse
    contact_id = request.POST.get('contact_id')
    contact_name = request.POST.get('contact_name', '').strip()
    channel = request.POST.get('channel', 'web')
    first_message = request.POST.get('first_message', '').strip()
    
    contact = None
    if contact_id:
        contact = CRMContact.objects.filter(pk=contact_id).first()
    elif contact_name:
        contact, _ = CRMContact.objects.get_or_create(
            name=contact_name,
            defaults={'created_by': request.user}
        )
        
    if contact:
        conv = CRMConversation.objects.create(
            contact=contact,
            channel=channel,
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
    # Ensure all integration records exist
    for key in INTEGRATION_META:
        CRMIntegration.objects.get_or_create(channel_type=key)

    integrations = CRMIntegration.objects.all()
    active_key = request.GET.get('channel', 'instagram')
    active_integration = integrations.filter(channel_type=active_key).first()
    active_meta = INTEGRATION_META.get(active_key, {})

    channels_with_meta = []
    for integ in integrations:
        meta = INTEGRATION_META.get(integ.channel_type, {})
        channels_with_meta.append({
            'obj': integ,
            'meta': meta,
            'is_active': integ.channel_type == active_key,
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

    fb_redirect_uri = request.build_absolute_uri(reverse('trendycrm:integration_oauth_callback', args=['facebook']))
    
    fb_client_id = getattr(settings, 'FACEBOOK_CLIENT_ID', '3765824723663820')
    fb_oauth_url = f"https://www.facebook.com/v24.0/dialog/oauth?client_id={fb_client_id}&redirect_uri={fb_redirect_uri}&scope=pages_manage_metadata,pages_read_engagement,pages_messaging,instagram_basic,instagram_manage_messages"

    context = {
        'channels_with_meta': channels_with_meta,
        'active_integration': active_integration,
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
    integ = get_object_or_404(CRMIntegration, channel_type=channel_key)
    if 'disconnect' in request.POST:
        integ.status = 'not_connected'
        integ.account_name = None
        integ.access_token = None
        integ.meta = {}
        integ.connected_at = None
    else:
        account_name = request.POST.get('account_name', '').strip()
        access_token = request.POST.get('access_token', '').strip()
        verify_token = request.POST.get('verify_token', '').strip()

        if account_name and access_token:
            integ.status = 'connected'
            integ.account_name = account_name
            integ.access_token = access_token
            if verify_token:
                integ.meta['verify_token'] = verify_token
            integ.connected_at = timezone.now()
            
    integ.save()
    return redirect(reverse('trendycrm:integrations') + f"?channel={channel_key}")


@login_required
def crm_integration_oauth_callback(request, channel_key):
    if channel_key in ['facebook', 'instagram']:
        # If user denied access, Facebook redirects with error=access_denied
        if 'error' in request.GET:
            logger.warning(f"OAuth error for {channel_key}: {request.GET.get('error_description', request.GET.get('error'))}")
        elif 'code' in request.GET:
            # We have an authorization code. For a real app, exchange this code for an access token.
            # Here we use the environment token as a fallback/mock for the demo.
            env_token = getattr(settings, 'META_PAGE_ACCESS_TOKEN', '')
            if env_token:
                integ = get_object_or_404(CRMIntegration, channel_type=channel_key)
                resp = requests.get(f"https://graph.facebook.com/v20.0/me?access_token={env_token}")
                if resp.status_code == 200:
                    data = resp.json()
                    integ.status = 'connected'
                    page_name = data.get('name', 'Meta Page')
                    page_id = data.get('id', '')
                    integ.account_name = f"{page_name} ({page_id})" if page_id else page_name
                    integ.access_token = env_token
                    integ.connected_at = timezone.now()
                    integ.save()
                else:
                    logger.error("Failed to fetch Facebook page in oauth callback: %s", resp.text)
                
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
