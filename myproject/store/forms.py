import re

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

# Nepali mobile numbers are 10 digits starting 96/97/98 (NTC, Ncell, Smart).
# Shoppers routinely paste them as +977-98…, 977 98…, or with spaces/dashes,
# so the number is normalised before it is checked.
NEPALI_MOBILE_RE = re.compile(r'^9[678]\d{8}$')


def normalize_phone(raw):
    digits = re.sub(r'\D', '', raw or '')
    if digits.startswith('977') and len(digits) == 13:
        digits = digits[3:]
    elif digits.startswith('0') and len(digits) == 11:
        digits = digits[1:]
    return digits


class ReviewForm(forms.Form):
    """A review. Signing in is optional, so `guest_name` is only required of
    visitors who are not logged in — the view passes `name_required=False`
    when it already knows who is writing."""
    guest_name = forms.CharField(
        max_length=120, required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Your name'})
    )
    rating = forms.IntegerField(min_value=1, max_value=5)
    comment = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-input', 'placeholder': 'Share your thoughts on this product...', 'rows': 4}),
        required=False
    )

    def __init__(self, *args, name_required=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['guest_name'].required = name_required

    def clean_guest_name(self):
        return (self.cleaned_data.get('guest_name') or '').strip()


class GuestOrderForm(forms.Form):
    """The single form behind Confirm Order / Inquiry Only, on the product page
    and at cart checkout. Everything the courier needs, nothing more."""

    full_name = forms.CharField(
        max_length=200, label='Name',
        error_messages={'required': 'Please enter your name.'},
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'Name', 'autocomplete': 'name',
        })
    )
    phone = forms.CharField(
        max_length=20, label='Mobile number',
        error_messages={'required': 'Please enter your mobile number.'},
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'Mobile number',
            'inputmode': 'numeric', 'autocomplete': 'tel',
        })
    )
    email = forms.EmailField(
        required=False, label='Email',
        widget=forms.EmailInput(attrs={
            'class': 'of-input', 'placeholder': 'Email (optional)', 'autocomplete': 'email',
        })
    )
    district = forms.CharField(
        max_length=100, label='District',
        error_messages={'required': 'Please select your district.'},
        widget=forms.Select(attrs={'class': 'of-input of-select'})
    )
    courier_branch = forms.CharField(
        max_length=120, required=False, label='Courier branch',
        widget=forms.Select(attrs={'class': 'of-input of-select'})
    )
    courier_branch_code = forms.CharField(max_length=40, required=False, widget=forms.HiddenInput())
    address = forms.CharField(
        max_length=300, label='Address',
        error_messages={'required': 'Please enter your delivery address.'},
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'Address', 'autocomplete': 'street-address',
        })
    )
    note = forms.CharField(
        max_length=500, required=False, label='Note',
        widget=forms.TextInput(attrs={'class': 'of-input', 'placeholder': 'Note (optional)'})
    )
    discount_code = forms.CharField(
        max_length=40, required=False, label='Discount code',
        widget=forms.TextInput(attrs={'class': 'of-input', 'placeholder': 'Discount Code'})
    )

    def clean_full_name(self):
        name = (self.cleaned_data.get('full_name') or '').strip()
        if len(name) < 2:
            raise forms.ValidationError('Please enter your full name.')
        return name

    def clean_phone(self):
        digits = normalize_phone(self.cleaned_data.get('phone'))
        if not NEPALI_MOBILE_RE.match(digits):
            raise forms.ValidationError(
                'Enter a valid 10-digit Nepali mobile number starting with 98, 97 or 96.'
            )
        return digits

    def clean_district(self):
        return (self.cleaned_data.get('district') or '').strip()

    def clean_address(self):
        address = (self.cleaned_data.get('address') or '').strip()
        if len(address) < 4:
            raise forms.ValidationError('Please enter a delivery address we can find.')
        return address

    def clean_discount_code(self):
        return (self.cleaned_data.get('discount_code') or '').strip().upper()


class OrderTrackForm(forms.Form):
    """Replaces "My Orders" — guests find an order by number + phone."""
    order_number = forms.CharField(
        max_length=36, label='Order number',
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'e.g. 4F9C2A1B7D3E',
            'autocapitalize': 'characters',
        })
    )
    phone = forms.CharField(
        max_length=20, label='Mobile number',
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'Mobile number used on the order',
            'inputmode': 'numeric',
        })
    )

    def clean_order_number(self):
        return (self.cleaned_data.get('order_number') or '').strip().upper()

    def clean_phone(self):
        return normalize_phone(self.cleaned_data.get('phone'))


# ──────────────────── Customer accounts ────────────────────
# Signing in is optional storefront-wide; these back the /account/ pages only.

class CustomerRegisterForm(forms.Form):
    """Create a shopper account. Email and mobile both have to be unique
    because either one can be typed into the login box."""

    full_name = forms.CharField(
        max_length=200, label='Full name',
        error_messages={'required': 'Please enter your name.'},
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'Full name', 'autocomplete': 'name',
        })
    )
    email = forms.EmailField(
        label='Email',
        error_messages={'required': 'Please enter your email address.'},
        widget=forms.EmailInput(attrs={
            'class': 'of-input', 'placeholder': 'Email', 'autocomplete': 'email',
        })
    )
    phone = forms.CharField(
        max_length=20, label='Mobile number',
        error_messages={'required': 'Please enter your mobile number.'},
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'Mobile number',
            'inputmode': 'numeric', 'autocomplete': 'tel',
        })
    )
    password = forms.CharField(
        label='Password',
        error_messages={'required': 'Please choose a password.'},
        widget=forms.PasswordInput(attrs={
            'class': 'of-input', 'placeholder': 'Password', 'autocomplete': 'new-password',
        })
    )
    password_confirm = forms.CharField(
        label='Confirm password',
        error_messages={'required': 'Please retype your password.'},
        widget=forms.PasswordInput(attrs={
            'class': 'of-input', 'placeholder': 'Confirm password', 'autocomplete': 'new-password',
        })
    )

    def clean_full_name(self):
        name = (self.cleaned_data.get('full_name') or '').strip()
        if len(name) < 2:
            raise forms.ValidationError('Please enter your full name.')
        return name

    def clean_email(self):
        from .models import StoreCustomer
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if StoreCustomer.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account already uses this email. Log in instead.')
        return email

    def clean_phone(self):
        from .models import StoreCustomer
        digits = normalize_phone(self.cleaned_data.get('phone'))
        if not NEPALI_MOBILE_RE.match(digits):
            raise forms.ValidationError(
                'Enter a valid 10-digit Nepali mobile number starting with 98, 97 or 96.'
            )
        if StoreCustomer.objects.filter(phone=digits).exists():
            raise forms.ValidationError('An account already uses this number. Log in instead.')
        return digits

    def clean_password(self):
        password = self.cleaned_data.get('password') or ''
        try:
            # Reuse the project's configured AUTH_PASSWORD_VALIDATORS rather
            # than inventing a second, weaker rule for shoppers.
            validate_password(password)
        except DjangoValidationError as exc:
            raise forms.ValidationError(list(exc.messages))
        return password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        confirm = cleaned.get('password_confirm')
        if password and confirm and password != confirm:
            self.add_error('password_confirm', 'The two passwords do not match.')
        return cleaned


class CustomerLoginForm(forms.Form):
    """Log in with either the email or the mobile number on the account."""

    identifier = forms.CharField(
        max_length=200, label='Email or mobile number',
        error_messages={'required': 'Enter your email or mobile number.'},
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'Email or mobile number',
            'autocomplete': 'username', 'autocapitalize': 'off',
        })
    )
    password = forms.CharField(
        label='Password',
        error_messages={'required': 'Enter your password.'},
        widget=forms.PasswordInput(attrs={
            'class': 'of-input', 'placeholder': 'Password', 'autocomplete': 'current-password',
        })
    )

    def clean_identifier(self):
        return (self.cleaned_data.get('identifier') or '').strip()

    def get_customer(self):
        """The matching account, or None. Call only on a valid form.

        Deliberately returns None for both "no such account" and "wrong
        password" so the page cannot be used to test which numbers are
        registered.
        """
        from .models import StoreCustomer
        identifier = self.cleaned_data['identifier']
        digits = normalize_phone(identifier)
        lookup = (
            StoreCustomer.objects.filter(phone=digits).first() if NEPALI_MOBILE_RE.match(digits)
            else StoreCustomer.objects.filter(email__iexact=identifier).first()
        )
        if lookup and lookup.is_active and lookup.check_password(self.cleaned_data['password']):
            return lookup
        return None


class CustomerProfileForm(forms.Form):
    """Name, contact and the saved delivery details the order form prefills."""

    full_name = forms.CharField(
        max_length=200, label='Full name',
        widget=forms.TextInput(attrs={'class': 'of-input', 'autocomplete': 'name'})
    )
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'class': 'of-input', 'autocomplete': 'email'})
    )
    phone = forms.CharField(
        max_length=20, label='Mobile number',
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'inputmode': 'numeric', 'autocomplete': 'tel',
        })
    )
    district = forms.CharField(
        max_length=100, required=False, label='District',
        widget=forms.TextInput(attrs={'class': 'of-input', 'placeholder': 'District'})
    )
    address = forms.CharField(
        max_length=300, required=False, label='Address',
        widget=forms.TextInput(attrs={
            'class': 'of-input', 'placeholder': 'Address', 'autocomplete': 'street-address',
        })
    )

    def __init__(self, *args, customer=None, **kwargs):
        self.customer = customer
        super().__init__(*args, **kwargs)

    def clean_full_name(self):
        name = (self.cleaned_data.get('full_name') or '').strip()
        if len(name) < 2:
            raise forms.ValidationError('Please enter your full name.')
        return name

    def clean_email(self):
        from .models import StoreCustomer
        email = (self.cleaned_data.get('email') or '').strip().lower()
        clash = StoreCustomer.objects.filter(email__iexact=email)
        if self.customer:
            clash = clash.exclude(pk=self.customer.pk)
        if clash.exists():
            raise forms.ValidationError('Another account already uses this email.')
        return email

    def clean_phone(self):
        from .models import StoreCustomer
        digits = normalize_phone(self.cleaned_data.get('phone'))
        if not NEPALI_MOBILE_RE.match(digits):
            raise forms.ValidationError(
                'Enter a valid 10-digit Nepali mobile number starting with 98, 97 or 96.'
            )
        clash = StoreCustomer.objects.filter(phone=digits)
        if self.customer:
            clash = clash.exclude(pk=self.customer.pk)
        if clash.exists():
            raise forms.ValidationError('Another account already uses this number.')
        return digits


class PasswordChangeForm(forms.Form):
    """Change the password from the account page."""

    current_password = forms.CharField(
        label='Current password',
        widget=forms.PasswordInput(attrs={
            'class': 'of-input', 'autocomplete': 'current-password',
        })
    )
    new_password = forms.CharField(
        label='New password',
        widget=forms.PasswordInput(attrs={'class': 'of-input', 'autocomplete': 'new-password'})
    )
    new_password_confirm = forms.CharField(
        label='Confirm new password',
        widget=forms.PasswordInput(attrs={'class': 'of-input', 'autocomplete': 'new-password'})
    )

    def __init__(self, *args, customer=None, **kwargs):
        self.customer = customer
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        password = self.cleaned_data.get('current_password') or ''
        if not self.customer or not self.customer.check_password(password):
            raise forms.ValidationError('That is not your current password.')
        return password

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password') or ''
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise forms.ValidationError(list(exc.messages))
        return password

    def clean(self):
        cleaned = super().clean()
        new = cleaned.get('new_password')
        confirm = cleaned.get('new_password_confirm')
        if new and confirm and new != confirm:
            self.add_error('new_password_confirm', 'The two passwords do not match.')
        return cleaned
