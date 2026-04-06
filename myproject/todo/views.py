import json
import functools
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q, Count
from django.utils import timezone

from .models import Task, TaskComment

User = get_user_model()


def todo_access_required(view_func):
    """Decorator: allow admin/superuser OR users with can_access_todo permission
    OR any user who has tasks assigned to / created by them."""
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        u = request.user
        if u.is_superuser or u.role == 'administrator' or getattr(u, 'can_access_todo', False):
            return view_func(request, *args, **kwargs)
        # Also allow any user who has tasks assigned to or created by them
        if Task.objects.filter(Q(assigned_to=u) | Q(created_by=u)).exists():
            return view_func(request, *args, **kwargs)
        messages.error(request, '🚫 You do not have access to the Task Board.')
        return redirect('dashboard')
    return wrapper


def can_manage_tasks(user):
    """Admin and administrator roles can create/edit/assign tasks."""
    return user.is_superuser or user.role == 'administrator'


@login_required
@todo_access_required
def task_board(request):
    """Main Kanban board view showing Todo / Doing / Completed columns."""
    u = request.user
    is_manager = can_manage_tasks(u)

    qs = Task.objects.select_related('assigned_to', 'created_by')
    if not is_manager:
        # Non-managers only see tasks assigned to them or created by them
        qs = qs.filter(Q(assigned_to=u) | Q(created_by=u))

    # Filters
    status_filter = request.GET.get('status', '')
    assignee_filter = request.GET.get('assignee', '')
    priority_filter = request.GET.get('priority', '')
    search_q = request.GET.get('q', '').strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if assignee_filter:
        qs = qs.filter(assigned_to_id=assignee_filter)
    if priority_filter:
        qs = qs.filter(priority=priority_filter)
    if search_q:
        qs = qs.filter(Q(title__icontains=search_q) | Q(description__icontains=search_q))

    todo_tasks = qs.filter(status=Task.STATUS_TODO)
    doing_tasks = qs.filter(status=Task.STATUS_DOING)
    done_tasks = qs.filter(status=Task.STATUS_DONE)

    # Users available for assignee filter dropdown
    if is_manager:
        assignable_users = User.objects.filter(
            is_active=True, is_deleted=False
        ).order_by('username')
    else:
        assignable_users = User.objects.none()

    context = {
        'todo_tasks': todo_tasks,
        'doing_tasks': doing_tasks,
        'done_tasks': done_tasks,
        'is_manager': is_manager,
        'assignable_users': assignable_users,
        'status_filter': status_filter,
        'assignee_filter': assignee_filter,
        'priority_filter': priority_filter,
        'search_q': search_q,
        'total_todo': qs.filter(status=Task.STATUS_TODO).count() if not status_filter else todo_tasks.count(),
        'total_doing': qs.filter(status=Task.STATUS_DOING).count() if not status_filter else doing_tasks.count(),
        'total_done': qs.filter(status=Task.STATUS_DONE).count() if not status_filter else done_tasks.count(),
    }
    return render(request, 'todo/board.html', context)


@login_required
@todo_access_required
def task_detail(request, task_id):
    """Task detail page with comment form."""
    u = request.user
    is_manager = can_manage_tasks(u)

    task = get_object_or_404(Task, id=task_id)

    # Access check: non-managers can only view tasks assigned/created by them
    if not is_manager and task.assigned_to != u and task.created_by != u:
        messages.error(request, '🚫 You do not have permission to view this task.')
        return redirect('todo:board')

    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        if content:
            TaskComment.objects.create(task=task, author=u, content=content)
            messages.success(request, '💬 Comment added.')
        return redirect('todo:task_detail', task_id=task_id)

    comments = task.comments.select_related('author').all()
    context = {
        'task': task,
        'comments': comments,
        'is_manager': is_manager,
        'can_edit': is_manager or task.created_by == u,
        'can_change_status': is_manager or task.assigned_to == u,
    }
    return render(request, 'todo/task_detail.html', context)


@login_required
@todo_access_required
def task_create(request):
    """Create a new task (admin/manager only)."""
    if not can_manage_tasks(request.user):
        messages.error(request, '🚫 Only administrators can create tasks.')
        return redirect('todo:board')

    assignable_users = User.objects.filter(
        is_active=True, is_deleted=False
    ).order_by('username')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        priority = request.POST.get('priority', Task.PRIORITY_MEDIUM)
        assigned_to_id = request.POST.get('assigned_to', '')
        due_date_str = request.POST.get('due_date', '').strip()

        if not title:
            messages.error(request, '❌ Task title is required.')
            return render(request, 'todo/task_form.html', {
                'assignable_users': assignable_users,
                'priorities': Task.PRIORITY_CHOICES,
            })

        task = Task(
            title=title,
            description=description,
            priority=priority,
            status=Task.STATUS_TODO,
            created_by=request.user,
        )

        if assigned_to_id:
            try:
                assignee = User.objects.get(id=assigned_to_id, is_active=True, is_deleted=False)
                task.assigned_to = assignee
            except User.DoesNotExist:
                pass

        if due_date_str:
            try:
                from datetime import date
                task.due_date = date.fromisoformat(due_date_str)
            except ValueError:
                pass

        task.save()

        # Notify assignee via existing chat system
        if task.assigned_to and task.assigned_to != request.user:
            _notify_assignee(request.user, task)

        messages.success(request, f'✅ Task "{task.title}" created successfully!')
        return redirect('todo:board')

    return render(request, 'todo/task_form.html', {
        'assignable_users': assignable_users,
        'priorities': Task.PRIORITY_CHOICES,
    })


@login_required
@todo_access_required
def task_edit(request, task_id):
    """Edit an existing task (admin/manager only)."""
    if not can_manage_tasks(request.user):
        messages.error(request, '🚫 Only administrators can edit tasks.')
        return redirect('todo:board')

    task = get_object_or_404(Task, id=task_id)
    assignable_users = User.objects.filter(
        is_active=True, is_deleted=False
    ).order_by('username')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        priority = request.POST.get('priority', task.priority)
        status = request.POST.get('status', task.status)
        assigned_to_id = request.POST.get('assigned_to', '')
        due_date_str = request.POST.get('due_date', '').strip()

        if not title:
            messages.error(request, '❌ Task title is required.')
            return render(request, 'todo/task_form.html', {
                'task': task,
                'assignable_users': assignable_users,
                'priorities': Task.PRIORITY_CHOICES,
                'statuses': Task.STATUS_CHOICES,
            })

        old_assignee = task.assigned_to
        task.title = title
        task.description = description
        task.priority = priority
        task.status = status

        if assigned_to_id:
            try:
                assignee = User.objects.get(id=assigned_to_id, is_active=True, is_deleted=False)
                task.assigned_to = assignee
            except User.DoesNotExist:
                task.assigned_to = None
        else:
            task.assigned_to = None

        if due_date_str:
            try:
                from datetime import date
                task.due_date = date.fromisoformat(due_date_str)
            except ValueError:
                task.due_date = None
        else:
            task.due_date = None

        task.save()

        # Notify new assignee if changed
        if task.assigned_to and task.assigned_to != old_assignee and task.assigned_to != request.user:
            _notify_assignee(request.user, task)

        messages.success(request, f'✅ Task "{task.title}" updated successfully!')
        return redirect('todo:task_detail', task_id=task.id)

    return render(request, 'todo/task_form.html', {
        'task': task,
        'assignable_users': assignable_users,
        'priorities': Task.PRIORITY_CHOICES,
        'statuses': Task.STATUS_CHOICES,
    })


@login_required
@todo_access_required
@require_POST
def task_update_status(request, task_id):
    """AJAX endpoint to move a task to a new status column."""
    task = get_object_or_404(Task, id=task_id)
    u = request.user
    is_manager = can_manage_tasks(u)

    # Only manager, assignee, or creator may change status
    if not is_manager and task.assigned_to != u and task.created_by != u:
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    try:
        data = json.loads(request.body)
        new_status = data.get('status', '')
    except (json.JSONDecodeError, AttributeError):
        new_status = request.POST.get('status', '')

    valid = [s[0] for s in Task.STATUS_CHOICES]
    if new_status not in valid:
        return JsonResponse({'success': False, 'message': 'Invalid status.'}, status=400)

    task.status = new_status
    task.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'success': True, 'status': task.status, 'status_display': task.get_status_display()})


@login_required
@todo_access_required
@require_POST
def task_delete(request, task_id):
    """Delete a task (admin/manager only)."""
    if not can_manage_tasks(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    task = get_object_or_404(Task, id=task_id)
    title = task.title
    task.delete()
    messages.success(request, f'🗑️ Task "{title}" deleted.')
    return redirect('todo:board')


def _notify_assignee(creator, task):
    """Send a chat notification to the task's assignee using the existing messaging system."""
    try:
        from chat.models import ChatThread, ChatMessage

        # Find or create a 1-on-1 thread between creator and assignee
        threads = ChatThread.objects.filter(
            is_group=False,
            participants=creator
        ).filter(
            participants=task.assigned_to
        )

        if threads.exists():
            thread = threads.first()
        else:
            thread = ChatThread.objects.create(created_by=creator)
            thread.participants.add(creator, task.assigned_to)

        priority_labels = {'low': '🟢', 'medium': '🔵', 'high': '🟡', 'urgent': '🔴'}
        icon = priority_labels.get(task.priority, '⚪')
        due = f' | Due: {task.due_date}' if task.due_date else ''
        msg = (
            f'📋 You have been assigned a new task:\n'
            f'{task.title}\n'
            f'{icon} Priority: {task.get_priority_display()}{due}\n'
            f'{"Description: " + task.description[:200] if task.description else ""}'
        ).strip()

        ChatMessage.objects.create(thread=thread, sender=creator, content=msg)

        # Update thread timestamp
        thread.save()
    except Exception:
        pass  # Notification failure should not break task creation

