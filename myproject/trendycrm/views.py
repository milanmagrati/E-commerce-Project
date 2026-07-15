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
import base64
import hashlib
import os

logger = logging.getLogger(__name__)

def build_absolute_callback(request, path_name):
    site_url = getattr(settings, 'SITE_URL', None)
    if site_url:
        site_url = site_url.rstrip('/')
        url = f"{site_url}{reverse(path_name)}"
    else:
        url = request.build_absolute_uri(reverse(path_name))
        
    # Facebook strictly requires localhost (or https), it blocks 127.0.0.1
    if '127.0.0.1' in url:
        url = url.replace('127.0.0.1', 'localhost')
    return url
from django.utils.timezone import now
import json
import requests
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import urlencode

from .meta_sync import (
    sync_meta_conversations, _process_meta_conversation,
    sync_meta_posts, sync_meta_comments, reply_to_meta_comment, hide_meta_comment, delete_meta_comment
)
from .models import (
    CRMContact, CRMConversation, CRMMessage,
    CRMQuickReply, CRMChatbotConfig, CRMIntegration, CRMTicket,
    CRMSocialPost, CRMSocialComment, CRMPageProfile, CRMCreditLog,
    CRMLabel, CRMNote, CommentAutomation
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
            'icon': 'fa-tools',
            'title': 'Configure page profiles',
            'desc': 'Customize the tone, FAQ, and specific auto-replies for each connected social page.',
            'action_label': 'Manage profiles',
            'action_url': 'trendycrm:page_profiles',
            'done': CRMPageProfile.objects.filter(is_active=True).exists(),
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
        active_conv = CRMConversation.objects.filter(pk=conv_id).first()
        if active_conv:
            messages = active_conv.messages.all()
        else:
            from django.shortcuts import redirect
            return redirect('trendycrm:conversations')

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
    
    # Throttle sync to every 15 seconds to allow local testing without webhooks
    import time
    if active_conv.integration:
        last_sync = request.session.get(f'last_sync_{active_conv.integration.pk}', 0)
        if time.time() - last_sync > 15:
            from .meta_sync import sync_meta_conversations
            try:
                sync_meta_conversations(active_conv.integration)
                request.session[f'last_sync_{active_conv.integration.pk}'] = time.time()
            except Exception as e:
                logger.error(f"Ajax auto-sync failed: {e}")

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
        msg = CRMMessage.objects.create(
            conversation=conv,
            sender=request.user.get_full_name() or request.user.username,
            body=body,
            is_outbound=True,
        )
        conv.last_message = body
        conv.updated_at = msg.created_at
        conv.save(update_fields=['last_message', 'updated_at'])
        
        # Send to Meta if applicable
        if conv.channel in ['facebook', 'instagram'] and conv.contact and conv.contact.meta_id:
            from .models import CRMIntegration
            from .meta_sync import send_meta_message
            success = False
            if conv.integration:
                success = send_meta_message(conv.integration, conv.contact.meta_id, body)
            else:
                integrations = CRMIntegration.objects.filter(channel_type=conv.channel, status='connected')
                for integration in integrations:
                    if send_meta_message(integration, conv.contact.meta_id, body):
                        success = True
                        break
            
            if not success:
                msg.status = 'failed'
                msg.save(update_fields=['status'])
                messages.error(request, "Failed to send message to Meta. Please check your page connection and permissions.")
                
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
        # Check if conversation already exists to prevent duplication
        conv = CRMConversation.objects.filter(contact=contact, channel=channel).first()
        if conv:
            # Update the integration/assigned user if necessary
            if integration and conv.integration != integration:
                conv.integration = integration
            conv.assigned_to = request.user
            conv.last_message = first_message
            conv.save()
        else:
            conv = CRMConversation.objects.create(
                contact=contact,
                channel=channel,
                integration=integration,
                assigned_to=request.user,
                last_message=first_message
            )
        
        if first_message:
            msg = CRMMessage.objects.create(
                conversation=conv,
                sender=request.user.get_full_name() or request.user.username,
                body=first_message,
                is_outbound=True
            )
            conv.updated_at = msg.created_at
            conv.save(update_fields=['updated_at'])
            
        return redirect(f"{reverse('trendycrm:conversations')}?id={conv.pk}")
    
    return redirect('trendycrm:conversations')
    
@login_required
@require_POST
def crm_delete_conversation(request, conv_id):
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    conv.delete()
    messages.success(request, "Conversation deleted successfully.")
    return redirect('trendycrm:conversations')


# ─── Conversation Sidebar Endpoints ──────────────────────────────────────────

@login_required
@require_POST
def crm_link_contact(request, conv_id):
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    contact_id = request.POST.get('contact_id')
    new_contact_name = request.POST.get('contact_name', '').strip()
    
    if contact_id:
        contact = get_object_or_404(CRMContact, pk=contact_id)
    elif new_contact_name:
        contact = CRMContact.objects.create(name=new_contact_name, created_by=request.user)
    else:
        return JsonResponse({'status': 'error', 'message': 'Must provide contact ID or new name'}, status=400)
        
    conv.contact = contact
    conv.save()
    
    return JsonResponse({
        'status': 'ok',
        'contact': {
            'id': contact.pk,
            'name': contact.name,
            'email': contact.email or '',
            'phone': contact.phone or '',
            'company': contact.company or '',
        }
    })

@login_required
@require_POST
def crm_add_label(request, conv_id):
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    label_name = request.POST.get('name', '').strip()
    color_hex = request.POST.get('color_hex', '#7c3aed').strip()
    
    if not label_name:
        return JsonResponse({'status': 'error', 'message': 'Label name required'}, status=400)
        
    label, _ = CRMLabel.objects.get_or_create(
        name=label_name,
        defaults={'color_hex': color_hex}
    )
    conv.labels.add(label)
    
    return JsonResponse({
        'status': 'ok',
        'label': {
            'id': label.pk,
            'name': label.name,
            'color_hex': label.color_hex
        }
    })

@login_required
@require_POST
def crm_remove_label(request, conv_id):
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    label_id = request.POST.get('label_id')
    
    if not label_id:
        return JsonResponse({'status': 'error', 'message': 'Label ID required'}, status=400)
        
    label = get_object_or_404(CRMLabel, pk=label_id)
    conv.labels.remove(label)
    
    return JsonResponse({'status': 'ok'})

@login_required
@require_POST
def crm_add_note(request, conv_id):
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    text = request.POST.get('text', '').strip()
    
    if not text:
        return JsonResponse({'status': 'error', 'message': 'Note text required'}, status=400)
        
    note = CRMNote.objects.create(
        conversation=conv,
        author=request.user,
        text=text
    )
    
    return JsonResponse({
        'status': 'ok',
        'note': {
            'id': note.pk,
            'text': note.text,
            'author_name': note.author.first_name or note.author.username,
            'created_at': note.created_at.strftime("%b %d, %Y, %I:%M %p")
        }
    })


# ─── Chatbot ─────────────────────────────────────────────────────────────────
@login_required
def crm_chatbot(request):

    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)
    integrations = CRMIntegration.objects.all()
    recent_logs = CRMCreditLog.objects.all()[:20]

    context = {
        'chatbot': chatbot,
        'integrations': integrations,
        'recent_logs': recent_logs,
        'crm_section': 'chatbot',
    }
    return render(request, 'trendycrm/chatbot.html', context)


@login_required
@require_POST
def crm_chatbot_toggle(request):
    """AJAX: Toggle the global AI on/off switch."""
    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)
    chatbot.is_active = not chatbot.is_active
    chatbot.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'is_active': chatbot.is_active})


@login_required
@require_POST
def crm_chatbot_save_knowledge(request):
    """
    AJAX: Save the 5-section Business Knowledge Base.
    Saves: about_blurb (identity), tone_voice, offerings, faq_text, playbook,
           business_name, business_email, business_phone, welcome_message.
    """
    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)

    chatbot.business_name = request.POST.get('business_name', '').strip() or chatbot.business_name
    chatbot.business_email = request.POST.get('business_email', '').strip() or chatbot.business_email
    chatbot.business_phone = request.POST.get('business_phone', '').strip() or chatbot.business_phone
    chatbot.about_blurb = request.POST.get('about_blurb', '').strip() or None
    chatbot.welcome_message = request.POST.get('welcome_message', '').strip() or None
    chatbot.tone_voice = request.POST.get('tone_voice', '').strip() or None
    chatbot.offerings = request.POST.get('offerings', '').strip() or None
    chatbot.faq_text = request.POST.get('faq_text', '').strip() or None
    chatbot.playbook = request.POST.get('playbook', '').strip() or None
    chatbot.save()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok', 'message': 'Business knowledge saved successfully!'})
    messages.success(request, 'Business knowledge saved!')
    return redirect('trendycrm:chatbot')


@login_required
@require_POST
def crm_chatbot_save_agent(request):
    """
    AJAX: Save agent configuration (AI model, tone, API keys).
    """
    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)

    chatbot.ai_model = request.POST.get('ai_model', chatbot.ai_model)
    chatbot.response_tone = request.POST.get('response_tone', chatbot.response_tone)

    # Only update API keys if a non-empty value is provided (don't overwrite with blank)
    openai_key = request.POST.get('openai_api_key', '').strip()
    gemini_key = request.POST.get('gemini_api_key', '').strip()
    if openai_key:
        chatbot.openai_api_key = openai_key
    if gemini_key:
        chatbot.gemini_api_key = gemini_key

    chatbot.save()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok', 'message': 'Agent configuration saved!'})
    messages.success(request, 'Agent configuration saved!')
    return redirect('trendycrm:chatbot')


@login_required
@require_POST
def crm_chatbot_toggle_channel(request):
    """
    AJAX: Toggle auto-reply for a specific channel on/off.
    POST body: { channel_type: 'facebook', enabled: 'true'/'false' }
    """
    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)
    channel_type = request.POST.get('channel_type', '')
    enabled = request.POST.get('enabled', 'false') == 'true'

    if channel_type:
        channels = chatbot.auto_reply_channels or {}
        channels[channel_type] = enabled
        chatbot.auto_reply_channels = channels
        chatbot.save(update_fields=['auto_reply_channels', 'updated_at'])
        return JsonResponse({'status': 'ok', 'channel': channel_type, 'enabled': enabled})

    return JsonResponse({'status': 'error', 'message': 'channel_type required'}, status=400)


@login_required
def crm_credit_history(request):
    """AJAX: Returns JSON list of recent credit log entries."""
    logs = CRMCreditLog.objects.all()[:50]
    data = [
        {
            'id': l.pk,
            'action': l.get_action_display(),
            'credits_used': l.credits_used,
            'model_used': l.model_used or '—',
            'description': l.description or '',
            'created_at': l.created_at.strftime('%b %d, %Y %H:%M'),
            'is_topup': l.credits_used > 0,
        }
        for l in logs
    ]
    chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)
    return JsonResponse({'logs': data, 'balance': chatbot.ai_credits})


# ─── Quick Replies ────────────────────────────────────────────────
@login_required
def crm_quick_replies(request):
    q = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    replies = CRMQuickReply.objects.all()
    if q:
        replies = replies.filter(name__icontains=q) | replies.filter(shortcut__icontains=q) | replies.filter(content__icontains=q)
    if category:
        replies = replies.filter(category__iexact=category)
    categories = CRMQuickReply.objects.exclude(category__isnull=True).exclude(category='').values_list('category', flat=True).distinct()
    context = {
        'replies': replies,
        'categories': list(categories),
        'search_q': q,
        'active_category': category,
        'crm_section': 'quick_replies',
    }
    return render(request, 'trendycrm/quick_replies.html', context)


@login_required
@require_POST
def crm_quick_reply_create(request):
    name = request.POST.get('name', '').strip()
    shortcut = request.POST.get('shortcut', '').strip().lstrip('/')
    content = request.POST.get('content', '').strip()
    category = request.POST.get('category', '').strip()

    if not (name and shortcut and content):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'status': 'error', 'message': 'Name, shortcut, and content are required.'}, status=400)
        messages.error(request, 'Name, shortcut, and content are required.')
        return redirect('trendycrm:quick_replies')

    if CRMQuickReply.objects.filter(shortcut=shortcut).exists():
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'status': 'error', 'message': f'Shortcut "/{shortcut}" already exists.'}, status=400)
        messages.error(request, f'Shortcut "/{shortcut}" already exists. Please use a different one.')
        return redirect('trendycrm:quick_replies')

    new_reply = CRMQuickReply.objects.create(
        name=name,
        shortcut=shortcut,
        content=content,
        category=category or None,
        created_by=request.user,
    )
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'status': 'ok',
            'reply': {
                'pk': new_reply.pk,
                'name': new_reply.name,
                'shortcut': new_reply.shortcut,
                'content': new_reply.content,
                'category': new_reply.category or '',
            }
        })

    messages.success(request, f'Quick reply "{name}" created successfully!')
    return redirect('trendycrm:quick_replies')


@login_required
@require_POST
def crm_quick_reply_edit(request, pk):
    """AJAX: Edit an existing quick reply."""
    reply = get_object_or_404(CRMQuickReply, pk=pk)
    name = request.POST.get('name', '').strip()
    shortcut = request.POST.get('shortcut', '').strip().lstrip('/')
    content = request.POST.get('content', '').strip()
    category = request.POST.get('category', '').strip()

    if not (name and shortcut and content):
        return JsonResponse({'status': 'error', 'message': 'Name, shortcut and content are required.'}, status=400)

    if CRMQuickReply.objects.filter(shortcut=shortcut).exclude(pk=pk).exists():
        return JsonResponse({'status': 'error', 'message': f'Shortcut "/{shortcut}" already in use.'}, status=400)

    reply.name = name
    reply.shortcut = shortcut
    reply.content = content
    reply.category = category or None
    reply.save()

    return JsonResponse({
        'status': 'ok',
        'reply': {
            'pk': reply.pk,
            'name': reply.name,
            'shortcut': reply.shortcut,
            'content': reply.content,
            'category': reply.category or '',
        }
    })


@login_required
def crm_quick_reply_delete(request, pk):
    reply = get_object_or_404(CRMQuickReply, pk=pk)
    name = reply.name
    reply.delete()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok'})
    messages.success(request, f'Quick reply "{name}" deleted.')
    return redirect('trendycrm:quick_replies')


@login_required
def crm_quick_replies_search(request):
    """AJAX: Search quick replies by shortcut prefix for autocomplete in conversations."""
    q = request.GET.get('q', '').strip().lstrip('/')
    if not q:
        return JsonResponse({'results': []})
    replies = CRMQuickReply.objects.filter(
        shortcut__istartswith=q
    ).values('pk', 'name', 'shortcut', 'content', 'category')[:10]
    return JsonResponse({'results': list(replies)})


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
    redirect_uri = build_absolute_callback(request, 'trendycrm:facebook_callback')
    params = {
        "client_id": getattr(settings, 'FACEBOOK_APP_ID', '873948152450056'),
        "redirect_uri": redirect_uri,
        "scope": "pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement,pages_read_user_content,pages_manage_engagement,instagram_basic,instagram_manage_messages",
        "response_type": "code",
        "auth_type": "rerequest",
    }
    from urllib.parse import urlencode
    auth_url = "https://www.facebook.com/v25.0/dialog/oauth?" + urlencode(params)
    return redirect(auth_url)

def connect_instagram(request):
    redirect_uri = build_absolute_callback(request, 'trendycrm:instagram_callback')
    params = {
        "client_id": getattr(settings, 'FACEBOOK_APP_ID', '873948152450056'),
        "redirect_uri": redirect_uri,
        "scope": "pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement,pages_read_user_content,pages_manage_engagement,instagram_basic,instagram_manage_messages",
        "response_type": "code",
        "auth_type": "rerequest",
    }
    from urllib.parse import urlencode
    auth_url = "https://www.facebook.com/v25.0/dialog/oauth?" + urlencode(params)
    return redirect(auth_url)

def connect_tiktok(request):
    redirect_uri = build_absolute_callback(request, 'trendycrm:tiktok_callback')
    tiktok_client_id = getattr(settings, 'TIKTOK_CLIENT_ID', '')
    if not tiktok_client_id:
        messages.error(request, 'TikTok Client ID is not configured in settings.')
        return redirect(reverse('trendycrm:integrations') + "?channel=tiktok")
        
    # Generate PKCE code verifier and challenge
    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    
    # Store verifier in session for token exchange
    request.session['tiktok_code_verifier'] = code_verifier
        
    # Redirect to TikTok standard Login Kit authorization page
    auth_url = f"https://www.tiktok.com/v2/auth/authorize/?client_key={tiktok_client_id}&response_type=code&scope=user.info.basic,video.list&redirect_uri={redirect_uri}&state=tiktok_auth&code_challenge={code_challenge}&code_challenge_method=S256"
    return redirect(auth_url)

def _handle_oauth_callback(request, channel_key):
    # If user denied access, Facebook redirects with error=access_denied
    if 'error' in request.GET:
        logger.warning(f"OAuth error for {channel_key}: {request.GET.get('error_description', request.GET.get('error'))}")
    elif 'code' in request.GET:
        code = request.GET['code']
        fb_client_id = getattr(settings, 'FACEBOOK_APP_ID', getattr(settings, 'FACEBOOK_CLIENT_ID', '873948152450056'))
        fb_app_secret = getattr(settings, 'FACEBOOK_APP_SECRET', '')
        route_name = f'trendycrm:{channel_key}_callback'
        fb_redirect_uri = build_absolute_callback(request, route_name)
        
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
                            connected_count = 0
                            for page in pages:
                                page_access_token = page.get('access_token')
                                if not page_access_token:
                                    continue
                                    
                                page_name = page.get('name', 'Meta Page')
                                page_id = page.get('id', '')
                                account_name = f"{page_name} ({page_id})" if page_id else page_name
                                
                                # Check if already connected
                                integ = CRMIntegration.objects.filter(channel_type=channel_key, account_name=account_name).first()
                                if not integ:
                                    # Use a not_connected one or create new
                                    integ = CRMIntegration.objects.filter(channel_type=channel_key, status='not_connected').first()
                                    if not integ:
                                        integ = CRMIntegration(channel_type=channel_key)
                                        
                                integ.status = 'connected'
                                integ.account_name = account_name
                                integ.access_token = page_access_token
                                integ.connected_at = timezone.now()
                                integ.save()
                                logger.info(f"Successfully connected {channel_key} page: {page_name}")
                                connected_count += 1
                                
                                # Trigger initial sync of conversations
                                try:
                                    sync_meta_conversations(integ)
                                except Exception as e:
                                    logger.exception("Failed to run initial sync for meta messages.")
                            
                            if connected_count > 0:
                                messages.success(request, f"Successfully connected {connected_count} {channel_key.title()} page(s)")
                            else:
                                messages.error(request, "No pages with valid permissions were found. Please ensure you grant messaging permissions.")
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
    return redirect(redirect_url)

def facebook_callback(request):
    return _handle_oauth_callback(request, 'facebook')

def instagram_callback(request):
    return _handle_oauth_callback(request, 'instagram')

def tiktok_callback(request):
    code = request.GET.get('code')
    if not code:
        # User might have denied access, resulting in error being returned
        err = request.GET.get('error_description', 'TikTok authorization failed.')
        messages.error(request, err)
        return redirect(reverse('trendycrm:integrations') + "?channel=tiktok")
        
    tiktok_client_id = getattr(settings, 'TIKTOK_CLIENT_ID', '')
    tiktok_client_secret = getattr(settings, 'TIKTOK_CLIENT_SECRET', '')
    redirect_uri = build_absolute_callback(request, 'trendycrm:tiktok_callback')
    
    # Exchange code for access token using standard TikTok Login Kit endpoint
    url = "https://open.tiktokapis.com/v2/oauth/token/"
    
    # Retrieve code_verifier from session
    code_verifier = request.session.get('tiktok_code_verifier', '')
    
    payload = {
        "client_key": tiktok_client_id,
        "client_secret": tiktok_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        resp = requests.post(url, data=payload, headers=headers)
        data = resp.json()
        
        # TikTok API v2 sometimes nests the response inside a 'data' key
        token_data = data.get('data', data)
        
        if 'access_token' in token_data:
            access_token = token_data['access_token']
            open_id = token_data.get('open_id', '')
            refresh_token = token_data.get('refresh_token', '')
            expires_in = token_data.get('expires_in', 0)
            
            integ = CRMIntegration.objects.filter(channel_type='tiktok', status='not_connected').first()
            if not integ:
                integ = CRMIntegration(channel_type='tiktok')
            
            integ.status = 'connected'
            integ.account_name = f'TikTok Account ({open_id})' if open_id else 'TikTok Account'
            integ.access_token = access_token
            integ.connected_at = timezone.now()
            
            # Store refresh token in meta field if present
            if refresh_token:
                meta = integ.meta or {}
                meta['refresh_token'] = refresh_token
                meta['expires_in'] = expires_in
                integ.meta = meta
                
            integ.save()
            messages.success(request, 'TikTok connected successfully.')
        else:
            err_msg = data.get('message', data.get('error_description', data.get('error', 'Unknown error')))
            messages.error(request, f"TikTok error: {err_msg}")
    except Exception as e:
        messages.error(request, f"Error connecting to TikTok: {str(e)}")
        
    redirect_url = reverse('trendycrm:integrations') + "?channel=tiktok"
    return redirect(redirect_url)


# ─── Webhooks ─────────────────────────────────────────────────────────────────
@csrf_exempt
def meta_webhook(request):
    """
    Webhook endpoint to receive real-time messages from Meta (Facebook/Instagram).
    """
    if request.method == 'GET':
        # Verification request from Meta
        # Verification request from Meta
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        # In a real app, verify_token should match a secret in settings
        if mode == 'subscribe' and token:
            return HttpResponse(challenge, status=200)
        return HttpResponse('Verification failed', status=403)
        
    elif request.method == 'POST':
        # Verify Meta Signature for security
        signature = request.headers.get('X-Hub-Signature-256', '')
        if not signature.startswith('sha256='):
            return HttpResponse('Invalid signature', status=403)
            
        import hmac
        import hashlib
        
        expected_signature = 'sha256=' + hmac.new(
            settings.FACEBOOK_APP_SECRET.encode('utf-8'),
            request.body,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_signature):
            return HttpResponse('Signature mismatch', status=403)
            
        try:
            payload = json.loads(request.body)
            
            # Process webhook events for read/delivery receipts
            if 'entry' in payload:
                for entry in payload['entry']:
                    if 'messaging' in entry:
                        for event in entry['messaging']:
                            sender_id = event.get('sender', {}).get('id')
                            if not sender_id:
                                continue
                                
                            import datetime
                            
                            # Handle read receipts
                            if 'read' in event:
                                watermark = event['read'].get('watermark')
                                if watermark:
                                    watermark_dt = datetime.datetime.fromtimestamp(watermark / 1000.0, tz=timezone.utc)
                                    CRMMessage.objects.filter(
                                        conversation__contact__meta_id=sender_id,
                                        is_outbound=True,
                                        created_at__lte=watermark_dt
                                    ).exclude(status='read').update(status='read')
                            
                            # Handle delivery receipts
                            if 'delivery' in event:
                                watermark = event['delivery'].get('watermark')
                                if watermark:
                                    watermark_dt = datetime.datetime.fromtimestamp(watermark / 1000.0, tz=timezone.utc)
                                    CRMMessage.objects.filter(
                                        conversation__contact__meta_id=sender_id,
                                        is_outbound=True,
                                        created_at__lte=watermark_dt,
                                        status='sent'
                                    ).update(status='delivered')

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
from django.http import JsonResponse

# ─── Social Posts & Comments ──────────────────────────────────────────────────
@login_required
def crm_social_posts(request):
    integrations = CRMIntegration.objects.filter(channel_type__in=['facebook', 'instagram'], status='connected')
    for integration in integrations:
        sync_meta_posts(integration)
        _backfill_facebook_user_names(integration)
        
    post_filter = request.GET.get('post_filter', 'all')
    if post_filter == 'follow_up':
        posts = CRMSocialPost.objects.filter(is_starred=True)
    else:
        posts = CRMSocialPost.objects.all()
    
    page_filters = request.GET.getlist('page_filter')
    valid_page_ids = []
    if page_filters and 'all' not in page_filters:
        for pf in page_filters:
            if pf.isdigit():
                valid_page_ids.append(int(pf))
                
    if valid_page_ids:
        posts = posts.filter(integration_id__in=valid_page_ids)
    
    selected_post_id = request.GET.get('post_id')
    active_post = None
    comments = []
    
    if selected_post_id:
        active_post = get_object_or_404(CRMSocialPost, pk=selected_post_id)
        sync_meta_comments(active_post)
        
        # Simple sorting and filtering
        filter_status = request.GET.get('filter', 'all')
        sort_by = request.GET.get('sort', '-created_time')
        
        qs = active_post.comments.filter(parent_comment__isnull=True)
        if filter_status == 'hidden':
            qs = qs.filter(visibility_status='hidden')
        elif filter_status == 'spam':
            qs = qs.filter(visibility_status='spam')
        elif filter_status == 'unread':
            qs = qs.filter(workflow_status='open')
        else:
            qs = qs.exclude(visibility_status='spam')
            
        comments = qs.order_by(sort_by)
        
    page_name = ''
    if active_post:
        page_name = active_post.integration.parsed_name or ''

    context = {
        'crm_section': 'social_posts',
        'posts': posts,
        'active_post': active_post,
        'comments': comments,
        'page_name': page_name,
        'current_filter': request.GET.get('filter', 'all'),
        'current_sort': request.GET.get('sort', '-created_time'),
        'current_post_filter': post_filter,
        'integrations': integrations,
        'page_filters': valid_page_ids if valid_page_ids else ['all'],
    }
    return render(request, 'trendycrm/social_posts.html', context)

@login_required
@require_POST
def crm_social_post_action(request, post_id):
    post = get_object_or_404(CRMSocialPost, pk=post_id)
    action = request.POST.get('action')
    
    if action == 'toggle_star':
        post.is_starred = not post.is_starred
        post.save(update_fields=['is_starred'])
        return JsonResponse({'status': 'ok', 'is_starred': post.is_starred})
    
    elif action == 'toggle_review':
        post.is_reviewed = not post.is_reviewed
        post.save(update_fields=['is_reviewed'])
        return JsonResponse({'status': 'ok', 'is_reviewed': post.is_reviewed})
        
    elif action == 'post_comment':
        msg_text = request.POST.get('message')
        if not msg_text:
            return JsonResponse({'status': 'error', 'message': 'Message cannot be empty'}, status=400)
            
        from trendycrm.meta_sync import reply_to_meta_comment
        # Graph API uses the same endpoint for comments on a post and replies to a comment
        if reply_to_meta_comment(post.integration, post.meta_post_id, msg_text):
            return JsonResponse({'status': 'ok'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Failed to post comment to Facebook'}, status=500)
            
    elif action == 'react':
        from trendycrm.meta_sync import react_meta_object
        reaction_type = request.POST.get('reaction_type', 'LIKE')
        
        if not isinstance(post.reactions_data, dict):
            post.reactions_data = {}
            
        current_reaction = post.reactions_data.get('my_reaction')
        if current_reaction == reaction_type:
            success = react_meta_object(post.integration, post.meta_post_id, 'DELETE')
            if success:
                post.reactions_data['my_reaction'] = None
                if post.reactions_data.get(reaction_type, 0) > 0:
                    post.reactions_data[reaction_type] -= 1
                post.likes_count = sum(v for k, v in post.reactions_data.items() if k != 'my_reaction' and isinstance(v, (int, float)))
                post.save(update_fields=['reactions_data', 'likes_count'])
                return JsonResponse({'status': 'ok'})
        else:
            success = react_meta_object(post.integration, post.meta_post_id, reaction_type)
            if success:
                if current_reaction and post.reactions_data.get(current_reaction, 0) > 0:
                    post.reactions_data[current_reaction] -= 1
                post.reactions_data['my_reaction'] = reaction_type
                post.reactions_data[reaction_type] = post.reactions_data.get(reaction_type, 0) + 1
                post.likes_count = sum(v for k, v in post.reactions_data.items() if k != 'my_reaction' and isinstance(v, (int, float)))
                post.save(update_fields=['reactions_data', 'likes_count'])
                return JsonResponse({'status': 'ok'})
                
        return JsonResponse({'status': 'error', 'message': 'Failed to react to Facebook post'}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid action'}, status=400)


@login_required
@require_POST
def crm_social_action(request, comment_id):
    comment = get_object_or_404(CRMSocialComment, pk=comment_id)
    action = request.POST.get('action')
    
    if action == 'reply':
        msg_text = request.POST.get('message')
        if msg_text and reply_to_meta_comment(comment.post.integration, comment.meta_comment_id, msg_text):
            CRMSocialComment.objects.create(
                post=comment.post,
                parent_comment=comment,
                sender_name='Trendy CRM',
                message=msg_text,
                created_time=timezone.now()
            )
            messages.success(request, "Reply posted successfully.")
        else:
            messages.error(request, "Failed to post reply.")
            
    elif action == 'hide':
        if hide_meta_comment(comment.post.integration, comment.meta_comment_id, True):
            comment.visibility_status = 'hidden'
            comment.save(update_fields=['visibility_status'])
            messages.success(request, "Comment hidden.")
            
    elif action == 'unhide':
        if hide_meta_comment(comment.post.integration, comment.meta_comment_id, False):
            comment.visibility_status = 'visible'
            comment.save(update_fields=['visibility_status'])
            messages.success(request, "Comment unhidden.")
            
    elif action == 'delete':
        if delete_meta_comment(comment.post.integration, comment.meta_comment_id):
            comment.delete()
            messages.success(request, "Comment deleted.")
            return redirect(f"{reverse('trendycrm:social_posts')}?post_id={comment.post.pk}")
            
    elif action == 'mark_done':
        comment.workflow_status = 'done'
        comment.save(update_fields=['workflow_status'])
        
    elif action == 'mark_spam':
        comment.visibility_status = 'spam'
        comment.save(update_fields=['visibility_status'])
        
    elif action == 'react':
        from trendycrm.meta_sync import react_meta_object
        reaction_type = request.POST.get('reaction_type', 'LIKE')
        
        if not isinstance(comment.reactions_data, dict):
            comment.reactions_data = {}
            
        current_reaction = comment.reactions_data.get('my_reaction')
        if current_reaction == reaction_type:
            success = react_meta_object(comment.post.integration, comment.meta_comment_id, 'DELETE')
            if success:
                comment.reactions_data['my_reaction'] = None
                if comment.reactions_data.get(reaction_type, 0) > 0:
                    comment.reactions_data[reaction_type] -= 1
                comment.like_count = sum(v for k, v in comment.reactions_data.items() if k != 'my_reaction' and isinstance(v, (int, float)))
                comment.save(update_fields=['reactions_data', 'like_count'])
                return JsonResponse({'status': 'ok'})
        else:
            success = react_meta_object(comment.post.integration, comment.meta_comment_id, reaction_type)
            if success:
                if current_reaction and comment.reactions_data.get(current_reaction, 0) > 0:
                    comment.reactions_data[current_reaction] -= 1
                comment.reactions_data['my_reaction'] = reaction_type
                comment.reactions_data[reaction_type] = comment.reactions_data.get(reaction_type, 0) + 1
                comment.like_count = sum(v for k, v in comment.reactions_data.items() if k != 'my_reaction' and isinstance(v, (int, float)))
                comment.save(update_fields=['reactions_data', 'like_count'])
                return JsonResponse({'status': 'ok'})
                
        return JsonResponse({'status': 'error', 'message': 'Failed to react to Facebook comment'}, status=500)
        
    return redirect(f"{reverse('trendycrm:social_posts')}?post_id={comment.post.pk}")


# ─── Page Profiles (Centralized Knowledge Core) ────────────────────────────────
@login_required
def crm_page_profiles(request):
    """
    Dashboard view to manage Page Profiles for the Centralized Knowledge Core.
    One profile per connected integration (social page).
    """
    integrations = CRMIntegration.objects.filter(status='connected')
    profiles = {p.integration_id: p for p in CRMPageProfile.objects.all()}

    # Auto-create missing profiles for connected pages
    for integ in integrations:
        if integ.pk not in profiles:
            profile = CRMPageProfile.objects.create(integration=integ)
            profiles[integ.pk] = profile

    pages_with_profiles = [
        {'integration': integ, 'profile': profiles.get(integ.pk)}
        for integ in integrations
    ]

    context = {
        'crm_section': 'chatbot',
        'pages_with_profiles': pages_with_profiles,
    }
    return render(request, 'trendycrm/page_profiles.html', context)


@login_required
@require_POST
def crm_page_profile_save(request, integration_id):
    """
    AJAX / form POST to save a Page Profile for a specific integration.
    """
    integration = get_object_or_404(CRMIntegration, pk=integration_id)
    profile, _ = CRMPageProfile.objects.get_or_create(integration=integration)

    profile.product_name = request.POST.get('product_name', '').strip() or None
    profile.price = request.POST.get('price', '').strip() or None
    profile.brand_tone = request.POST.get('brand_tone', 'friendly')
    profile.checkout_link = request.POST.get('checkout_link', '').strip() or None
    profile.custom_faq = request.POST.get('custom_faq', '').strip() or None
    profile.comment_auto_reply_enabled = request.POST.get('comment_auto_reply_enabled') == 'on'
    profile.public_reply_template = request.POST.get('public_reply_template', '').strip() or 'Just sent the link to your DMs! \U0001f48c'
    profile.is_active = request.POST.get('is_active') == 'on'
    profile.save()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok', 'message': 'Profile saved successfully'})

    messages.success(request, f'Page profile for {integration.parsed_name or integration} saved successfully!')
    return redirect('trendycrm:page_profiles')


@login_required
@require_POST
def crm_ai_test(request):
    """
    Quick test endpoint to run a message through the AI router from the dashboard.
    Returns JSON with intent, model used, and the AI-generated reply.
    """
    message_text = request.POST.get('message', '').strip()
    integration_id = request.POST.get('integration_id')

    if not message_text:
        return JsonResponse({'error': 'Message is required'}, status=400)

    integration = None
    if integration_id:
        integration = CRMIntegration.objects.filter(pk=integration_id).first()

    try:
        from .ai_router import route_message
        chatbot, _ = CRMChatbotConfig.objects.get_or_create(pk=1)
        result = route_message(
            message_text=message_text,
            integration=integration,
            chatbot_config=chatbot,
            input_type='text',
        )
        return JsonResponse(result)
    except Exception as e:
        logger.exception("AI test failed")
        return JsonResponse({'error': str(e)}, status=500)


# ─── Comment Automation (ManyChat-style) ──────────────────────────────────────

def _check_comment_automations(integration, comment_obj):
    """
    Internal trigger: checks all active CommentAutomation rules for the given
    integration, fires matching ones (public reply + optional DM).

    Args:
        integration: CRMIntegration instance
        comment_obj: CRMSocialComment instance (the new comment)
    """
    from .meta_sync import reply_to_meta_comment, send_meta_message

    comment_text = (comment_obj.message or '').strip()
    sender_id = comment_obj.sender_id or ''
    comment_meta_id = comment_obj.meta_comment_id

    automations = CommentAutomation.objects.filter(
        integration=integration,
        is_active=True
    )

    for auto in automations:
        matched = False
        kw = auto.trigger_keyword.strip().lower()

        if auto.match_type == 'any':
            matched = True
        elif auto.match_type == 'exact':
            matched = comment_text.lower() == kw
        elif auto.match_type == 'contains':
            matched = bool(kw) and kw in comment_text.lower()

        if matched:
            try:
                page_name = integration.parsed_name or integration.account_name

                # 1. Public reply on the comment
                if auto.public_reply:
                    success, new_fb_id = reply_to_meta_comment(integration, comment_meta_id, auto.public_reply)
                    if success:
                        stable_id = new_fb_id if new_fb_id else f"auto_{auto.pk}_{comment_meta_id}"
                        CRMSocialComment.objects.get_or_create(
                            meta_comment_id=stable_id,
                            defaults={
                                'post': comment_obj.post,
                                'parent_comment': comment_obj,
                                'sender_name': page_name,
                                'message': auto.public_reply,
                                'created_time': timezone.now(),
                            }
                        )
                        logger.info(f"Auto-reply posted | rule='{auto.name}' | fb_id={stable_id} | parent={comment_meta_id}")
                    else:
                        logger.error(f"Auto-reply FAILED to post to Facebook | rule='{auto.name}' | comment={comment_meta_id}")

                # 2. Optional private DM
                if auto.send_dm and auto.dm_message:
                    from .meta_sync import reply_to_meta_comment_privately
                    try:
                        dm_success = reply_to_meta_comment_privately(integration, comment_meta_id, auto.dm_message)
                        if dm_success:
                            logger.info(f"Private Reply DM sent | rule='{auto.name}' | comment={comment_meta_id}")
                            contact_name = comment_obj.sender_name or 'Facebook User'
                            contact_meta_id = sender_id if sender_id else f"commenter_{comment_meta_id}"
                            
                            contact, _ = CRMContact.objects.get_or_create(
                                meta_id=contact_meta_id,
                                defaults={'name': contact_name}
                            )
                            page_id = integration.account_name.split('(')[-1].strip(')') if '(' in integration.account_name else None
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
                            CRMMessage.objects.create(
                                conversation=conversation,
                                sender=page_name,
                                body=auto.dm_message,
                                is_outbound=True,
                                created_at=timezone.now()
                            )
                            conversation.last_message = auto.dm_message
                            conversation.updated_at = timezone.now()
                            conversation.save(update_fields=['last_message', 'updated_at'])
                        else:
                            logger.warning(f"Private Reply DM failed for rule '{auto.name}' on comment {comment_meta_id}")
                    except Exception as dm_err:
                        logger.error(f"DM exception for rule '{auto.name}': {dm_err}")

                # 3. Update stats
                auto.trigger_count += 1
                auto.last_triggered_at = timezone.now()
                auto.save(update_fields=['trigger_count', 'last_triggered_at'])

                logger.info(
                    f"CommentAutomation fired | rule={auto.name} | "
                    f"comment={comment_meta_id} | match={auto.match_type}"
                )
            except Exception as e:
                logger.error(f"CommentAutomation '{auto.name}' failed to fire: {e}")


@login_required
def crm_comment_automations(request):
    """List / manage all comment automation rules."""
    integrations = CRMIntegration.objects.filter(status='connected')
    automations = CommentAutomation.objects.select_related('integration').all()

    # Stats
    active_count = automations.filter(is_active=True).count()
    total_fired = sum(a.trigger_count for a in automations)

    context = {
        'crm_section': 'social_posts',
        'automations': automations,
        'integrations': integrations,
        'active_count': active_count,
        'total_fired': total_fired,
    }
    return render(request, 'trendycrm/comment_automations.html', context)


@login_required
@require_POST
def crm_comment_automation_save(request):
    """Create or update a CommentAutomation rule via AJAX POST."""
    pk = request.POST.get('pk') or None  # treat empty string as None
    integration_id = request.POST.get('integration_id', '').strip()
    name = request.POST.get('name', '').strip()
    trigger_keyword = request.POST.get('trigger_keyword', '').strip()
    # match_type: valid values are 'exact', 'contains', 'any'
    match_type = request.POST.get('match_type', 'contains')
    if match_type not in ('exact', 'contains', 'any'):
        match_type = 'contains'
    public_reply = request.POST.get('public_reply', '').strip()
    # Checkboxes are only sent when checked; default to False
    send_dm = request.POST.get('send_dm') == 'on'
    dm_message = request.POST.get('dm_message', '').strip()
    # BUG FIX: default must be '' not 'on', else is_active always True when unchecked
    is_active = request.POST.get('is_active', '') == 'on'

    if not name or not public_reply or not integration_id:
        return JsonResponse({'status': 'error', 'message': 'Name, public reply, and page are required.'}, status=400)

    # keyword required for non-'any' rules
    if match_type != 'any' and not trigger_keyword:
        return JsonResponse({'status': 'error', 'message': 'A trigger keyword is required for this match type.'}, status=400)

    integration = get_object_or_404(CRMIntegration, pk=integration_id)

    if pk:
        auto = get_object_or_404(CommentAutomation, pk=pk)
    else:
        auto = CommentAutomation(integration=integration)

    auto.integration = integration
    auto.name = name
    auto.trigger_keyword = trigger_keyword
    auto.match_type = match_type
    auto.public_reply = public_reply
    auto.send_dm = send_dm
    auto.dm_message = dm_message
    auto.is_active = is_active
    auto.save()

    return JsonResponse({
        'status': 'ok',
        'pk': auto.pk,
        'name': auto.name,
        'is_active': auto.is_active,
        'message': 'Automation saved successfully!'
    })


@login_required
@require_POST
def crm_comment_automation_delete(request, pk):
    """Delete a CommentAutomation rule."""
    auto = get_object_or_404(CommentAutomation, pk=pk)
    auto.delete()
    return JsonResponse({'status': 'ok', 'message': 'Automation deleted.'})


@login_required
@require_POST
def crm_comment_automation_toggle(request, pk):
    """Toggle active/paused state of a CommentAutomation rule."""
    auto = get_object_or_404(CommentAutomation, pk=pk)
    auto.is_active = not auto.is_active
    auto.save(update_fields=['is_active'])
    return JsonResponse({
        'status': 'ok',
        'is_active': auto.is_active,
        'message': f"Automation {'activated' if auto.is_active else 'paused'}."
    })


@login_required
def crm_comment_automation_get(request, pk):
    """Return automation data as JSON for editing."""
    auto = get_object_or_404(CommentAutomation, pk=pk)
    return JsonResponse({
        'pk': auto.pk,
        'integration_id': auto.integration_id,
        'name': auto.name,
        'trigger_keyword': auto.trigger_keyword,
        'match_type': auto.match_type,
        'public_reply': auto.public_reply,
        'send_dm': auto.send_dm,
        'dm_message': auto.dm_message,
        'is_active': auto.is_active,
    })


@login_required
@require_POST
def crm_fire_automation_on_comment(request, comment_id):
    comment = get_object_or_404(CRMSocialComment, pk=comment_id)
    try:
        _check_comment_automations(comment.post.integration, comment)
        return JsonResponse({'status': 'ok', 'message': f'Automations fired for comment.'})
    except Exception as e:
        logger.error(f'crm_fire_automation_on_comment failed: {e}')
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

def _backfill_facebook_user_names(integration):
    if not integration or not integration.access_token:
        return
    comments = CRMSocialComment.objects.filter(
        post__integration=integration,
        sender_name='Facebook User'
    ).exclude(sender_id__isnull=True).exclude(sender_id='')[:20]
    
    if not comments:
        return
        
    from trendycrm.meta_sync import GRAPH_API_VERSION
    for comment in comments:
        try:
            url = f'https://graph.facebook.com/{GRAPH_API_VERSION}/{comment.sender_id}?fields=name&access_token={integration.access_token}'
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                name = resp.json().get('name')
                if name:
                    comment.sender_name = name
                    comment.save(update_fields=['sender_name'])
        except Exception as e:
            logger.error(f'Failed backfill name for {comment.sender_id}: {e}')
