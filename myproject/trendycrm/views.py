from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import F
from dashboard.timezone_utils import format_nepali_datetime
import json
import requests
import logging
import base64
import hashlib
import os
import secrets
import threading
from django.db import connection

logger = logging.getLogger(__name__)

# Keep in sync with trendycrm/meta_sync.py's GRAPH_API_VERSION.
GRAPH_API_VERSION = 'v25.0'

def run_async(func, *args, **kwargs):
    """Runs a function in a background thread to prevent blocking page loads."""
    def wrapper():
        try:
            func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Error in async task {func.__name__}")
        finally:
            connection.close()
    t = threading.Thread(target=wrapper)
    t.daemon = True
    t.start()


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
    sync_meta_posts, sync_meta_comments, reply_to_meta_comment, hide_meta_comment, delete_meta_comment,
    _resolve_integration_by_page, process_whatsapp_message_webhook,
    LIVE_INTEGRATION_STATUSES,
)
from .models import (
    CRMContact, CRMConversation, CRMMessage,
    CRMQuickReply, CRMChatbotConfig, CRMIntegration, CRMTicket,
    CRMSocialPost, CRMSocialComment, CRMPageProfile, CRMCreditLog,
    CRMLabel, CRMNote, CommentAutomation
)


def _employee_display_name(user):
    """Best-effort human name for a CustomUser, preferring their linked HRM Employee record."""
    if not user:
        return None
    try:
        return user.employee_profile.full_name
    except Exception:
        return user.get_full_name() or user.username


# ─── Home ────────────────────────────────────────────────────────────────────
@login_required
def crm_home(request):
    integrations = CRMIntegration.objects.all()

    total_contacts = CRMContact.objects.count()
    open_tickets = CRMTicket.objects.filter(status='open').count()
    total_conversations = CRMConversation.objects.count()
    connected_channels = integrations.filter(status='connected').count()

    any_chatbot_active = CRMChatbotConfig.objects.filter(is_active=True).exists()
    
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
            'action_label': 'Open Chatbots',
            'action_url': 'trendycrm:chatbot_list',
            'done': CRMChatbotConfig.objects.filter(business_name__isnull=False).exclude(business_name='').exists(),
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
            'action_url': 'trendycrm:chatbot_list',
            'done': any_chatbot_active,
        },
    ]
    done_count = sum(1 for s in setup_steps if s['done'])

    context = {
        'setup_steps': setup_steps,
        'done_count': done_count,
        'total_steps': len(setup_steps),
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
    # Try to auto-sync connected meta channels when opening the inbox. Pages
    # flagged 'error' are retried too — a successful sync is what clears the
    # flag once the operator re-grants the page.
    try:
        active_integrations = CRMIntegration.objects.filter(
            channel_type__in=['facebook', 'instagram'], status__in=LIVE_INTEGRATION_STATUSES
        )
        for integ in active_integrations:
            sync_meta_conversations(integ)
    except Exception as e:
        logger.exception("Failed auto-sync on inbox load")

    conversations = CRMConversation.objects.select_related('contact', 'assigned_to', 'integration').all().order_by('-updated_at')
    
    page_filter = request.GET.get('page_filter')
    if page_filter:
        conversations = conversations.filter(integration_id=page_filter)
        
    read_status = request.GET.get('read_status')
    if read_status == 'unread':
        conversations = conversations.filter(is_read=False)
    elif read_status == 'read':
        conversations = conversations.filter(is_read=True)
        
    search_query = request.GET.get('q', '').strip()
    search_type = request.GET.get('search_type', 'chats')
    
    if search_query:
        from django.db.models import Q
        if search_type == 'messages':
            conversations = conversations.filter(
                Q(messages__body__icontains=search_query)
            ).distinct()
        else:
            conversations = conversations.filter(
                Q(contact__name__icontains=search_query) | 
                Q(contact__phone__icontains=search_query) |
                Q(contact__email__icontains=search_query)
            ).distinct()

    label_filter = request.GET.get('label_filter')
    if label_filter:
        conversations = conversations.filter(labels__id=label_filter)
        
    connected_integrations = CRMIntegration.objects.filter(status='connected')
    all_labels = CRMLabel.objects.all().order_by('name')

    active_conv = None
    conv_id = request.GET.get('id')
    messages = []
    
    if conv_id:
        active_conv = CRMConversation.objects.filter(pk=conv_id).first()
        if not active_conv:
            from django.shortcuts import redirect
            return redirect('trendycrm:conversations')

    # Auto-redirect to the first relevant chat when a filter is applied
    if not active_conv and (label_filter or page_filter or read_status or search_query) and conversations.exists():
        active_conv = conversations.first()
        
    if active_conv:
        if not active_conv.is_read:
            active_conv.is_read = True
            active_conv.save(update_fields=['is_read'])
        messages = active_conv.messages.all()

    contacts = CRMContact.objects.all()

    from hrm.models import Employee
    employees = Employee.objects.filter(
        employee_status='active', user__isnull=False
    ).select_related('user', 'designation').order_by('full_name')

    context = {
        'conversations': conversations,
        'active_conv': active_conv,
        'chat_messages': messages,
        'contacts': contacts,
        'connected_integrations': connected_integrations,
        'all_labels': all_labels,
        'employees': employees,
        'assigned_employee_name': _employee_display_name(active_conv.assigned_to) if active_conv else None,
        'page_filter': int(page_filter) if page_filter and page_filter.isdigit() else None,
        'read_status': read_status,
        'search_query': search_query,
        'search_type': search_type,
        'label_filter': int(label_filter) if label_filter and label_filter.isdigit() else None,
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
                run_async(sync_meta_conversations, active_conv.integration)
                request.session[f'last_sync_{active_conv.integration.pk}'] = time.time()
            except Exception as e:
                logger.error(f"Ajax auto-sync failed: {e}")

    # Re-fetch the conversation because sync_meta_conversations might have updated it
    active_conv.refresh_from_db()
    
    if not active_conv.is_read:
        active_conv.is_read = True
        active_conv.save(update_fields=['is_read'])

    messages = active_conv.messages.all()
    
    context = {
        'chat_messages': messages,
    }
    return render(request, 'trendycrm/partials/messages_list.html', context)


ATTACHMENT_PREVIEW_TEXT = {
    'image': '📷 Photo',
    'audio': '🎤 Voice message',
    'document': '📎 Document',
}


def _delivery_failure_message(integration, send_error):
    """
    Build the toast shown when an outbound message doesn't reach the provider.
    Names the account it was sent from and quotes the provider's own reason —
    a bare "check your permissions" gives the agent nothing to act on, and the
    most common cause (a page that was left unticked during Facebook Login, so
    its token is dead) is only diagnosable from the Graph error text.
    """
    account = integration.parsed_name or integration.account_name if integration else None
    prefix = f"Couldn't deliver to {account}" if account else "Couldn't deliver the message"
    reason = f" — {send_error}" if send_error else "."
    if integration and integration.status == 'error':
        return (
            f"{prefix}{reason} This page needs to be reconnected: open Integrations → "
            f"Connect, and make sure this page is ticked in Facebook's page picker."
        )
    return f"{prefix}{reason}"


@login_required
@require_POST
def crm_send_message(request, conv_id):
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    body = request.POST.get('body', '').strip()

    attachment_file = None
    attachment_type = ''
    for field_name, a_type in (('image_file', 'image'), ('audio_file', 'audio'), ('doc_file', 'document')):
        f = request.FILES.get(field_name)
        if f:
            attachment_file = f
            attachment_type = a_type
            break

    if body or attachment_file:
        msg = CRMMessage.objects.create(
            conversation=conv,
            sender=request.user.get_full_name() or request.user.username,
            body=body,
            is_outbound=True,
            attachment=attachment_file,
            attachment_type=attachment_type,
            attachment_name=attachment_file.name if attachment_file else '',
        )
        conv.last_message = body or ATTACHMENT_PREVIEW_TEXT.get(attachment_type, '📎 Attachment')
        conv.updated_at = msg.created_at
        conv.is_read = True
        conv.save(update_fields=['last_message', 'updated_at', 'is_read'])

        # Send to the channel's messaging API if applicable
        if conv.channel in ['facebook', 'instagram', 'whatsapp'] and conv.contact and conv.contact.meta_id:
            from .models import CRMIntegration
            from .meta_sync import send_meta_message, send_whatsapp_message
            send_fn = send_whatsapp_message if conv.channel == 'whatsapp' else send_meta_message
            send_kwargs = {}
            if msg.attachment:
                # Hand over the file itself, not a URL — Meta fetches `payload.url`
                # from their own servers, so a local /media/ link is unreachable.
                send_kwargs = {
                    'attachment_file': msg.attachment,
                    'attachment_type': attachment_type,
                }
            success, send_error, used_integration = False, None, None
            if conv.integration:
                used_integration = conv.integration
                success, send_error = send_fn(conv.integration, conv.contact.meta_id, body, **send_kwargs)
            else:
                integrations = CRMIntegration.objects.filter(channel_type=conv.channel, status='connected')
                for integration in integrations:
                    used_integration = integration
                    success, send_error = send_fn(integration, conv.contact.meta_id, body, **send_kwargs)
                    if success:
                        break
                if not integrations:
                    send_error = f"No connected {conv.get_channel_display()} account to send from."

            if not success:
                msg.status = 'failed'
                msg.save(update_fields=['status'])
                messages.error(request, _delivery_failure_message(used_integration, send_error))

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
            conv.is_read = True
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
            conv.is_read = True
            conv.save(update_fields=['updated_at', 'is_read'])

            # Actually deliver it through the channel's messaging API — mirrors
            # crm_send_message. Only possible when the contact already has a
            # stored PSID/wa_id (i.e. they've messaged in before); a brand-new
            # manually-typed contact has no external identifier to send to yet.
            if channel in ['facebook', 'instagram', 'whatsapp'] and contact.meta_id:
                from .meta_sync import send_meta_message, send_whatsapp_message
                send_fn = send_whatsapp_message if channel == 'whatsapp' else send_meta_message
                send_integration = integration
                if not send_integration:
                    send_integration = CRMIntegration.objects.filter(channel_type=channel, status='connected').first()

                if send_integration:
                    sent, send_error = send_fn(send_integration, contact.meta_id, first_message)
                else:
                    sent, send_error = False, f"No connected {channel} account to send from."

                if not sent:
                    msg.status = 'failed'
                    msg.save(update_fields=['status'])
                    messages.error(request, _delivery_failure_message(send_integration, send_error))

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
def crm_create_label_global(request):
    label_name = request.POST.get('name', '').strip()
    color_hex = request.POST.get('color_hex', '#7c3aed').strip()
    
    if not label_name:
        messages.error(request, 'Label name required')
        return redirect('trendycrm:conversations')
        
    label, created = CRMLabel.objects.get_or_create(
        name=label_name,
        defaults={'color_hex': color_hex}
    )
    
    if created:
        messages.success(request, f'Label "{label.name}" created.')
    
    # We could redirect to the conversations page, possibly maintaining filters if passed in request.META['HTTP_REFERER']
    # For now, just redirect back to the conversations page.
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('trendycrm:conversations')

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


@login_required
@require_POST
def crm_assign_conversation(request, conv_id):
    """Assign a conversation to an HRM employee (their linked login account)
    — the assignable roster is sourced from hrm.Employee, not raw CustomUsers."""
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    employee_id = request.POST.get('employee_id', '').strip()

    if not employee_id:
        conv.assigned_to = None
        conv.save(update_fields=['assigned_to'])
        return JsonResponse({'status': 'ok', 'assigned': None})

    if not employee_id.isdigit():
        return JsonResponse({'status': 'error', 'message': 'Invalid employee id'}, status=400)

    from hrm.models import Employee
    employee = get_object_or_404(Employee, pk=employee_id, user__isnull=False)
    conv.assigned_to = employee.user
    conv.save(update_fields=['assigned_to'])

    return JsonResponse({
        'status': 'ok',
        'assigned': {
            'employee_id': employee.pk,
            'name': employee.full_name,
            'designation': employee.designation.name if employee.designation_id else '',
        }
    })


@login_required
@require_POST
def crm_resolve_conversation(request, conv_id):
    """Toggle a conversation between 'resolved' and 'open'."""
    conv = get_object_or_404(CRMConversation, pk=conv_id)
    conv.status = 'open' if conv.status == 'resolved' else 'resolved'
    conv.save(update_fields=['status'])

    return JsonResponse({
        'status': 'ok',
        'conv_status': conv.status,
        'conv_status_display': conv.get_status_display(),
    })


# ─── Chatbot ─────────────────────────────────────────────────────────────────
@login_required
def crm_chatbot_list(request):
    """
    Multi-business setup hub. Each CRMChatbotConfig represents one business/brand —
    its own identity, knowledge base, agent settings, and set of connected channels.
    """
    chatbots = list(
        CRMChatbotConfig.objects.all().prefetch_related('integrations').order_by('-is_active', 'name')
    )

    # Decorate each business with its assigned channels so the card can show real
    # icons (Font Awesome has no brand glyph for the mail channels) and an
    # unambiguous count — "assigned to this business", not "connected overall".
    for bot in chatbots:
        assigned = list(bot.integrations.all())
        bot.assigned_channels = [
            {
                'meta': channel_meta(i.channel_type),
                'display_name': i.get_channel_type_display(),
                'account_name': i.parsed_name or i.account_name,
                'is_connected': i.status == 'connected',
            }
            for i in assigned
        ]
        bot.assigned_count = len(assigned)

    total_businesses = len(chatbots)
    active_count = sum(1 for b in chatbots if b.is_active)
    connected_channels_count = CRMIntegration.objects.filter(status='connected').count()
    unassigned_connected_count = CRMIntegration.objects.filter(
        status='connected', chatbot_config__isnull=True
    ).count()

    context = {
        'chatbots': chatbots,
        'unassigned_connected_count': unassigned_connected_count,
        'total_businesses': total_businesses,
        'active_count': active_count,
        'connected_channels_count': connected_channels_count,
        'crm_section': 'chatbot',
    }
    return render(request, 'trendycrm/chatbot_list.html', context)


@login_required
@require_POST
def crm_chatbot_create(request):
    """Create a new business AI configuration and send the operator straight to setup."""
    # Count-based naming collides after a delete (delete #1 of 2, create again →
    # a second "New Business 2"), so walk up until the name is actually free.
    taken = set(CRMChatbotConfig.objects.values_list('business_name', flat=True))
    default_name = "New Business"
    n = 1
    while default_name in taken:
        n += 1
        default_name = f"New Business {n}"
    bot = CRMChatbotConfig.objects.create(name=f"{default_name} AI", business_name=default_name)
    messages.success(request, f'"{default_name}" created. Fill in its knowledge base to bring it online.')
    return redirect('trendycrm:chatbot', bot_id=bot.id)


@login_required
@require_POST
def crm_chatbot_delete(request, bot_id):
    """AJAX: Delete a business's chatbot configuration. Its channels simply become unassigned."""
    chatbot = get_object_or_404(CRMChatbotConfig, pk=bot_id)
    name = chatbot.business_name or chatbot.name
    chatbot.delete()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok'})
    messages.success(request, f'"{name}" deleted.')
    return redirect('trendycrm:chatbot_list')


@login_required
def crm_chatbot(request, bot_id):
    chatbot = get_object_or_404(CRMChatbotConfig, pk=bot_id)
    integrations = CRMIntegration.objects.all().select_related('chatbot_config')
    recent_logs = chatbot.credit_logs.all()[:20]
    other_businesses = CRMChatbotConfig.objects.exclude(pk=bot_id).order_by('name')

    channel_groups = []
    for ct, ct_display in CRMIntegration.CHANNEL_TYPE_CHOICES:
        integs = [i for i in integrations if i.channel_type == ct]
        if not integs:
            continue

        is_connected = any(i.status == 'connected' for i in integs)

        channel_groups.append({
            'channel_type': ct,
            'display_name': ct_display,
            'is_connected': is_connected,
            'meta': channel_meta(ct),
            # Accounts this business is already auto-replying on — used to
            # auto-expand the group so an enabled toggle isn't hidden.
            'enabled_here': any(i.chatbot_config_id == chatbot.pk for i in integs),
            'accounts': integs,
        })

    # Which AI providers actually have a key configured in .env — drives the
    # availability badges and safe-error messaging in the Model Routing UI.
    from .ai_router import _get_available_providers
    providers_available = _get_available_providers()

    context = {
        'chatbot': chatbot,
        'channel_groups': channel_groups,
        'recent_logs': recent_logs,
        'crm_section': 'chatbot',
        'other_businesses': other_businesses,
        'openai_available': providers_available['openai'],
        'gemini_available': providers_available['gemini'],
        # Seeds the hidden field behind the Cities Served tag input, so the list
        # survives a submit even if the tag JS never runs.
        'cities_served_json': json.dumps(chatbot.cities_served or []),
    }
    return render(request, 'trendycrm/chatbot.html', context)


@login_required
@require_POST
def crm_chatbot_toggle(request, bot_id):
    """
    AJAX: Set the global AI on/off switch.

    The caller sends the state it wants ('enabled': 'true'/'false') so a stale page
    or a second browser tab can't flip the AI the opposite way from what the
    operator just confirmed. Falls back to a plain flip if no state is sent.
    """
    chatbot = get_object_or_404(CRMChatbotConfig, pk=bot_id)
    desired = request.POST.get('enabled')
    if desired in ('true', 'false'):
        chatbot.is_active = (desired == 'true')
    else:
        chatbot.is_active = not chatbot.is_active
    chatbot.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'status': 'ok', 'is_active': chatbot.is_active})


def _parse_city_list(raw):
    """
    Normalise the 'cities served' field into a de-duplicated list of names.
    Accepts the JSON array the tag input posts, or a plain comma-separated
    string, so the field still saves if JS is unavailable.
    """
    raw = (raw or '').strip()
    if not raw:
        return []

    values = None
    if raw.startswith('['):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                values = parsed
        except (ValueError, TypeError):
            values = None
    if values is None:
        values = raw.split(',')

    cities, seen = [], set()
    for city in values:
        city = str(city).strip()
        if city and city.lower() not in seen:
            seen.add(city.lower())
            cities.append(city)
    return cities


@login_required
@require_POST
def crm_chatbot_save_knowledge(request, bot_id):
    """
    AJAX: Save the 5-section Business Knowledge Base.
    Saves: about_blurb (identity), tone_voice, offerings, faq_text, playbook,
           business_name, business_email, business_phone, welcome_message,
           business_address, cities_served, and the social handles.
    """
    chatbot = get_object_or_404(CRMChatbotConfig, pk=bot_id)

    chatbot.business_name = request.POST.get('business_name', '').strip() or chatbot.business_name

    # Email/phone are optional, so an empty submission means "clear it" — the old
    # `or <existing>` fallback made them impossible to remove once set.
    email = request.POST.get('business_email', '').strip()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse(
                    {'status': 'error', 'message': f'"{email}" is not a valid email address.'},
                    status=400,
                )
            messages.error(request, f'"{email}" is not a valid email address.')
            return redirect('trendycrm:chatbot', bot_id=bot_id)
    chatbot.business_email = email or None
    chatbot.business_phone = request.POST.get('business_phone', '').strip() or None

    chatbot.about_blurb = request.POST.get('about_blurb', '').strip() or None
    chatbot.welcome_message = request.POST.get('welcome_message', '').strip() or None
    chatbot.business_address = request.POST.get('business_address', '').strip() or None

    # Cities are posted as a JSON array from the tag input. Fall back to a
    # comma-separated string so a non-JS submit (or an API caller) still works.
    chatbot.cities_served = _parse_city_list(request.POST.get('cities_served', ''))

    for field in ('social_facebook', 'social_instagram', 'social_tiktok', 'social_whatsapp'):
        setattr(chatbot, field, request.POST.get(field, '').strip() or None)

    chatbot.tone_voice = request.POST.get('tone_voice', '').strip() or None
    chatbot.offerings = request.POST.get('offerings', '').strip() or None
    chatbot.faq_text = request.POST.get('faq_text', '').strip() or None
    chatbot.playbook = request.POST.get('playbook', '').strip() or None
    chatbot.save()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok', 'message': 'Business knowledge saved successfully!'})
    messages.success(request, 'Business knowledge saved!')
    return redirect('trendycrm:chatbot', bot_id=bot_id)


@login_required
@require_POST
def crm_chatbot_save_agent(request, bot_id):
    """
    AJAX: Save agent configuration (AI model, tone, API keys).
    """
    chatbot = get_object_or_404(CRMChatbotConfig, pk=bot_id)

    chatbot.ai_model = request.POST.get('ai_model', chatbot.ai_model)
    chatbot.response_tone = request.POST.get('response_tone', chatbot.response_tone)
    chatbot.creativity_level = request.POST.get('creativity_level', chatbot.creativity_level)
    chatbot.response_length = request.POST.get('response_length', chatbot.response_length)

    # Purpose-based provider routing
    valid_providers = {'auto', 'openai', 'gemini'}
    text_provider = request.POST.get('text_provider')
    if text_provider in valid_providers:
        chatbot.text_provider = text_provider
    image_provider = request.POST.get('image_provider')
    if image_provider in valid_providers:
        chatbot.image_provider = image_provider
    openai_model = request.POST.get('openai_model')
    if openai_model is not None and openai_model.strip():
        chatbot.openai_model = openai_model.strip()
    gemini_model = request.POST.get('gemini_model')
    if gemini_model is not None and gemini_model.strip():
        chatbot.gemini_model = gemini_model.strip()

    import json
    
    chatbot.primary_language = request.POST.get('primary_language', chatbot.primary_language)
    
    triggers_json = request.POST.get('handoff_triggers')
    if triggers_json:
        try:
            chatbot.handoff_triggers = json.loads(triggers_json)
        except json.JSONDecodeError:
            pass
    chatbot.save()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok', 'message': 'Agent configuration saved!'})
    messages.success(request, 'Agent configuration saved!')
    return redirect('trendycrm:chatbot', bot_id=bot_id)


@login_required
@require_POST
def crm_chatbot_toggle_channel(request, bot_id):
    """
    AJAX: Toggle auto-reply for a specific channel on/off.
    POST body: { channel_type: 'facebook', enabled: 'true'/'false' }
    """
    chatbot = get_object_or_404(CRMChatbotConfig, pk=bot_id)
    integration_id = request.POST.get('integration_id')
    enabled = request.POST.get('enabled', 'false') == 'true'

    if integration_id:
        integration = get_object_or_404(CRMIntegration, pk=integration_id)
        channels = chatbot.auto_reply_channels or {}
        
        if enabled:
            # If the integration was connected to another chatbot, remove it from that bot's config
            if integration.chatbot_config and integration.chatbot_config_id != chatbot.id:
                prev_bot = integration.chatbot_config
                prev_channels = prev_bot.auto_reply_channels or {}
                if str(integration_id) in prev_channels:
                    prev_channels.pop(str(integration_id), None)
                    prev_bot.auto_reply_channels = prev_channels
                    prev_bot.save(update_fields=['auto_reply_channels'])
                    
            channels[str(integration_id)] = True
            integration.chatbot_config = chatbot
        else:
            channels.pop(str(integration_id), None)
            if integration.chatbot_config_id == chatbot.id:
                integration.chatbot_config = None
        
        integration.save(update_fields=['chatbot_config'])
        chatbot.auto_reply_channels = channels
        chatbot.save(update_fields=['auto_reply_channels', 'updated_at'])
        return JsonResponse({'status': 'ok', 'integration_id': integration_id, 'enabled': enabled})

    return JsonResponse({'status': 'error', 'message': 'integration_id required'}, status=400)


def charge_ai_credits(chatbot, action, model_used='', description='', credits=1):
    """
    Record one AI call against a business's credit balance.

    `credits_used` follows the CRMCreditLog convention: negative = usage,
    positive = top-up. The balance is decremented with an F() expression so
    concurrent webhook replies can't clobber each other's writes.

    Never raises — an accounting failure must not break the AI reply itself.
    """
    if not chatbot or not credits:
        return
    try:
        CRMChatbotConfig.objects.filter(pk=chatbot.pk).update(ai_credits=F('ai_credits') - credits)
        CRMCreditLog.objects.create(
            chatbot_config=chatbot,
            action=action,
            credits_used=-credits,
            model_used=(model_used or '')[:50],
            description=(description or '')[:300],
        )
    except Exception:
        logger.exception("Failed to record AI credit usage for chatbot %s", getattr(chatbot, 'pk', None))


@login_required
def crm_credit_history(request, bot_id):
    """AJAX: Returns JSON list of recent credit log entries."""
    chatbot = get_object_or_404(CRMChatbotConfig, pk=bot_id)
    logs = chatbot.credit_logs.all()[:50]
    data = [
        {
            'id': l.pk,
            'action': l.get_action_display(),
            'credits_used': l.credits_used,
            'model_used': l.model_used or '—',
            'description': l.description or '',
            'created_at': format_nepali_datetime(l.created_at, '%b %d, %Y %H:%M'),
            'is_topup': l.credits_used > 0,
        }
        for l in logs
    ]
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
# 'prefix' is the Font Awesome style class the icon actually lives in — the mail
# channels use solid (fas) glyphs because Font Awesome has no Gmail/Outlook/Zoho
# brand icons. Rendering these with a hardcoded `fab` produces a blank square.
INTEGRATION_META = {
    'whatsapp': {'label': 'WhatsApp', 'icon': 'fa-whatsapp', 'prefix': 'fab', 'color': '#25D366', 'tint': '#d1fae5', 'group': 'MESSAGING CHANNELS'},
    'instagram': {'label': 'Instagram', 'icon': 'fa-instagram', 'prefix': 'fab', 'color': '#E1306C', 'tint': '#fce7f3', 'group': 'MESSAGING CHANNELS'},
    'facebook': {'label': 'Facebook Messenger', 'icon': 'fa-facebook-messenger', 'prefix': 'fab', 'color': '#0084FF', 'tint': '#dbeafe', 'group': 'MESSAGING CHANNELS'},
    'tiktok': {'label': 'TikTok', 'icon': 'fa-tiktok', 'prefix': 'fab', 'color': '#010101', 'tint': '#f3f4f6', 'group': 'MESSAGING CHANNELS'},
    'gmail': {'label': 'Gmail', 'icon': 'fa-envelope', 'prefix': 'fas', 'color': '#EA4335', 'tint': '#fee2e2', 'group': 'MESSAGING CHANNELS'},
    'outlook': {'label': 'Outlook', 'icon': 'fa-envelope-open', 'prefix': 'fas', 'color': '#0078D4', 'tint': '#dbeafe', 'group': 'MESSAGING CHANNELS'},
    'zoho_mail': {'label': 'Zoho Mail', 'icon': 'fa-mail-bulk', 'prefix': 'fas', 'color': '#E42527', 'tint': '#fee2e2', 'group': 'MESSAGING CHANNELS'},
}

# Fallback so an unknown/legacy channel_type still renders a visible icon.
FALLBACK_CHANNEL_META = {'label': 'Channel', 'icon': 'fa-plug', 'prefix': 'fas', 'color': '#6b7280', 'tint': '#f3f4f6'}


def channel_meta(channel_type):
    """Icon/colour metadata for a channel type, never None."""
    return INTEGRATION_META.get(channel_type, FALLBACK_CHANNEL_META)


@login_required
def crm_integrations(request):
    # Ensure at least one integration record exists per channel for UI.
    # get_or_create avoids a race where two concurrent requests both see
    # "missing" and each insert a duplicate not_connected row for the channel.
    existing_channels = set(CRMIntegration.objects.values_list('channel_type', flat=True))
    for key in INTEGRATION_META:
        if key not in existing_channels:
            CRMIntegration.objects.get_or_create(channel_type=key, defaults={'status': 'not_connected'})

    integrations = CRMIntegration.objects.all()
    active_key = request.GET.get('channel', 'instagram')
    active_integrations = integrations.filter(channel_type=active_key)
    # 'error' accounts are listed too — a page whose token Graph has rejected is
    # still linked here, and hiding it would leave no way to reconnect or remove it.
    connected_integrations = active_integrations.filter(status__in=['connected', 'error'])
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
    elif active_key == 'tiktok':
        active_perms = [
            ('Public profile', 'Access your username and profile picture (user.info.basic).'),
            ('Video list', 'Read your published videos (video.list).'),
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
    elif active_key == 'whatsapp':
        fb_oauth_url = reverse('trendycrm:whatsapp_connect')
    else:
        fb_oauth_url = ''

    # Facebook/Instagram/WhatsApp all share the same Meta webhook receiver
    # (trendycrm:meta_webhook) — showing a fake /api/webhooks/<channel>/ URL for
    # channels with no receiver just leads to 404s in the provider's console.
    webhook_url = None
    if active_key in ('facebook', 'instagram', 'whatsapp'):
        webhook_url = request.build_absolute_uri(reverse('trendycrm:meta_webhook'))

    connected_channel_count = sum(1 for ch in channels_with_meta if ch['is_connected'])

    context = {
        'channels_with_meta': channels_with_meta,
        'active_integrations': active_integrations,
        'connected_integrations': connected_integrations,
        'active_meta': active_meta,
        'active_key': active_key,
        'active_perms': active_perms,
        'fb_oauth_url': fb_oauth_url,
        'webhook_url': webhook_url,
        'connected_channel_count': connected_channel_count,
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


def _connect_meta_channel(request, callback_route_name, scope, state_session_key):
    """
    Shared Meta OAuth-dialog redirect for Facebook, Instagram and WhatsApp — all
    three connect through the same Facebook Business app/OAuth dialog (Instagram
    professional accounts and WhatsApp Business numbers are both linked via Meta
    Business Manager), only the callback route and requested scope differ.

    A random `state` value is minted and stashed in the session so the callback
    can verify the response actually belongs to this login's own request (basic
    OAuth CSRF protection) rather than blindly trusting whatever comes back.
    """
    redirect_uri = build_absolute_callback(request, callback_route_name)
    state = secrets.token_urlsafe(24)
    request.session[state_session_key] = state
    params = {
        "client_id": getattr(settings, 'FACEBOOK_APP_ID', '873948152450056'),
        "redirect_uri": redirect_uri,
        "scope": scope,
        "response_type": "code",
        "auth_type": "rerequest",
        "state": state,
    }
    from urllib.parse import urlencode
    auth_url = "https://www.facebook.com/v25.0/dialog/oauth?" + urlencode(params)
    return redirect(auth_url)

@login_required
def connect_facebook(request):
    return _connect_meta_channel(
        request, 'trendycrm:facebook_callback',
        scope="pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement,pages_read_user_content,pages_manage_engagement,instagram_basic,instagram_manage_messages",
        state_session_key='facebook_oauth_state',
    )

@login_required
def connect_instagram(request):
    return _connect_meta_channel(
        request, 'trendycrm:instagram_callback',
        scope="pages_show_list,pages_manage_metadata,pages_messaging,pages_read_engagement,pages_read_user_content,pages_manage_engagement,instagram_basic,instagram_manage_messages",
        state_session_key='instagram_oauth_state',
    )

@login_required
def connect_whatsapp(request):
    return _connect_meta_channel(
        request, 'trendycrm:whatsapp_callback',
        scope="whatsapp_business_management,whatsapp_business_messaging,business_management",
        state_session_key='whatsapp_oauth_state',
    )

@login_required
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

    # Store verifier + a random CSRF state in session for token exchange / verification
    request.session['tiktok_code_verifier'] = code_verifier
    state = secrets.token_urlsafe(24)
    request.session['tiktok_oauth_state'] = state

    # Redirect to TikTok standard Login Kit authorization page
    auth_url = f"https://www.tiktok.com/v2/auth/authorize/?client_key={tiktok_client_id}&response_type=code&scope=user.info.basic,video.list&redirect_uri={redirect_uri}&state={state}&code_challenge={code_challenge}&code_challenge_method=S256"
    return redirect(auth_url)

def _exchange_fb_code_for_token(request, code, channel_key):
    """Exchange an OAuth `code` for a Meta user access token. Returns the token string or None."""
    fb_client_id = getattr(settings, 'FACEBOOK_APP_ID', getattr(settings, 'FACEBOOK_CLIENT_ID', '873948152450056'))
    fb_app_secret = getattr(settings, 'FACEBOOK_APP_SECRET', '')
    route_name = f'trendycrm:{channel_key}_callback'
    fb_redirect_uri = build_absolute_callback(request, route_name)
    token_exchange_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/oauth/access_token?client_id={fb_client_id}&redirect_uri={fb_redirect_uri}&client_secret={fb_app_secret}&code={code}"
    try:
        resp = requests.get(token_exchange_url, timeout=10)
    except Exception:
        logger.exception(f"Error exchanging OAuth code for {channel_key}")
        return None
    if resp.status_code == 200:
        return resp.json().get('access_token')
    logger.error(f"Failed to exchange token for {channel_key}: {resp.text}")
    return None

def _handle_oauth_callback(request, channel_key, state_session_key):
    # If user denied access, Facebook redirects with error=access_denied
    if 'error' in request.GET:
        logger.warning(f"OAuth error for {channel_key}: {request.GET.get('error_description', request.GET.get('error'))}")
    elif 'code' in request.GET:
        expected_state = request.session.pop(state_session_key, None)
        if not expected_state or request.GET.get('state') != expected_state:
            logger.warning(f"OAuth callback for {channel_key}: state mismatch or missing session state.")
            messages.error(request, "Connection could not be verified (session expired) — please try connecting again.")
            return redirect(reverse('trendycrm:integrations') + f"?channel={channel_key}")

        code = request.GET['code']
        user_access_token = _exchange_fb_code_for_token(request, code, channel_key)

        try:
            if user_access_token:
                # 2. Fetch pages the user has access to
                accounts_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/accounts?access_token={user_access_token}"
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
                            # Clear any "reconnect required" flag from a previously
                            # rejected token — this grant supersedes it.
                            if isinstance(integ.meta, dict):
                                integ.meta.pop('token_error', None)
                                integ.meta.pop('token_error_at', None)
                            integ.save()
                            logger.info(f"Successfully connected {channel_key} page: {page_name}")
                            connected_count += 1

                            # Trigger initial sync of conversations in background
                            try:
                                run_async(sync_meta_conversations, integ)
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
                messages.error(request, "Failed to exchange token with Meta.")
        except Exception as e:
            logger.exception("Error during Facebook OAuth token exchange")
            messages.error(request, "An unexpected error occurred during connection.")

    redirect_url = reverse('trendycrm:integrations') + f"?channel={channel_key}"
    return redirect(redirect_url)

def facebook_callback(request):
    return _handle_oauth_callback(request, 'facebook', 'facebook_oauth_state')

def instagram_callback(request):
    return _handle_oauth_callback(request, 'instagram', 'instagram_oauth_state')

def whatsapp_callback(request):
    channel_key = 'whatsapp'
    if 'error' in request.GET:
        logger.warning(f"OAuth error for whatsapp: {request.GET.get('error_description', request.GET.get('error'))}")
        messages.error(request, request.GET.get('error_description', 'WhatsApp authorization failed.'))
        return redirect(reverse('trendycrm:integrations') + "?channel=whatsapp")

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'WhatsApp authorization failed.')
        return redirect(reverse('trendycrm:integrations') + "?channel=whatsapp")

    expected_state = request.session.pop('whatsapp_oauth_state', None)
    if not expected_state or request.GET.get('state') != expected_state:
        logger.warning("WhatsApp OAuth callback: state mismatch or missing session state.")
        messages.error(request, "Connection could not be verified (session expired) — please try connecting again.")
        return redirect(reverse('trendycrm:integrations') + "?channel=whatsapp")

    user_access_token = _exchange_fb_code_for_token(request, code, channel_key)
    if not user_access_token:
        messages.error(request, "Failed to exchange token with Meta.")
        return redirect(reverse('trendycrm:integrations') + "?channel=whatsapp")

    def _graph_list(path, resource_label):
        resp = requests.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path}",
            params={'access_token': user_access_token}, timeout=10
        )
        if resp.status_code != 200:
            logger.error(f"Failed to fetch {resource_label} for WhatsApp: {resp.text}")
            return []
        return resp.json().get('data', [])

    try:
        businesses = _graph_list('me/businesses', 'businesses')

        connected_count = 0
        for biz in businesses:
            biz_id = biz.get('id')
            wabas = _graph_list(f'{biz_id}/owned_whatsapp_business_accounts', 'WhatsApp Business Accounts')

            for waba in wabas:
                waba_id = waba.get('id')
                phones = _graph_list(f'{waba_id}/phone_numbers', 'phone numbers')

                for phone in phones:
                    phone_number_id = phone.get('id', '')
                    display_number = phone.get('display_phone_number', 'WhatsApp Number')
                    verified_name = phone.get('verified_name', '')
                    account_name = f"{display_number} ({phone_number_id})" if phone_number_id else display_number

                    integ = CRMIntegration.objects.filter(channel_type='whatsapp', account_name=account_name).first()
                    if not integ:
                        integ = CRMIntegration.objects.filter(channel_type='whatsapp', status='not_connected').first()
                        if not integ:
                            integ = CRMIntegration(channel_type='whatsapp')

                    integ.status = 'connected'
                    integ.account_name = account_name
                    integ.access_token = user_access_token
                    integ.connected_at = timezone.now()
                    meta = integ.meta or {}
                    meta['waba_id'] = waba_id
                    meta['phone_number_id'] = phone_number_id
                    meta['verified_name'] = verified_name
                    integ.meta = meta
                    integ.save()
                    connected_count += 1
                    logger.info(f"Successfully connected WhatsApp number: {display_number}")

        if connected_count > 0:
            messages.success(request, f"Successfully connected {connected_count} WhatsApp number(s).")
        else:
            messages.error(
                request,
                "No WhatsApp Business phone numbers were found. Make sure a WhatsApp "
                "Business Account is set up under your Meta Business Manager and try again."
            )
    except Exception:
        logger.exception("Error during WhatsApp OAuth setup")
        messages.error(request, "An unexpected error occurred while connecting WhatsApp.")

    return redirect(reverse('trendycrm:integrations') + "?channel=whatsapp")

def tiktok_callback(request):
    code = request.GET.get('code')
    if not code:
        # User might have denied access, resulting in error being returned
        err = request.GET.get('error_description', 'TikTok authorization failed.')
        messages.error(request, err)
        return redirect(reverse('trendycrm:integrations') + "?channel=tiktok")

    expected_state = request.session.pop('tiktok_oauth_state', None)
    if not expected_state or request.GET.get('state') != expected_state:
        logger.warning("TikTok OAuth callback: state mismatch or missing session state.")
        messages.error(request, "Connection could not be verified (session expired) — please try connecting again.")
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
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        expected_token = getattr(settings, 'FACEBOOK_WEBHOOK_VERIFY_TOKEN', '')
        if mode != 'subscribe' or not token:
            return HttpResponse('Verification failed', status=403)
        if expected_token and token != expected_token:
            logger.warning("Meta webhook verification failed: verify_token mismatch")
            return HttpResponse('Verification failed', status=403)
        if not expected_token:
            logger.warning(
                "FACEBOOK_WEBHOOK_VERIFY_TOKEN is not configured — accepting webhook "
                "verification without checking the token. Set it in settings to secure this."
            )
        return HttpResponse(challenge, status=200)
        
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
            messaging_page_ids = set()

            # Process webhook events for read/delivery receipts
            if 'entry' in payload:
                for entry in payload['entry']:
                    page_id = entry.get('id')

                    # ── Page feed events (comments on posts) ──────────────────
                    # Drives real-time keyword automations + Comment-to-DM funnel.
                    from trendycrm.meta_sync import handle_feed_comment_webhook
                    for change in (entry.get('changes') or []):
                        if change.get('field') == 'feed':
                            value = change.get('value') or {}
                            if value.get('item') == 'comment':
                                # Run off-request so a slow Graph call never
                                # makes Meta retry the webhook delivery.
                                run_async(handle_feed_comment_webhook, page_id, value)
                        elif change.get('field') == 'messages':
                            # WhatsApp Cloud API inbound message/status event.
                            value = change.get('value') or {}
                            if value.get('messaging_product') == 'whatsapp' and value.get('messages'):
                                run_async(process_whatsapp_message_webhook, value)

                    if 'messaging' in entry:
                        if page_id:
                            messaging_page_ids.add(page_id)
                        for event in entry['messaging']:
                            sender_id = event.get('sender', {}).get('id')
                            if not sender_id:
                                continue
                                
                            import datetime
                            
                            # Handle read receipts
                            if 'read' in event:
                                watermark = event['read'].get('watermark')
                                if watermark:
                                    watermark_dt = datetime.datetime.fromtimestamp(watermark / 1000.0, tz=datetime.timezone.utc)
                                    CRMMessage.objects.filter(
                                        conversation__contact__meta_id=sender_id,
                                        is_outbound=True,
                                        created_at__lte=watermark_dt
                                    ).exclude(status='read').update(status='read')
                            
                            # Handle delivery receipts
                            if 'delivery' in event:
                                watermark = event['delivery'].get('watermark')
                                if watermark:
                                    watermark_dt = datetime.datetime.fromtimestamp(watermark / 1000.0, tz=datetime.timezone.utc)
                                    CRMMessage.objects.filter(
                                        conversation__contact__meta_id=sender_id,
                                        is_outbound=True,
                                        created_at__lte=watermark_dt,
                                        status='sent'
                                    ).update(status='delivered')

            # Only sync the page(s) this webhook payload actually referenced,
            # instead of every connected FB/IG integration on every event.
            if messaging_page_ids:
                for page_id in messaging_page_ids:
                    integ = _resolve_integration_by_page(page_id)
                    if integ:
                        run_async(sync_meta_conversations, integ)
                    else:
                        logger.warning(f"meta_webhook: no connected integration for messaging page {page_id}")
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
    integrations = CRMIntegration.objects.filter(
        channel_type__in=['facebook', 'instagram'], status__in=LIVE_INTEGRATION_STATUSES
    )
    for integration in integrations:
        sync_meta_posts(integration)
        _backfill_facebook_user_names(integration)
        
    post_filter = request.GET.get('post_filter', 'all')
    if post_filter == 'follow_up':
        posts = CRMSocialPost.objects.filter(is_starred=True)
    elif post_filter == 'unread':
        posts = CRMSocialPost.objects.filter(is_read=False)
    elif post_filter == 'read':
        posts = CRMSocialPost.objects.filter(is_read=True)
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
        
    from django.db.models import OuterRef, Subquery
    from django.db.models.functions import Coalesce
    
    latest_comment = CRMSocialComment.objects.filter(
        post=OuterRef('pk')
    ).exclude(
        sender_name__isnull=True
    ).exclude(
        sender_name=''
    ).order_by('-created_time')
    
    posts = posts.annotate(
        annotated_latest_time=Coalesce(Subquery(latest_comment.values('created_time')[:1]), 'created_time'),
        annotated_latest_sender=Subquery(latest_comment.values('sender_name')[:1]),
        annotated_latest_message=Subquery(latest_comment.values('message')[:1])
    ).order_by('-annotated_latest_time')
    
    selected_post_id = request.GET.get('post_id')
    active_post = None
    comments = []
    
    if selected_post_id:
        active_post = get_object_or_404(CRMSocialPost, pk=selected_post_id)
        if not active_post.is_read:
            active_post.is_read = True
            active_post.save(update_fields=['is_read'])
            
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
        # Graph API uses the same endpoint for comments on a post and replies to a comment.
        # reply_to_meta_comment returns (success, new_comment_id) — must unpack it.
        success, _new_id = reply_to_meta_comment(post.integration, post.meta_post_id, msg_text)
        if success:
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
        msg_text = (request.POST.get('message') or '').strip()
        if not msg_text:
            messages.error(request, "Reply cannot be empty.")
            return redirect(f"{reverse('trendycrm:social_posts')}?post_id={comment.post.pk}")

        integration = comment.post.integration
        # reply_to_meta_comment returns (success, new_comment_id) — unpack it.
        success, new_reply_id = reply_to_meta_comment(integration, comment.meta_comment_id, msg_text)
        if success:
            page_name = integration.parsed_name or integration.account_name or 'Page'
            # meta_comment_id is unique & required; use the real FB id when returned,
            # otherwise a stable synthetic id so repeat replies don't collide.
            stable_id = new_reply_id or f"manual_{comment.meta_comment_id}_{int(timezone.now().timestamp())}"
            CRMSocialComment.objects.get_or_create(
                meta_comment_id=stable_id,
                defaults={
                    'post': comment.post,
                    'parent_comment': comment,
                    'sender_name': page_name,
                    'sender_id': integration.parsed_id,
                    'message': msg_text,
                    'created_time': timezone.now(),
                }
            )
            messages.success(request, "Reply posted successfully.")
        else:
            messages.error(request, "Failed to post reply to Facebook.")
            
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
    # 'error' pages are included: their knowledge/profile must stay editable
    # while the operator sorts the reconnect out.
    integrations = CRMIntegration.objects.filter(status__in=LIVE_INTEGRATION_STATUSES)
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
    bot_id = request.POST.get('bot_id')
    image_file = request.FILES.get('image')

    # Validate & encode an uploaded image (image mode); text-only requests need a message.
    image_data = None
    image_mime = None
    if image_file:
        content_type = (image_file.content_type or '').lower()
        if not content_type.startswith('image/'):
            return JsonResponse({'error': 'Only image files can be uploaded.'}, status=400)
        MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
        if image_file.size > MAX_IMAGE_BYTES:
            return JsonResponse({'error': 'Image is too large (max 10 MB).'}, status=400)
        import base64
        image_data = base64.b64encode(image_file.read()).decode('ascii')
        image_mime = content_type

    if not message_text and not image_file:
        return JsonResponse({'error': 'Message or image is required'}, status=400)

    integration = None
    if integration_id:
        integration = CRMIntegration.objects.filter(pk=integration_id).first()

    if not bot_id and integration:
        chatbot = integration.chatbot_config
    elif bot_id:
        chatbot = get_object_or_404(CRMChatbotConfig, pk=bot_id)
    else:
        return JsonResponse({'error': 'Bot ID or Integration ID is required'}, status=400)

    try:
        from .ai_router import route_message

        result = route_message(
            message_text=message_text,
            integration=integration,
            chatbot_config=chatbot,
            input_type='image' if image_file else 'text',
            image_data=image_data,
            image_mime=image_mime,
        )
        if result.get('success') and not result.get('error'):
            charge_ai_credits(
                chatbot,
                action='manual_test',
                model_used=result.get('model_used', ''),
                description=(message_text or '[image only]'),
            )
            chatbot.refresh_from_db(fields=['ai_credits'])
            result['credits_remaining'] = chatbot.ai_credits
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
    from .meta_sync import reply_to_meta_comment, send_comment_dm, record_outbound_dm

    comment_text = (comment_obj.message or '').strip()
    sender_id = comment_obj.sender_id or ''
    comment_meta_id = comment_obj.meta_comment_id

    automations = CommentAutomation.objects.filter(
        integration=integration,
        is_active=True
    )

    results = []  # one entry per fired rule — lets the caller report DM status to the UI

    for auto in automations:
        matched = False
        kw = auto.trigger_keyword.strip().lower()

        if auto.match_type == 'any':
            matched = True
        elif auto.match_type == 'exact':
            matched = comment_text.lower() == kw
        elif auto.match_type == 'contains':
            matched = bool(kw) and kw in comment_text.lower()

        if not matched:
            continue

        rule_result = {
            'rule': auto.name,
            'public_reply_sent': False,
            'dm_requested': bool(auto.send_dm and auto.dm_message),
            'dm_sent': False,
            'dm_method': None,
            'dm_error': None,
        }

        try:
            page_name = integration.parsed_name or integration.account_name

            # 1. Public reply on the comment
            if auto.public_reply:
                success, new_fb_id = reply_to_meta_comment(integration, comment_meta_id, auto.public_reply)
                if success:
                    rule_result['public_reply_sent'] = True
                    stable_id = new_fb_id if new_fb_id else f"auto_{auto.pk}_{comment_meta_id}"
                    CRMSocialComment.objects.get_or_create(
                        meta_comment_id=stable_id,
                        defaults={
                            'post': comment_obj.post,
                            'parent_comment': comment_obj,
                            'sender_name': page_name,
                            'sender_id': integration.parsed_id,
                            'message': auto.public_reply,
                            'created_time': timezone.now(),
                        }
                    )
                    logger.info(f"Auto-reply posted | rule='{auto.name}' | fb_id={stable_id} | parent={comment_meta_id}")
                else:
                    logger.error(f"Auto-reply FAILED to post to Facebook | rule='{auto.name}' | comment={comment_meta_id}")

            # 2. Optional private DM (Private Replies API, with Send-API fallback)
            if rule_result['dm_requested']:
                try:
                    dm_success, dm_method, dm_error = send_comment_dm(
                        integration, comment_meta_id, auto.dm_message, sender_id=sender_id
                    )
                    rule_result['dm_sent'] = dm_success
                    rule_result['dm_method'] = dm_method
                    rule_result['dm_error'] = dm_error
                    if dm_success:
                        # Log the DM into the CRM inbox so it appears on the
                        # Conversations page as an outbound message.
                        record_outbound_dm(integration, comment_obj, auto.dm_message)
                        logger.info(
                            f"DM sent | rule='{auto.name}' | comment={comment_meta_id} | via={dm_method}"
                        )
                    else:
                        logger.warning(
                            f"DM failed | rule='{auto.name}' | comment={comment_meta_id} | error={dm_error}"
                        )
                except Exception as dm_err:
                    rule_result['dm_error'] = str(dm_err)
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

        results.append(rule_result)

    return results


@login_required
def crm_comment_automations(request):
    """List / manage all comment automation rules."""
    integrations = CRMIntegration.objects.filter(status__in=LIVE_INTEGRATION_STATUSES)
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
        results = _check_comment_automations(comment.post.integration, comment) or []

        if not results:
            return JsonResponse({
                'status': 'ok',
                'message': 'No active automation rule matches this comment.'
            })

        fired = len(results)
        dm_failures = [r for r in results if r['dm_requested'] and not r['dm_sent']]
        dm_sent = sum(1 for r in results if r['dm_sent'])

        msg = f"Fired {fired} automation rule{'s' if fired != 1 else ''}."
        if dm_sent:
            msg += f" Sent {dm_sent} private DM{'s' if dm_sent != 1 else ''}."
        if dm_failures:
            # Show the real Facebook reason so the operator can act on it
            reason = dm_failures[0].get('dm_error') or 'Unknown error'
            msg += f" ⚠️ DM not delivered: {reason}"

        return JsonResponse({
            'status': 'ok',
            'message': msg,
            'dm_failed': bool(dm_failures),
        })
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
