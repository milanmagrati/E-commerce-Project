import os

input_path = r'c:\Users\milan\OneDrive\Desktop\E-commerce-Project\myproject\accounts\templates\accounts\user_create.html'
output_path = r'c:\Users\milan\OneDrive\Desktop\E-commerce-Project\myproject\accounts\templates\accounts\role_permissions.html'

with open(input_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

permissions_html = "".join(lines[138:681])

template = """{% extends 'base.html' %}
{% block title %}Accessibility Management - {{ role.display_name }}{% endblock %}

{% block extra_css %}
<style>
    .bg-gradient-primary {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }

    .form-section {
        margin-bottom: 30px;
        padding: 20px;
        background: #f8f9fa;
        border-radius: 8px;
    }

    .section-title {
        font-size: 18px;
        font-weight: 600;
        color: #333;
        margin-bottom: 20px;
        padding-bottom: 10px;
        border-bottom: 2px solid #dee2e6;
    }

    .section-title i {
        color: #667eea;
        margin-right: 8px;
    }

    .perm-card {
        border: 1px solid #dee2e6;
        border-radius: 8px;
        overflow: hidden;
        height: 100%;
        background: white;
        transition: all 0.3s;
    }

    .perm-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        transform: translateY(-2px);
    }

    .perm-card-header {
        padding: 12px 15px;
        color: white;
        font-weight: 600;
        font-size: 14px;
    }

    .perm-card-body {
        padding: 15px;
    }

    .perm-item {
        padding: 10px;
        border-bottom: 1px solid #f0f0f0;
        display: flex;
        align-items: center;
    }

    .perm-item:last-child {
        border-bottom: none;
    }

    .perm-item:hover {
        background: #f8f9fa;
    }

    .perm-item input[type="checkbox"] {
        width: 20px;
        height: 20px;
        margin-right: 10px;
        cursor: pointer;
    }

    .perm-item label {
        margin: 0;
        cursor: pointer;
        flex: 1;
        font-size: 14px;
    }
</style>
{% endblock %}

{% block content %}
<div class="container-fluid py-4">
    <div class="row mb-4">
        <div class="col-12">
            <div class="card shadow-sm">
                <div class="card-header bg-gradient-primary text-white">
                    <div class="d-flex justify-content-between align-items-center">
                        <h4 class="mb-0"><i class="fas fa-shield-alt"></i> Accessibility Management - {{ role.display_name }}</h4>
                        <a href="{% url 'role_list' %}" class="btn btn-light btn-sm">
                            <i class="fas fa-arrow-left"></i> Back to Roles
                        </a>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="row">
        <div class="col-12">
            <div class="card shadow-sm">
                <div class="card-body p-4">
                    <form method="POST" id="rolePermissionsForm">
                        {% csrf_token %}
                        
                        <div class="d-flex justify-content-end mb-3">
                            <button type="button" class="btn btn-outline-primary btn-sm me-2" onclick="checkAllPermissions()">Check All</button>
                            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="uncheckAllPermissions()">Uncheck All</button>
                        </div>

""" + permissions_html + """
                        
                        <div class="d-flex justify-content-between mt-4 pt-3 border-top">
                            <a href="{% url 'role_list' %}" class="btn btn-secondary btn-lg">
                                <i class="fas fa-times"></i> Cancel
                            </a>
                            <button type="submit" class="btn btn-primary btn-lg">
                                <i class="fas fa-save"></i> Save Default Permissions
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
const savedPermissions = {{ saved_permissions|safe }};
const savedDiscount = '{{ saved_discount|default:"0.00" }}';

window.addEventListener('DOMContentLoaded', function() {
    savedPermissions.forEach(perm => {
        const checkbox = document.getElementById(perm);
        if (checkbox) checkbox.checked = true;
    });

    const discountInput = document.getElementById('max_discount_percent');
    if (discountInput) discountInput.value = savedDiscount;

    updateDiscountInput();

    const hrmMain = document.getElementById('can_view_hrm');
    if (hrmMain) toggleHrmSubPermissions(hrmMain);
});

function updateDiscountInput() {
    const giveDiscounts = document.getElementById('can_give_discounts');
    const discountInput = document.getElementById('max_discount_percent');
    if (giveDiscounts && giveDiscounts.checked) {
        discountInput.disabled = false;
        if (!discountInput.value || discountInput.value === '0' || discountInput.value === '0.00') {
            discountInput.value = '10.00';
        }
    } else if (discountInput) {
        discountInput.disabled = true;
        discountInput.value = '0.00';
    }
}

if (document.getElementById('can_give_discounts')) {
    document.getElementById('can_give_discounts').addEventListener('change', updateDiscountInput);
}

function checkAllPermissions() {
    document.querySelectorAll('#rolePermissionsForm input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
    });
    document.getElementById('max_discount_percent').value = '100.00';
    const hrmSub = document.getElementById('hrmSubPermissions');
    if (hrmSub) { hrmSub.style.opacity = '1'; hrmSub.style.pointerEvents = 'auto'; }
    updateDiscountInput();
}

function uncheckAllPermissions() {
    document.querySelectorAll('#rolePermissionsForm input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });
    document.getElementById('max_discount_percent').value = '0.00';
    const hrmSub = document.getElementById('hrmSubPermissions');
    if (hrmSub) { hrmSub.style.opacity = '0.5'; hrmSub.style.pointerEvents = 'none'; }
    updateDiscountInput();
}

function toggleHrmSubPermissions(mainCheckbox) {
    const subPerms = document.getElementById('hrmSubPermissions');
    if (!subPerms) return;
    if (mainCheckbox.checked) {
        subPerms.style.opacity = '1';
        subPerms.style.pointerEvents = 'auto';
    } else {
        subPerms.style.opacity = '0.5';
        subPerms.style.pointerEvents = 'none';
        subPerms.querySelectorAll('.hrm-sub-perm').forEach(cb => cb.checked = false);
    }
}
</script>
{% endblock %}
"""

with open(output_path, 'w', encoding='utf-8') as out_f:
    out_f.write(template)
print('role_permissions.html created successfully.')
