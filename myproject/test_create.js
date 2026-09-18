
// Password validation
const passwordInput = document.getElementById('password');
const togglePassword = document.getElementById('togglePassword');
const strengthBar = document.getElementById('strengthBar');
const strengthText = document.getElementById('strengthText');
const passwordRequirements = document.getElementById('passwordRequirements');
const submitBtn = document.getElementById('submitBtn');

// Show/Hide password requirements on focus
passwordInput.addEventListener('focus', function() {
    passwordRequirements.style.display = 'block';
    document.querySelector('.password-strength-bar').style.display = 'block';
});

// Toggle password visibility
togglePassword.addEventListener('click', function() {
    const type = passwordInput.type === 'password' ? 'text' : 'password';
    passwordInput.type = type;
    this.querySelector('i').classList.toggle('fa-eye');
    this.querySelector('i').classList.toggle('fa-eye-slash');
});

// Real-time password validation
passwordInput.addEventListener('input', function() {
    const password = this.value;
    let strength = 0;
    let metRequirements = 0;

    // Check each requirement
    const hasLength = password.length >= 8;
    const hasUppercase = /[A-Z]/.test(password);
    const hasLowercase = /[a-z]/.test(password);
    const hasNumber = /[0-9]/.test(password);
    const hasSpecial = /[!@#$%^&*(),.?":{}|<>]/.test(password);

    // Update requirement indicators
    updateRequirement('req-length', hasLength);
    updateRequirement('req-uppercase', hasUppercase);
    updateRequirement('req-lowercase', hasLowercase);
    updateRequirement('req-number', hasNumber);
    updateRequirement('req-special', hasSpecial);

    // Calculate strength
    if (hasLength) metRequirements++;
    if (hasUppercase) metRequirements++;
    if (hasLowercase) metRequirements++;
    if (hasNumber) metRequirements++;
    if (hasSpecial) metRequirements++;

    // Update strength bar
    strengthBar.className = 'strength-bar';
    if (metRequirements <= 2) {
        strengthBar.classList.add('strength-weak');
        strengthText.textContent = 'Weak password';
        strengthText.style.color = '#dc3545';
    } else if (metRequirements <= 4) {
        strengthBar.classList.add('strength-medium');
        strengthText.textContent = 'Medium strength';
        strengthText.style.color = '#ffc107';
    } else {
        strengthBar.classList.add('strength-strong');
        strengthText.textContent = 'Strong password ✓';
        strengthText.style.color = '#28a745';
    }
});

function updateRequirement(id, met) {
    const element = document.getElementById(id);
    if (met) {
        element.classList.add('req-met');
        element.querySelector('i').className = 'fas fa-check-circle';
    } else {
        element.classList.remove('req-met');
        element.querySelector('i').className = 'fas fa-circle text-muted';
    }
}

// Form validation before submit
document.getElementById('userCreateForm').addEventListener('submit', function(e) {
    e.preventDefault();

    const password = passwordInput.value;
    const username = document.getElementById('username').value;
    const email = document.getElementById('email').value;
    const role = document.getElementById('roleSelect').value;

    // Validate username
    if (username.length < 3) {
        Swal.fire({
            icon: 'error',
            title: 'Invalid Username',
            text: 'Username must be at least 3 characters long',
            confirmButtonColor: '#667eea'
        });
        return false;
    }

    // Validate email
    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailPattern.test(email)) {
        Swal.fire({
            icon: 'error',
            title: 'Invalid Email',
            text: 'Please enter a valid email address',
            confirmButtonColor: '#667eea'
        });
        return false;
    }

    // Validate password
    const hasLength = password.length >= 8;
    const hasUppercase = /[A-Z]/.test(password);
    const hasLowercase = /[a-z]/.test(password);
    const hasNumber = /[0-9]/.test(password);
    const hasSpecial = /[!@#$%^&*(),.?":{}|<>]/.test(password);

    if (!hasLength || !hasUppercase || !hasLowercase || !hasNumber || !hasSpecial) {
        let errorMessage = '<div style="text-align: left;"><strong>Password must meet these requirements:</strong><ul style="margin-top: 10px;">';
        if (!hasLength) errorMessage += '<li>At least 8 characters long</li>';
        if (!hasUppercase) errorMessage += '<li>Contains uppercase letter (A-Z)</li>';
        if (!hasLowercase) errorMessage += '<li>Contains lowercase letter (a-z)</li>';
        if (!hasNumber) errorMessage += '<li>Contains number (0-9)</li>';
        if (!hasSpecial) errorMessage += '<li>Contains special character (!@#$%^&*)</li>';
        errorMessage += '</ul></div>';

        Swal.fire({
            icon: 'error',
            title: 'Weak Password',
            html: errorMessage,
            confirmButtonColor: '#667eea',
            width: '500px'
        });
        return false;
    }

    // Validate role selection
    if (!role) {
        Swal.fire({
            icon: 'warning',
            title: 'Role Required',
            text: 'Please select a role for this user',
            confirmButtonColor: '#667eea'
        });
        return false;
    }

    // All validations passed - submit via AJAX to preserve form data on errors
    Swal.fire({
        title: 'Creating User...',
        text: 'Please wait',
        allowOutsideClick: false,
        didOpen: () => {
            Swal.showLoading();
        }
    });

    const formData = new FormData(this);

    fetch(window.location.href, {
        method: 'POST',
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
        },
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            Swal.fire({
                icon: 'success',
                title: 'User Created!',
                text: 'User has been created successfully.',
                confirmButtonColor: '#667eea',
                timer: 1500,
                showConfirmButton: false
            }).then(() => {
                window.location.href = data.redirect || '{% url "user_list" %}';
            });
        } else {
            Swal.fire({
                icon: 'error',
                title: 'Error',
                text: data.error || 'Something went wrong. Please try again.',
                confirmButtonColor: '#667eea'
            });
        }
    })
    .catch(error => {
        console.error('Error:', error);
        Swal.fire({
            icon: 'error',
            title: 'Network Error',
            text: 'Could not connect to the server. Please try again.',
            confirmButtonColor: '#667eea'
        });
    });
});

// Role permissions configuration
const roleDefaults = {};

// Role selection handler
document.getElementById('roleSelect').addEventListener('change', function() {
    const role = this.value;
    const permSection = document.getElementById('permissionsSection');
    const roleInfo = document.getElementById('roleInfo');
    const roleTitle = document.getElementById('roleTitle');
    const roleDesc = document.getElementById('roleDesc');
    const selectedOption = this.options[this.selectedIndex];
    const description = selectedOption.getAttribute('data-description') || '';

    if (role === 'administrator') {
        permSection.style.display = 'none';
        roleInfo.classList.remove('d-none');
        roleTitle.textContent = 'Administrator - Full Access';
        roleDesc.textContent = 'Administrators have all permissions by default. No customization needed.';
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
    } else if (role) {
        permSection.style.display = 'block';
        roleInfo.classList.remove('d-none');
        roleTitle.textContent = selectedOption.textContent + ' Role Selected';
        roleDesc.textContent = description || 'Customize permissions below as needed.';

        // Uncheck all first
        uncheckAllPermissions();

        // Check default permissions if available for this role
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
    if (giveDiscounts && discountInput) {
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
}

document.getElementById('can_give_discounts').addEventListener('change', updateDiscountInput);

window.addEventListener('DOMContentLoaded', function() {
    updateDiscountInput();
    
    // Trigger change event to populate role defaults if a role is already selected
    const roleSelect = document.getElementById('roleSelect');
    if (roleSelect && roleSelect.value) {
        roleSelect.dispatchEvent(new Event('change'));
    }
});

// Check all permissions
function checkAllPermissions() {
    document.querySelectorAll('#permissionsSection input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
    });
    document.getElementById('max_discount_percent').value = '100.00';
    const hrmSub = document.getElementById('hrmSubPermissions');
    if (hrmSub) { hrmSub.style.opacity = '1'; hrmSub.style.pointerEvents = 'auto'; }
}

// Uncheck all permissions
function uncheckAllPermissions() {
    document.querySelectorAll('#permissionsSection input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });
    document.getElementById('max_discount_percent').value = '0.00';
    const hrmSub = document.getElementById('hrmSubPermissions');
    if (hrmSub) { hrmSub.style.opacity = '0.5'; hrmSub.style.pointerEvents = 'none'; }
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
