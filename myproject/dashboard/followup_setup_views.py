from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Setup

def user_can_setup_followup(user):
    return getattr(user, 'can_setup_follow_up_status', False) or user.is_superuser or getattr(user, 'role', '') == 'administrator'

@login_required
def followup_setup_management(request):
    if not user_can_setup_followup(request.user):
        messages.error(request, 'You do not have permission to access Follow-up Status Setup.')
        return redirect('dashboard')
        
    status_setups = Setup.objects.filter(setup_type='followup_status').order_by('name')
    context = {
        'status_setups': status_setups,
        'page_title': 'Follow-up Status Setup',
    }
    return render(request, 'dashboard/followup_setup_management.html', context)

@login_required
def followup_setup_add(request):
    if not user_can_setup_followup(request.user):
        return redirect('dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'

        if not name:
            messages.error(request, '❌ Setup name is required!')
            return redirect('followup_setup_management')

        if Setup.objects.filter(setup_type='followup_status', name=name).exists():
            messages.error(request, f'❌ {name} already exists!')
            return redirect('followup_setup_management')

        try:
            Setup.objects.create(
                setup_type='followup_status',
                name=name,
                description=description,
                is_active=is_active
            )
            messages.success(request, f'✅ {name} setup created successfully!')
        except Exception as e:
            messages.error(request, f'❌ Error creating setup: {str(e)}')

    return redirect('followup_setup_management')

@login_required
def followup_setup_edit(request, setup_id):
    if not user_can_setup_followup(request.user):
        return redirect('dashboard')

    setup = get_object_or_404(Setup, id=setup_id, setup_type='followup_status')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'

        if not name:
            messages.error(request, '❌ Setup name is required!')
            return redirect('followup_setup_management')

        if Setup.objects.filter(setup_type='followup_status', name=name).exclude(id=setup_id).exists():
            messages.error(request, f'❌ {name} already exists!')
            return redirect('followup_setup_management')

        try:
            setup.name = name
            setup.description = description
            setup.is_active = is_active
            setup.save()
            messages.success(request, f'✅ {name} updated successfully!')
        except Exception as e:
            messages.error(request, f'❌ Error updating setup: {str(e)}')

    return redirect('followup_setup_management')

@login_required
def followup_setup_delete(request, setup_id):
    if not user_can_setup_followup(request.user):
        return redirect('dashboard')

    if request.method == 'POST':
        setup = get_object_or_404(Setup, id=setup_id, setup_type='followup_status')
        name = setup.name
        setup.delete()
        messages.success(request, f'✅ {name} deleted successfully!')
        
    return redirect('followup_setup_management')

@login_required
def followup_setup_toggle_default(request, setup_id):
    if not user_can_setup_followup(request.user):
        return redirect('dashboard')

    setup = get_object_or_404(Setup, id=setup_id, setup_type='followup_status')
    
    # If setting to default, unset default for all other follow-up statuses
    if not setup.is_default:
        Setup.objects.filter(setup_type='followup_status').update(is_default=False)
        setup.is_default = True
        messages.success(request, f'✅ {setup.name} set as default status!')
    else:
        setup.is_default = False
        messages.success(request, f'✅ {setup.name} is no longer the default status.')
        
    setup.save()
    return redirect('followup_setup_management')
