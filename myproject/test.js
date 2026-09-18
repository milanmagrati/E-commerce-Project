
// Toggle password visibility
const togglePassword = document.getElementById('togglePassword');
const passwordInput = document.getElementById('password');

if (togglePassword && passwordInput) {
    togglePassword.addEventListener('click', function() {
        const type = passwordInput.type === 'password' ? 'text' : 'password';
        passwordInput.type = type;
        this.querySelector('i').classList.toggle('fa-eye');
        this.querySelector('i').classList.toggle('fa-eye-slash');
    });
}

// Check all permissions
function checkAll() {
    document.querySelectorAll('#permissionsSection input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
    });
    document.getElementById('max_discount_percent').value = '100.00';
    updateDiscountInput();
    const hrmSub = document.getElementById('hrmSubPermissions');
    if (hrmSub) { hrmSub.style.opacity = '1'; hrmSub.style.pointerEvents = 'auto'; }
}

// Uncheck all permissions
function uncheckAll() {
    document.querySelectorAll('#permissionsSection input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });
    document.getElementById('max_discount_percent').value = '0.00';
    const hrmSub = document.getElementById('hrmSubPermissions');
    if (hrmSub) { hrmSub.style.opacity = '0.5'; hrmSub.style.pointerEvents = 'none'; }
    updateDiscountInput();
}

// HRM sub-permissions toggle
function toggleHrmSubPermissions(mainCheckbox) {
    const subPerms = document.getElementById('hrmSubPermissions');
    if (mainCheckbox.checked) {
        subPerms.style.opacity = '1';
        subPerms.style.pointerEvents = 'auto';
    } else {
        subPerms.style.opacity = '0.5';
        subPerms.style.pointerEvents = 'none';
        subPerms.querySelectorAll('.hrm-sub-perm').forEach(cb => cb.checked = false);
    }
}

// Hide/show permissions based on role
const roleDefaults = {};
document.getElementById('roleSelect').addEventListener('change', function() {
    const permSection = document.getElementById('permissionsSection');
    const roleAlert = document.getElementById('roleAlert');
    const selectedOption = this.options[this.selectedIndex];
    const roleName = selectedOption.textContent;

    if (this.value === 'administrator') {
        if (permSection) permSection.style.display = 'none';
        roleAlert.innerHTML = '<i class="fas fa-crown text-warning"></i> <strong>Administrator selected:</strong> Full access granted automatically.';
        roleAlert.className = 'alert alert-warning mt-4';
        document.getElementById('can_view_cost_price').checked = false;
        document.getElementById('can_edit_prices').checked = false;
        document.getElementById('can_give_discounts').checked = false;
        document.getElementById('max_discount_percent').value = '0.00';
        document.getElementById('max_discount_percent').disabled = true;
        // Reset HRM checkboxes
        const hrmMain = document.getElementById('can_view_hrm');
        if (hrmMain) {
            hrmMain.checked = false;
            const hrmSub = document.getElementById('hrmSubPermissions');
            if (hrmSub) { hrmSub.style.opacity = '0.5'; hrmSub.style.pointerEvents = 'none'; }
            document.querySelectorAll('.hrm-sub-perm').forEach(cb => cb.checked = false);
        }
    } else {
        if (permSection) permSection.style.display = 'block';
        roleAlert.innerHTML = `<i class="fas fa-info-circle"></i> <strong>${roleName} role selected:</strong> Customize permissions below.`;
        roleAlert.className = 'alert alert-info mt-4';
        
        // Uncheck all first
        uncheckAll();
        
        // Check default permissions if available for this role
        const role = this.value;
        if (roleDefaults[role]) {
            let perms = roleDefaults[role].permissions || [];
            perms.forEach(perm => {
                const checkbox = document.getElementById(perm);
                if (checkbox) checkbox.checked = true;
            });
            let discount = roleDefaults[role].max_discount_percent || '0.00';
            document.getElementById('max_discount_percent').value = discount;
            if (parseFloat(discount) > 0) {
                document.getElementById('max_discount_percent').disabled = false;
            }
        }
        updateDiscountInput();
        const hrmMain = document.getElementById('can_view_hrm');
        if (hrmMain) {
            toggleHrmSubPermissions(hrmMain);
        }
    }
});
// Enable/disable Max Discount % input based on Give Discounts checkbox
function updateDiscountInput() {
    const giveDiscounts = document.getElementById('can_give_discounts');
    const discountInput = document.getElementById('max_discount_percent');
    if (giveDiscounts.checked) {
        discountInput.disabled = false;
        if (!discountInput.value || discountInput.value === '0' || discountInput.value === '0.00') {
            discountInput.value = '10.00';
        }
    } else {
        discountInput.disabled = true;
        discountInput.value = '0.00';
    }
}

document.getElementById('can_give_discounts').addEventListener('change', updateDiscountInput);

window.addEventListener('DOMContentLoaded', function() {
    updateDiscountInput();
    
    // Hide permissions if administrator
    const roleSelect = document.getElementById('roleSelect');
    const permSection = document.getElementById('permissionsSection');
    if (roleSelect && roleSelect.value === 'administrator') {
        if (permSection) permSection.style.display = 'none';
        const roleAlert = document.getElementById('roleAlert');
        if(roleAlert) {
            roleAlert.innerHTML = '<i class="fas fa-crown text-warning"></i> <strong>Administrator selected:</strong> Full access granted automatically.';
            roleAlert.className = 'alert alert-warning mt-4';
        }
    }
    
    // Trigger HRM sub-permissions toggle if HRM is already checked
    const hrmMain = document.getElementById('can_view_hrm');
    if (hrmMain) {
        toggleHrmSubPermissions(hrmMain);
    }
});

// Form submission with confirmation
document.getElementById('editUserForm').addEventListener('submit', function(e) {
    e.preventDefault();
    
    Swal.fire({
        title: 'Update User?',
        text: 'Are you sure you want to update this user\'s information?',
        icon: 'question',
        showCancelButton: true,
        confirmButtonColor: '#f59e0b',
        cancelButtonColor: '#6c757d',
        confirmButtonText: '<i class="fas fa-check"></i> Yes, Update',
        cancelButtonText: '<i class="fas fa-times"></i> Cancel'
    }).then((result) => {
        if (result.isConfirmed) {
            Swal.fire({
                title: 'Updating User...',
                text: 'Please wait',
                allowOutsideClick: false,
                didOpen: () => {
                    Swal.showLoading();
                }
            });
            this.submit();
        }
    });
});

// Active status toggle label update
document.getElementById('isActive').addEventListener('change', function() {
    const label = this.nextElementSibling;
    if (this.checked) {
        label.innerHTML = '<strong>Active</strong>';
        label.classList.remove('text-danger');
        label.classList.add('text-success');
    } else {
        label.innerHTML = '<strong>Inactive</strong>';
        label.classList.remove('text-success');
        label.classList.add('text-danger');
    }
});
