from django.db import models
from django.conf import settings


class Branch(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    name = models.CharField(max_length=255)
    address = models.TextField(blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    country = models.CharField(max_length=100, blank=True, default='')
    zip_code = models.CharField(max_length=20, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Branches'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Department(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    name = models.CharField(max_length=255)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='departments')
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Designation(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    name = models.CharField(max_length=255)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='designations')
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class DocumentType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, default='')
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Employee(models.Model):
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    ]
    EMPLOYMENT_TYPE_CHOICES = [
        ('full-time', 'Full-time'),
        ('part-time', 'Part-time'),
        ('contract', 'Contract'),
        ('intern', 'Intern'),
        ('freelance', 'Freelance'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('on_leave', 'On Leave'),
        ('terminated', 'Terminated'),
        ('resigned', 'Resigned'),
    ]

    # Basic Information
    full_name = models.CharField(max_length=255)
    employee_id = models.CharField(max_length=20, unique=True)
    employee_code = models.CharField(max_length=50, blank=True, default='')
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20)
    password = models.CharField(max_length=128)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    profile_image = models.ImageField(upload_to='employee_images/', blank=True, null=True)

    # Employment Details
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, related_name='employees')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, related_name='employees')
    designation = models.ForeignKey(Designation, on_delete=models.SET_NULL, null=True, related_name='employees')
    date_of_joining = models.DateField()
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE_CHOICES, default='full-time')
    employee_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    shift = models.CharField(max_length=100, blank=True, default='')
    attendance_policy = models.CharField(max_length=100, blank=True, default='')

    # Contact Information
    address_line_1 = models.CharField(max_length=255)
    address_line_2 = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)

    # Emergency Contact
    emergency_contact_name = models.CharField(max_length=255)
    emergency_contact_relationship = models.CharField(max_length=100)
    emergency_contact_phone = models.CharField(max_length=20)

    # Banking Information
    bank_name = models.CharField(max_length=255)
    account_holder_name = models.CharField(max_length=255)
    account_number = models.CharField(max_length=50)
    bank_identifier_code = models.CharField(max_length=50)
    bank_branch = models.CharField(max_length=255)
    tax_payer_id = models.CharField(max_length=50, blank=True, default='')
    base_salary = models.DecimalField(max_digits=12, decimal_places=2)

    # User link (optional)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='employee_profile')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.full_name} ({self.employee_id})"

    @staticmethod
    def generate_employee_id():
        last = Employee.objects.order_by('-id').first()
        if last:
            # Extract numeric part from employee_id
            try:
                num = int(''.join(filter(str.isdigit, last.employee_id)))
                return f"EMP{num + 1:06d}"
            except ValueError:
                pass
        return "EMP000001"


class EmployeeDocument(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='documents')
    document_type = models.ForeignKey(DocumentType, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='employee_documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.title} - {self.employee.full_name}"


class AwardType(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Promotion(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='promotions')
    previous_designation = models.CharField(max_length=255)
    new_designation = models.ForeignKey(Designation, on_delete=models.SET_NULL, null=True, related_name='promotions')
    promotion_date = models.DateField()
    effective_date = models.DateField()
    salary_adjustment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reason = models.TextField(blank=True, default='')
    document = models.FileField(upload_to='promotion_documents/', blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.full_name} - {self.previous_designation} → {self.new_designation}"


class Award(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='awards')
    award_type = models.ForeignKey(AwardType, on_delete=models.CASCADE, related_name='awards')
    date = models.DateField()
    gift = models.CharField(max_length=255, blank=True, default='')
    monetary_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    description = models.TextField(blank=True, default='')
    certificate = models.FileField(upload_to='award_certificates/', blank=True, null=True)
    photo = models.ImageField(upload_to='award_photos/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.full_name} - {self.award_type.name}"


class Resignation(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('completed', 'Completed'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='resignations')
    resignation_date = models.DateField()
    last_working_day = models.DateField()
    notice_period = models.CharField(max_length=100, blank=True, default='')
    reason = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    document = models.FileField(upload_to='resignation_documents/', blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.full_name} - {self.resignation_date}"


class Termination(models.Model):
    TYPE_CHOICES = [
        ('involuntary', 'Involuntary'),
        ('voluntary', 'Voluntary'),
        ('retirement', 'Retirement'),
        ('contract_end', 'Contract End'),
        ('misconduct', 'Misconduct'),
        ('layoff', 'Layoff'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('revoked', 'Revoked'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='terminations')
    termination_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    termination_date = models.DateField()
    notice_date = models.DateField()
    notice_period = models.CharField(max_length=100, blank=True, default='')
    reason = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    document = models.FileField(upload_to='termination_documents/', blank=True, null=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.full_name} - {self.get_termination_type_display()} ({self.termination_date})"


class Warning(models.Model):
    TYPE_CHOICES = [
        ('performance', 'Performance'),
        ('attendance', 'Attendance'),
        ('conduct', 'Conduct'),
        ('policy_violation', 'Policy Violation'),
        ('safety', 'Safety'),
        ('other', 'Other'),
    ]
    SEVERITY_CHOICES = [
        ('verbal', 'Verbal'),
        ('written', 'Written'),
        ('final', 'Final'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('acknowledged', 'Acknowledged'),
        ('resolved', 'Resolved'),
        ('escalated', 'Escalated'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='warnings')
    warning_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='warnings_issued')
    warning_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    subject = models.CharField(max_length=255)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='verbal')
    warning_date = models.DateField()
    description = models.TextField(blank=True, default='')
    improvement_plan = models.BooleanField(default=False)
    document = models.FileField(upload_to='warning_documents/', blank=True, null=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.full_name} - {self.subject} ({self.warning_date})"


class Complaint(models.Model):
    TYPE_CHOICES = [
        ('harassment', 'Harassment'),
        ('discrimination', 'Discrimination'),
        ('workplace_conditions', 'Workplace Conditions'),
        ('management_issues', 'Management Issues'),
        ('policy_violation', 'Policy Violation'),
        ('safety', 'Safety'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('submitted', 'Submitted'),
        ('under_review', 'Under Review'),
        ('resolved', 'Resolved'),
        ('dismissed', 'Dismissed'),
    ]

    complainant = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='complaints_filed')
    against = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='complaints_against')
    complaint_type = models.CharField(max_length=25, choices=TYPE_CHOICES)
    subject = models.CharField(max_length=255)
    complaint_date = models.DateField()
    description = models.TextField(blank=True, default='')
    assigned_to = models.CharField(max_length=255, blank=True, default='')
    is_anonymous = models.BooleanField(default=False)
    document = models.FileField(upload_to='complaint_documents/', blank=True, null=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='submitted')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        name = 'Anonymous' if self.is_anonymous else self.complainant.full_name
        return f"{name} - {self.subject} ({self.complaint_date})"


class AssetType(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Asset Type'
        verbose_name_plural = 'Asset Types'

    def __str__(self):
        return self.name


class Asset(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('assigned', 'Assigned'),
        ('under_maintenance', 'Under Maintenance'),
        ('retired', 'Retired'),
        ('disposed', 'Disposed'),
    ]
    CONDITION_CHOICES = [
        ('new', 'New'),
        ('good', 'Good'),
        ('fair', 'Fair'),
        ('poor', 'Poor'),
        ('damaged', 'Damaged'),
    ]
    DEPRECIATION_METHOD_CHOICES = [
        ('straight_line', 'Straight Line'),
        ('declining_balance', 'Declining Balance'),
        ('sum_of_years', 'Sum of Years Digits'),
        ('units_of_production', 'Units of Production'),
        ('none', 'None'),
    ]

    name = models.CharField(max_length=255)
    asset_type = models.ForeignKey(AssetType, on_delete=models.CASCADE, related_name='assets')
    serial_number = models.CharField(max_length=100, blank=True, default='')
    asset_code = models.CharField(max_length=100, unique=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='new')
    description = models.TextField(blank=True, default='')
    location = models.CharField(max_length=255, blank=True, default='')
    assigned_to = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_assets'
    )
    supplier = models.CharField(max_length=255, blank=True, default='')
    warranty_info = models.CharField(max_length=255, blank=True, default='')
    warranty_expiry = models.DateField(null=True, blank=True)
    image = models.ImageField(upload_to='asset_images/', blank=True, null=True)
    document = models.FileField(upload_to='asset_documents/', blank=True, null=True)
    depreciation_method = models.CharField(
        max_length=25, choices=DEPRECIATION_METHOD_CHOICES, default='none'
    )
    useful_life_years = models.PositiveIntegerField(default=5)
    salvage_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Asset'
        verbose_name_plural = 'Assets'

    def __str__(self):
        return f"{self.name} ({self.asset_code})"
