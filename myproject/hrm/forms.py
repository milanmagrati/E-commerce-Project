from django import forms
from .models import Employee, EmployeeDocument, Branch, Department, Designation, DocumentType, Shift, AttendancePolicy


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            'full_name', 'employee_id', 'employee_code', 'email', 'phone',
            'date_of_birth', 'gender', 'profile_image',
            'branch', 'department', 'designation', 'date_of_joining',
            'employment_type', 'employee_status', 'shift', 'attendance_policy',
            'address_line_1', 'address_line_2', 'city', 'state', 'country', 'postal_code',
            'emergency_contact_name', 'emergency_contact_relationship', 'emergency_contact_phone',
            'bank_name', 'account_holder_name', 'account_number',
            'bank_identifier_code', 'bank_branch', 'tax_payer_id', 'base_salary', 'payment_qr_code',
        ]
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter full name'}),
            'employee_id': forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
            'employee_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter employee code'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Enter email'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter phone number'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'gender': forms.RadioSelect(attrs={'class': 'form-check-input'}),
            'profile_image': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'branch': forms.Select(attrs={'class': 'form-select'}),
            'department': forms.Select(attrs={'class': 'form-select'}),
            'designation': forms.Select(attrs={'class': 'form-select'}),
            'date_of_joining': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'employment_type': forms.Select(attrs={'class': 'form-select'}),
            'employee_status': forms.Select(attrs={'class': 'form-select'}),
            'shift': forms.Select(attrs={'class': 'form-select'}),
            'attendance_policy': forms.Select(attrs={'class': 'form-select'}),
            'address_line_1': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter address line 1'}),
            'address_line_2': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter address line 2'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter city'}),
            'state': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter state/province'}),
            'country': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter country'}),
            'postal_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter postal/zip code'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter name'}),
            'emergency_contact_relationship': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter relationship'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter phone number'}),
            'bank_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter bank name'}),
            'account_holder_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter account holder name'}),
            'account_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter account number'}),
            'bank_identifier_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter BIC/SWIFT code'}),
            'bank_branch': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter bank branch'}),
            'tax_payer_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter tax payer ID'}),
            'base_salary': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter base salary', 'step': '0.01'}),
            'payment_qr_code': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['branch'].queryset = Branch.objects.filter(status='active').order_by('name')
        self.fields['branch'].empty_label = 'Select Branch'
        self.fields['department'].queryset = Department.objects.none()
        self.fields['department'].empty_label = 'Select Branch First'
        self.fields['designation'].queryset = Designation.objects.none()
        self.fields['designation'].empty_label = 'Select Department First'

        # Shift and attendance_policy are ForeignKeys — widget already set in Meta.widgets
        # Set queryset LAST so choices bind to the widget correctly
        self.fields['shift'].required = False
        self.fields['shift'].empty_label = 'Select Shift (Optional)'
        self.fields['shift'].queryset = Shift.objects.filter(is_active=True).order_by('name')
        self.fields['attendance_policy'].required = False
        self.fields['attendance_policy'].empty_label = 'Select Attendance Policy (Optional)'
        self.fields['attendance_policy'].queryset = AttendancePolicy.objects.filter(is_active=True).order_by('name')
        self.fields['tax_payer_id'].required = False
        self.fields['address_line_2'].required = False
        self.fields['employee_code'].required = True
        self.fields['profile_image'].required = False

        # Contact Information - all optional
        for field in ['address_line_1', 'city', 'state', 'country', 'postal_code',
                      'emergency_contact_name', 'emergency_contact_relationship', 'emergency_contact_phone']:
            self.fields[field].required = False

        # Banking Information - all optional
        for field in ['bank_name', 'account_holder_name', 'account_number',
                      'bank_identifier_code', 'bank_branch', 'base_salary', 'payment_qr_code']:
            self.fields[field].required = False

        # If editing, populate department and designation based on current branch/department
        if self.instance and self.instance.pk:
            if self.instance.branch:
                self.fields['department'].queryset = Department.objects.filter(
                    branch=self.instance.branch, status='active'
                ).order_by('name')
                self.fields['department'].empty_label = 'Select Department'
            if self.instance.department:
                self.fields['designation'].queryset = Designation.objects.filter(
                    department=self.instance.department, status='active'
                ).order_by('name')
                self.fields['designation'].empty_label = 'Select Designation'

        # If form data has branch, populate departments
        if 'branch' in self.data:
            try:
                branch_id = int(self.data.get('branch'))
                self.fields['department'].queryset = Department.objects.filter(
                    branch_id=branch_id, status='active'
                ).order_by('name')
                self.fields['department'].empty_label = 'Select Department'
            except (ValueError, TypeError):
                pass

        if 'department' in self.data:
            try:
                department_id = int(self.data.get('department'))
                self.fields['designation'].queryset = Designation.objects.filter(
                    department_id=department_id, status='active'
                ).order_by('name')
                self.fields['designation'].empty_label = 'Select Designation'
            except (ValueError, TypeError):
                pass

        # Add is-invalid class to fields with errors for Bootstrap styling
        if self.errors:
            for field_name in self.errors:
                if field_name in self.fields and field_name != 'gender':
                    widget = self.fields[field_name].widget
                    css = widget.attrs.get('class', '')
                    if 'is-invalid' not in css:
                        widget.attrs['class'] = css + ' is-invalid'


class EmployeeDocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ['document_type', 'title', 'file']
        widgets = {
            'document_type': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Document title'}),
            'file': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document_type'].queryset = DocumentType.objects.filter(is_active=True).order_by('name')
        self.fields['document_type'].empty_label = 'Select Document Type'
        self.fields['document_type'].required = False
