import json
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
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
        if thread.is_group:
            # Group thread
            members = thread.participants.exclude(id=request.user.id)
            last_msg = thread.get_last_message()
            thread_list.append({
                'thread': thread,
                'is_group': True,
                'group_name': thread.group_name or 'Unnamed Group',
                'members': members,
                'member_count': thread.participants.count(),
                'last_message': last_msg,
                'unread_count': thread.unread_count,
            })
        else:
            # 1-on-1 thread
            other_user = thread.get_other_participant(request.user)
            last_msg = thread.get_last_message()
            if other_user:
                thread_list.append({
                    'thread': thread,
                    'is_group': False,
                    'other_user': other_user,
                    'last_message': last_msg,
                    'unread_count': thread.unread_count,
                })

    # All users can message any other user
    available_users = User.objects.filter(
        is_active=True, is_deleted=False
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

    # Mark unread messages as read
    thread.messages.filter(
        is_read=False
    ).exclude(
        sender=request.user
    ).update(is_read=True)

    messages_list = thread.messages.select_related('sender').order_by('created_at')

    is_admin = request.user.is_superuser or request.user.role == 'administrator'

    if thread.is_group:
        # Group chat context
        members = thread.participants.all()
        # Available users to add (not already in group)
        available_to_add = User.objects.filter(
            is_active=True, is_deleted=False
        ).exclude(id__in=members.values_list('id', flat=True)).order_by('username')
        context = {
            'thread': thread,
            'is_group': True,
            'group_name': thread.group_name or 'Unnamed Group',
            'members': members,
            'member_count': members.count(),
            'messages': messages_list,
            'is_admin': is_admin,
            'is_creator': thread.created_by == request.user,
            'available_to_add': available_to_add,
        }
    else:
        # 1-on-1 chat context
        other_user = thread.get_other_participant(request.user)
        context = {
            'thread': thread,
            'is_group': False,
            'other_user': other_user,
            'messages': messages_list,
            'is_admin': is_admin,
        }

    return render(request, 'chat/thread.html', context)


@login_required
def start_chat(request, user_id):
    """Start a new chat with a user, or redirect to existing thread"""
    other_user = get_object_or_404(User, id=user_id, is_active=True, is_deleted=False)

    # Check if a 1-on-1 thread already exists between these two users
    existing_threads = ChatThread.objects.filter(
        is_group=False,
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
def create_group(request):
    """Create a new group chat - only admins can create groups"""
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        return JsonResponse({'success': False, 'message': 'Only administrators can create groups'}, status=403)

    try:
        data = json.loads(request.body)
        group_name = data.get('group_name', '').strip()
        member_ids = data.get('member_ids', [])

        if not group_name:
            group_name = 'New Group'

        if not member_ids or len(member_ids) < 1:
            return JsonResponse({'success': False, 'message': 'Select at least 1 member'}, status=400)

        # Validate members exist
        members = User.objects.filter(id__in=member_ids, is_active=True, is_deleted=False)
        if members.count() == 0:
            return JsonResponse({'success': False, 'message': 'No valid members selected'}, status=400)

        # Create group thread
        thread = ChatThread.objects.create(
            is_group=True,
            group_name=group_name,
            created_by=request.user,
        )
        thread.participants.add(request.user, *members)

        return JsonResponse({
            'success': True,
            'thread_id': thread.id,
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid request'}, status=400)


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
                'sender': message.sender.get_full_name() or message.sender.username,
                'sender_id': message.sender.id,
                'sender_role': message.sender.role,
                'created_at': message.created_at.strftime('%b %d, %Y %I:%M %p'),
                'is_mine': True,
                'is_deleted': False,
                'is_edited': False,
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
            'sender': msg.sender.get_full_name() or msg.sender.username,
            'sender_id': msg.sender.id,
            'sender_role': msg.sender.role,
            'created_at': msg.created_at.strftime('%b %d, %Y %I:%M %p'),
            'is_mine': msg.sender.id == request.user.id,
            'is_deleted': msg.is_deleted,
            'is_edited': msg.is_edited,
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


# ==================== NEW FEATURES ====================

@login_required
@require_POST
def api_delete_message(request, message_id):
    """Soft-delete a message. Sender can delete own messages; admin can delete any."""
    message = get_object_or_404(ChatMessage, id=message_id)
    thread = message.thread

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    is_admin = request.user.is_superuser or request.user.role == 'administrator'
    if message.sender != request.user and not is_admin:
        return JsonResponse({'success': False, 'message': 'You can only delete your own messages'}, status=403)

    message.is_deleted = True
    message.content = 'This message was deleted.'
    message.save(update_fields=['is_deleted', 'content'])

    return JsonResponse({'success': True, 'message_id': message.id})


@login_required
@require_POST
def api_edit_message(request, message_id):
    """Edit a message. Only the sender can edit their own messages."""
    message = get_object_or_404(ChatMessage, id=message_id)
    thread = message.thread

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    if message.sender != request.user:
        return JsonResponse({'success': False, 'message': 'You can only edit your own messages'}, status=403)

    if message.is_deleted:
        return JsonResponse({'success': False, 'message': 'Cannot edit a deleted message'}, status=400)

    try:
        data = json.loads(request.body)
        new_content = data.get('content', '').strip()
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid request'}, status=400)

    if not new_content:
        return JsonResponse({'success': False, 'message': 'Message cannot be empty'}, status=400)

    message.content = new_content
    message.is_edited = True
    message.edited_at = timezone.now()
    message.save(update_fields=['content', 'is_edited', 'edited_at'])

    return JsonResponse({
        'success': True,
        'message_id': message.id,
        'content': message.content,
    })


@login_required
@require_POST
def api_remove_member(request, thread_id):
    """Remove a member from a group chat. Admin or group creator only."""
    thread = get_object_or_404(ChatThread, id=thread_id, is_group=True)

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    is_admin = request.user.is_superuser or request.user.role == 'administrator'
    is_creator = thread.created_by == request.user
    if not (is_admin or is_creator):
        return JsonResponse({'success': False, 'message': 'Only admins can remove members'}, status=403)

    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid request'}, status=400)

    if not user_id:
        return JsonResponse({'success': False, 'message': 'User ID required'}, status=400)

    # Cannot remove yourself via this endpoint (use leave instead)
    if int(user_id) == request.user.id:
        return JsonResponse({'success': False, 'message': 'Use the leave group option to remove yourself'}, status=400)

    member = get_object_or_404(User, id=user_id)
    if not thread.participants.filter(id=member.id).exists():
        return JsonResponse({'success': False, 'message': 'User is not in this group'}, status=400)

    thread.participants.remove(member)

    # Post a system message
    ChatMessage.objects.create(
        thread=thread,
        sender=request.user,
        content=f"🚫 {request.user.get_full_name() or request.user.username} removed {member.get_full_name() or member.username} from the group."
    )

    return JsonResponse({
        'success': True,
        'removed_user_id': member.id,
        'removed_user_name': member.get_full_name() or member.username,
        'member_count': thread.participants.count(),
    })


@login_required
@require_POST
def api_add_members(request, thread_id):
    """Add members to an existing group chat. Admin or group creator only."""
    thread = get_object_or_404(ChatThread, id=thread_id, is_group=True)

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    is_admin = request.user.is_superuser or request.user.role == 'administrator'
    is_creator = thread.created_by == request.user
    if not (is_admin or is_creator):
        return JsonResponse({'success': False, 'message': 'Only admins can add members'}, status=403)

    try:
        data = json.loads(request.body)
        user_ids = data.get('user_ids', [])
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid request'}, status=400)

    if not user_ids:
        return JsonResponse({'success': False, 'message': 'No users selected'}, status=400)

    new_members = User.objects.filter(id__in=user_ids, is_active=True, is_deleted=False)
    added_names = []
    for member in new_members:
        if not thread.participants.filter(id=member.id).exists():
            thread.participants.add(member)
            added_names.append(member.get_full_name() or member.username)

    if added_names:
        ChatMessage.objects.create(
            thread=thread,
            sender=request.user,
            content=f"✅ {request.user.get_full_name() or request.user.username} added {', '.join(added_names)} to the group."
        )

    return JsonResponse({
        'success': True,
        'added_count': len(added_names),
        'member_count': thread.participants.count(),
    })


@login_required
@require_POST
def api_leave_group(request, thread_id):
    """Leave a group chat."""
    thread = get_object_or_404(ChatThread, id=thread_id, is_group=True)

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'You are not in this group'}, status=403)

    thread.participants.remove(request.user)

    # Post a system message
    ChatMessage.objects.create(
        thread=thread,
        sender=request.user,
        content=f"👋 {request.user.get_full_name() or request.user.username} left the group."
    )

    return JsonResponse({'success': True})


@login_required
@require_POST
def api_delete_group(request, thread_id):
    """Delete a group entirely. Admin or creator only."""
    thread = get_object_or_404(ChatThread, id=thread_id, is_group=True)

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    is_admin = request.user.is_superuser or request.user.role == 'administrator'
    is_creator = thread.created_by == request.user
    if not (is_admin or is_creator):
        return JsonResponse({'success': False, 'message': 'Only admins or creators can delete this group'}, status=403)

    thread.delete()
    return JsonResponse({'success': True})


@login_required
@require_POST
def api_delete_thread(request, thread_id):
    """Delete (clear) a 1-on-1 conversation. Any participant can do this."""
    thread = get_object_or_404(ChatThread, id=thread_id)

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    thread.delete()
    return JsonResponse({'success': True})


@login_required
@require_POST
def api_rename_group(request, thread_id):
    """Rename a group chat. Admin or creator only."""
    thread = get_object_or_404(ChatThread, id=thread_id, is_group=True)

    if not thread.participants.filter(id=request.user.id).exists():
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    is_admin = request.user.is_superuser or request.user.role == 'administrator'
    is_creator = thread.created_by == request.user
    if not (is_admin or is_creator):
        return JsonResponse({'success': False, 'message': 'Only admins can rename the group'}, status=403)

    try:
        data = json.loads(request.body)
        new_name = data.get('group_name', '').strip()
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid request'}, status=400)

    if not new_name:
        return JsonResponse({'success': False, 'message': 'Group name cannot be empty'}, status=400)

    old_name = thread.group_name
    thread.group_name = new_name
    thread.save(update_fields=['group_name'])

    ChatMessage.objects.create(
        thread=thread,
        sender=request.user,
        content=f"✏️ Group renamed from \"{old_name}\" to \"{new_name}\"."
    )

    return JsonResponse({'success': True, 'group_name': new_name})
