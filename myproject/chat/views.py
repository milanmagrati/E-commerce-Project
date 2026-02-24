import json
import time
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib.auth import get_user_model
from django.db.models import Q, Max, Count
from django.utils import timezone
from .models import ChatThread, ChatMessage

User = get_user_model()


@login_required
def chat_inbox(request):
    """Main chat inbox page listing all conversations"""
    threads = ChatThread.objects.filter(
        participants=request.user
    ).annotate(
        last_message_time=Max('messages__created_at'),
        unread_count=Count(
            'messages',
            filter=Q(messages__is_read=False) & ~Q(messages__sender=request.user)
        )
    ).order_by('-last_message_time')

    thread_list = []
    for thread in threads:
        other_user = thread.get_other_participant(request.user)
        last_msg = thread.get_last_message()
        if other_user:
            thread_list.append({
                'thread': thread,
                'other_user': other_user,
                'last_message': last_msg,
                'unread_count': thread.unread_count,
            })

    # Determine which users the current user can message
    if request.user.is_superuser or request.user.role == 'administrator':
        available_users = User.objects.filter(
            is_active=True, is_deleted=False
        ).exclude(id=request.user.id).order_by('username')
    else:
        available_users = User.objects.filter(
            is_active=True,
            is_deleted=False
        ).filter(
            Q(role='administrator') | Q(is_superuser=True)
        ).exclude(id=request.user.id).order_by('username')

    context = {
        'thread_list': thread_list,
        'available_users': available_users,
    }
    return render(request, 'chat/inbox.html', context)


@login_required
def chat_thread(request, thread_id):
    """View a specific conversation thread"""
    thread = get_object_or_404(ChatThread, id=thread_id)

    if not thread.participants.filter(id=request.user.id).exists():
        return redirect('chat:inbox')

    other_user = thread.get_other_participant(request.user)

    # Mark unread messages from the other user as read
    thread.messages.filter(
        is_read=False
    ).exclude(
        sender=request.user
    ).update(is_read=True)

    messages_list = thread.messages.select_related('sender').order_by('created_at')

    context = {
        'thread': thread,
        'other_user': other_user,
        'messages': messages_list,
    }
    return render(request, 'chat/thread.html', context)


@login_required
def start_chat(request, user_id):
    """Start a new chat with a user, or redirect to existing thread"""
    other_user = get_object_or_404(User, id=user_id, is_active=True, is_deleted=False)

    # Permission check: non-admins can only chat with admins
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        if not (other_user.is_superuser or other_user.role == 'administrator'):
            return redirect('chat:inbox')

    # Check if a thread already exists between these two users
    existing_threads = ChatThread.objects.filter(
        participants=request.user
    ).filter(
        participants=other_user
    )

    if existing_threads.exists():
        return redirect('chat:thread', thread_id=existing_threads.first().id)

    # Create new thread
    thread = ChatThread.objects.create()
    thread.participants.add(request.user, other_user)

    return redirect('chat:thread', thread_id=thread.id)


@login_required
@require_POST
def api_send_message(request):
    """AJAX endpoint to send a message"""
    try:
        data = json.loads(request.body)
        thread_id = data.get('thread_id')
        content = data.get('content', '').strip()

        if not content:
            return JsonResponse({'success': False, 'message': 'Message cannot be empty'}, status=400)

        if not thread_id:
            return JsonResponse({'success': False, 'message': 'Thread ID required'}, status=400)

        thread = get_object_or_404(ChatThread, id=thread_id)

        if not thread.participants.filter(id=request.user.id).exists():
            return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

        message = ChatMessage.objects.create(
            thread=thread,
            sender=request.user,
            content=content
        )

        # Update thread timestamp
        thread.updated_at = timezone.now()
        thread.save(update_fields=['updated_at'])

        return JsonResponse({
            'success': True,
            'message': {
                'id': message.id,
                'content': message.content,
                'sender': message.sender.username,
                'sender_id': message.sender.id,
                'sender_role': message.sender.role,
                'created_at': message.created_at.strftime('%b %d, %Y %I:%M %p'),
                'is_mine': True,
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid request'}, status=400)


@login_required
@require_http_methods(["GET"])
def api_get_messages(request, thread_id):
    """AJAX endpoint to poll for new messages"""
    thread = get_object_or_404(ChatThread, id=thread_id)

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    after_id = request.GET.get('after_id', 0)
    try:
        after_id = int(after_id)
    except (ValueError, TypeError):
        after_id = 0

    new_messages = thread.messages.filter(
        id__gt=after_id
    ).select_related('sender').order_by('created_at')

    # Mark incoming messages as read
    new_messages.filter(is_read=False).exclude(sender=request.user).update(is_read=True)

    messages_data = []
    for msg in new_messages:
        messages_data.append({
            'id': msg.id,
            'content': msg.content,
            'sender': msg.sender.username,
            'sender_id': msg.sender.id,
            'sender_role': msg.sender.role,
            'created_at': msg.created_at.strftime('%b %d, %Y %I:%M %p'),
            'is_mine': msg.sender.id == request.user.id,
        })

    return JsonResponse({
        'success': True,
        'messages': messages_data,
    })


@login_required
@require_http_methods(["GET"])
def api_unread_count(request):
    """AJAX endpoint to get total unread message count for navbar badge"""
    count = ChatMessage.objects.filter(
        thread__participants=request.user,
        is_read=False
    ).exclude(
        sender=request.user
    ).count()

    return JsonResponse({
        'success': True,
        'unread_count': count,
    })


@login_required
@require_POST
def api_mark_read(request, thread_id):
    """AJAX endpoint to mark all messages in a thread as read"""
    thread = get_object_or_404(ChatThread, id=thread_id)

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    updated = thread.messages.filter(
        is_read=False
    ).exclude(
        sender=request.user
    ).update(is_read=True)

    return JsonResponse({
        'success': True,
        'marked_count': updated,
    })


@login_required
def sse_thread_messages(request, thread_id):
    """SSE endpoint: pushes new messages instantly to the client"""
    thread = get_object_or_404(ChatThread, id=thread_id)

    if not thread.participants.filter(id=request.user.id).exists():
        return HttpResponseForbidden()

    last_id = int(request.GET.get('last_id', 0))

    def event_stream():
        nonlocal last_id
        heartbeat = 0
        while True:
            from django.db import connection
            # Ensure fresh query results (no stale cache)
            if connection.connection and not connection.is_usable():
                connection.close()

            new_msgs = list(
                ChatMessage.objects.filter(
                    thread_id=thread_id, id__gt=last_id
                ).exclude(
                    sender=request.user
                ).select_related('sender').order_by('created_at')
            )

            for msg in new_msgs:
                if not msg.is_read:
                    ChatMessage.objects.filter(id=msg.id).update(is_read=True)
                data = json.dumps({
                    'id': msg.id,
                    'content': msg.content,
                    'sender': msg.sender.username,
                    'sender_id': msg.sender.id,
                    'sender_role': msg.sender.role,
                    'created_at': msg.created_at.strftime('%b %d, %Y %I:%M %p'),
                    'is_mine': False,
                })
                last_id = msg.id
                yield f"data: {data}\n\n"

            # Heartbeat every ~15 seconds to keep connection alive
            heartbeat += 1
            if heartbeat % 30 == 0:
                yield ": heartbeat\n\n"

            time.sleep(0.5)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@login_required
def sse_unread_count(request):
    """SSE endpoint: pushes unread count changes instantly to the client"""
    user = request.user

    def event_stream():
        last_count = -1
        heartbeat = 0
        while True:
            from django.db import connection
            if connection.connection and not connection.is_usable():
                connection.close()

            count = ChatMessage.objects.filter(
                thread__participants=user,
                is_read=False
            ).exclude(
                sender=user
            ).count()

            if count != last_count:
                yield f"data: {json.dumps({'unread_count': count})}\n\n"
                last_count = count

            heartbeat += 1
            if heartbeat % 30 == 0:
                yield ": heartbeat\n\n"

            time.sleep(0.5)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
