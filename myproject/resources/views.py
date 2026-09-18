import functools
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Resource, ResourceFile, ResourceRead, ResourceTopic

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB per file


def resources_access_required(view_func):
    """Allow admin/superuser OR users with can_view_resources / can_create_resources."""
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        u = request.user
        if (u.is_superuser or u.role == 'administrator'
                or getattr(u, 'can_view_resources', False)
                or getattr(u, 'can_create_resources', False)):
            return view_func(request, *args, **kwargs)
        messages.error(request, '🚫 You do not have access to Resources.', extra_tags='permission_denied')
        return redirect('dashboard')
    return wrapper


def can_manage_resources(user):
    return user.is_superuser or user.role == 'administrator' or getattr(user, 'can_create_resources', False)


def _validate_files(files):
    """Returns (valid_files, error_messages)."""
    valid, errors = [], []
    for f in files:
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext not in ResourceFile.ALLOWED_EXTENSIONS:
            errors.append(f'"{f.name}" was skipped — only audio, video, and PDF files are allowed.')
            continue
        if f.size > MAX_FILE_SIZE:
            errors.append(f'"{f.name}" was skipped — file exceeds the 500MB limit.')
            continue
        valid.append(f)
    return valid, errors


@login_required
@resources_access_required
def resource_list(request):
    topics = ResourceTopic.objects.all()

    topic_id = request.GET.get('topic', '').strip()
    search_q = request.GET.get('q', '').strip()

    qs = Resource.objects.select_related('topic', 'created_by').prefetch_related('files')

    active_topic = None
    if topic_id:
        active_topic = get_object_or_404(ResourceTopic, pk=topic_id)
        qs = qs.filter(topic=active_topic)
    if search_q:
        qs = qs.filter(Q(title__icontains=search_q) | Q(subtitle__icontains=search_q) | Q(content__icontains=search_q))

    read_ids = set()
    if request.user.is_authenticated:
        read_ids = set(ResourceRead.objects.filter(user=request.user).values_list('resource_id', flat=True))

    context = {
        'topics': topics,
        'active_topic': active_topic,
        'search_q': search_q,
        'resources': qs,
        'total_count': Resource.objects.count(),
        'read_ids': read_ids,
        'can_manage': can_manage_resources(request.user),
        'color_choices': ResourceTopic.COLOR_CHOICES,
        'icon_choices': ResourceTopic.ICON_CHOICES,
    }
    return render(request, 'resources/list.html', context)


@login_required
@resources_access_required
def resource_detail(request, pk):
    resource = get_object_or_404(Resource.objects.select_related('topic', 'created_by').prefetch_related('files'), pk=pk)
    context = {
        'resource': resource,
        'can_manage': can_manage_resources(request.user),
        'is_read': resource.is_read_by(request.user),
    }
    return render(request, 'resources/detail.html', context)


@login_required
@resources_access_required
def resource_create(request):
    if not can_manage_resources(request.user):
        messages.error(request, '🚫 Only administrators can create resources.', extra_tags='permission_denied')
        return redirect('resources:list')

    topics = ResourceTopic.objects.all()
    preselect_topic_id = request.GET.get('topic', '').strip()
    selected_topic_id = int(preselect_topic_id) if preselect_topic_id.isdigit() else None

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        subtitle = request.POST.get('subtitle', '').strip()
        content = request.POST.get('content', '').strip()
        notes = request.POST.get('notes', '').strip()
        version = request.POST.get('version', '').strip() or 'v1.0'
        topic_id = request.POST.get('topic', '').strip()
        files = request.FILES.getlist('files')

        if not title:
            messages.error(request, '❌ Title is required.')
            return render(request, 'resources/form.html', {'topics': topics, 'selected_topic_id': selected_topic_id})

        topic = None
        if topic_id:
            topic = ResourceTopic.objects.filter(pk=topic_id).first()

        valid_files, file_errors = _validate_files(files)
        for err in file_errors:
            messages.warning(request, err)

        resource = Resource.objects.create(
            title=title, subtitle=subtitle, content=content, notes=notes,
            version=version, topic=topic, created_by=request.user,
        )
        for f in valid_files:
            ResourceFile.objects.create(resource=resource, file=f, uploaded_by=request.user)

        messages.success(request, f'✅ Article "{resource.title}" published successfully!')
        return redirect('resources:detail', pk=resource.pk)

    return render(request, 'resources/form.html', {'topics': topics, 'selected_topic_id': selected_topic_id})


@login_required
@resources_access_required
def resource_edit(request, pk):
    if not can_manage_resources(request.user):
        messages.error(request, '🚫 Only administrators can edit resources.', extra_tags='permission_denied')
        return redirect('resources:detail', pk=pk)

    resource = get_object_or_404(Resource.objects.prefetch_related('files'), pk=pk)
    topics = ResourceTopic.objects.all()
    selected_topic_id = resource.topic_id

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        subtitle = request.POST.get('subtitle', '').strip()
        content = request.POST.get('content', '').strip()
        notes = request.POST.get('notes', '').strip()
        version = request.POST.get('version', '').strip() or 'v1.0'
        topic_id = request.POST.get('topic', '').strip()
        files = request.FILES.getlist('files')

        if not title:
            messages.error(request, '❌ Title is required.')
            return render(request, 'resources/form.html', {'resource': resource, 'topics': topics, 'selected_topic_id': selected_topic_id})

        valid_files, file_errors = _validate_files(files)
        for err in file_errors:
            messages.warning(request, err)

        resource.title = title
        resource.subtitle = subtitle
        resource.content = content
        resource.notes = notes
        resource.version = version
        resource.topic = ResourceTopic.objects.filter(pk=topic_id).first() if topic_id else None
        resource.save()

        for f in valid_files:
            ResourceFile.objects.create(resource=resource, file=f, uploaded_by=request.user)

        messages.success(request, f'✅ Article "{resource.title}" updated successfully!')
        return redirect('resources:detail', pk=resource.pk)

    return render(request, 'resources/form.html', {'resource': resource, 'topics': topics, 'selected_topic_id': selected_topic_id})


@login_required
@resources_access_required
@require_POST
def resource_delete(request, pk):
    if not can_manage_resources(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    resource = get_object_or_404(Resource, pk=pk)
    title = resource.title
    for rf in resource.files.all():
        rf.file.delete(save=False)
    resource.delete()
    messages.success(request, f'🗑️ Article "{title}" deleted.')
    return redirect('resources:list')


@login_required
@resources_access_required
@require_POST
def resource_file_delete(request, pk, file_id):
    if not can_manage_resources(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    rf = get_object_or_404(ResourceFile, pk=file_id, resource_id=pk)
    rf.file.delete(save=False)
    rf.delete()
    return JsonResponse({'success': True})


@login_required
@resources_access_required
@require_POST
def resource_mark_read(request, pk):
    """Toggle the current user's personal read status for an article."""
    resource = get_object_or_404(Resource, pk=pk)
    existing = ResourceRead.objects.filter(resource=resource, user=request.user).first()
    if existing:
        existing.delete()
        return JsonResponse({'success': True, 'read': False})
    ResourceRead.objects.create(resource=resource, user=request.user)
    return JsonResponse({'success': True, 'read': True})


# ── Topic (sidebar category) management — AJAX, manager-only ──────────────

@login_required
@resources_access_required
@require_POST
def topic_create(request):
    if not can_manage_resources(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        data = request.POST

    name = (data.get('name') or '').strip()
    icon = (data.get('icon') or 'fa-folder').strip()
    color = (data.get('color') or '#6366f1').strip()

    if not name:
        return JsonResponse({'success': False, 'message': 'Topic name is required.'}, status=400)

    valid_icons = {c[0] for c in ResourceTopic.ICON_CHOICES}
    if icon not in valid_icons:
        icon = 'fa-folder'

    topic = ResourceTopic.objects.create(name=name, icon=icon, color=color, created_by=request.user)
    return JsonResponse({
        'success': True,
        'topic': {'id': topic.id, 'name': topic.name, 'icon': topic.icon, 'color': topic.color, 'article_count': 0},
    })


@login_required
@resources_access_required
@require_POST
def topic_edit(request, pk):
    if not can_manage_resources(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    topic = get_object_or_404(ResourceTopic, pk=pk)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        data = request.POST

    name = (data.get('name') or '').strip()
    icon = (data.get('icon') or topic.icon).strip()
    color = (data.get('color') or topic.color).strip()

    if not name:
        return JsonResponse({'success': False, 'message': 'Topic name is required.'}, status=400)

    valid_icons = {c[0] for c in ResourceTopic.ICON_CHOICES}
    if icon not in valid_icons:
        icon = topic.icon

    topic.name = name
    topic.icon = icon
    topic.color = color
    topic.save()
    return JsonResponse({
        'success': True,
        'topic': {'id': topic.id, 'name': topic.name, 'icon': topic.icon, 'color': topic.color, 'article_count': topic.article_count},
    })


@login_required
@resources_access_required
@require_POST
def topic_delete(request, pk):
    if not can_manage_resources(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    topic = get_object_or_404(ResourceTopic, pk=pk)
    topic.delete()  # articles keep existing (topic set to NULL via SET_NULL)
    return JsonResponse({'success': True})
