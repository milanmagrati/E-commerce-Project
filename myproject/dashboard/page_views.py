from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from store.models import Page

@login_required(login_url='login')
def page_list(request):
    # Only admins or superusers should access this ideally
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, 'Permission Denied')
        return redirect('dashboard')
    
    pages = Page.objects.all().order_by('-created_at')
    return render(request, 'dashboard/pages/page_list.html', {'pages': pages})

@login_required(login_url='login')
def page_create(request):
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, 'Permission Denied')
        return redirect('dashboard')

    if request.method == 'POST':
        title = request.POST.get('title')
        slug = request.POST.get('slug')
        content = request.POST.get('content')
        is_published = request.POST.get('is_published') == 'on'
        
        if Page.objects.filter(slug=slug).exists():
            messages.error(request, 'A page with this slug already exists.')
        else:
            Page.objects.create(
                title=title,
                slug=slug,
                content=content,
                is_published=is_published
            )
            messages.success(request, 'Page created successfully.')
            return redirect('page_list')
            
    return render(request, 'dashboard/pages/page_form.html')

@login_required(login_url='login')
def page_edit(request, pk):
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, 'Permission Denied')
        return redirect('dashboard')

    page = get_object_or_404(Page, pk=pk)
    if request.method == 'POST':
        page.title = request.POST.get('title')
        page.slug = request.POST.get('slug')
        page.content = request.POST.get('content')
        page.is_published = request.POST.get('is_published') == 'on'
        
        # Check slug uniqueness excluding current page
        if Page.objects.filter(slug=page.slug).exclude(pk=page.pk).exists():
            messages.error(request, 'A page with this slug already exists.')
        else:
            page.save()
            messages.success(request, 'Page updated successfully.')
            return redirect('page_list')
            
    return render(request, 'dashboard/pages/page_form.html', {'page': page})

@login_required(login_url='login')
def page_delete(request, pk):
    if not (request.user.is_superuser or request.user.role == 'administrator'):
        messages.error(request, 'Permission Denied')
        return redirect('dashboard')

    page = get_object_or_404(Page, pk=pk)
    if request.method == 'POST':
        page.delete()
        messages.success(request, 'Page deleted successfully.')
        return redirect('page_list')
    return redirect('page_list')
