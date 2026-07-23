from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal
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

    def clean(self):
        super().clean()
        if self.pay_period_start and self.pay_period_end:
            if self.pay_period_end < self.pay_period_start:
                from django.core.exceptions import ValidationError
                raise ValidationError({'pay_period_end': 'End date must be after or equal to start date.'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

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
    password = models.CharField(max_length=128, blank=True, default='')
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
    shift = models.ForeignKey('Shift', on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    attendance_policy = models.ForeignKey('AttendancePolicy', on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')

    # Contact Information
    address_line_1 = models.CharField(max_length=255, blank=True, default='')
    address_line_2 = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    country = models.CharField(max_length=100, blank=True, default='')
    postal_code = models.CharField(max_length=20, blank=True, default='')

    # Emergency Contact
    emergency_contact_name = models.CharField(max_length=255, blank=True, default='')
    emergency_contact_relationship = models.CharField(max_length=100, blank=True, default='')
    emergency_contact_phone = models.CharField(max_length=20, blank=True, default='')

    # Banking Information
    bank_name = models.CharField(max_length=255, blank=True, default='')
    account_holder_name = models.CharField(max_length=255, blank=True, default='')
    account_number = models.CharField(max_length=50, blank=True, default='')
    bank_identifier_code = models.CharField(max_length=50, blank=True, default='')
    bank_branch = models.CharField(max_length=255, blank=True, default='')
    tax_payer_id = models.CharField(max_length=50, blank=True, default='')
    base_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payment_qr_code = models.ImageField(upload_to='payment_qr/', blank=True, null=True)

    # User link (optional)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='employee_profile')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.full_name} ({self.employee_id})"

    @property
    def effective_attendance_policy(self):
        """The employee's own attendance policy, falling back to the active
        company-wide policy when none is explicitly assigned."""
        if self.attendance_policy_id:
            return self.attendance_policy
        return AttendancePolicy.objects.filter(is_active=True).order_by('id').first()

    @staticmethod
    def generate_employee_id():
        last = Employee.objects.order_by('-id').first()
        if last:
            # Extract numeric part from employee_id
            try:
                num = int(''.join(filter(str.isdigit, last.employee_id)))
                return f"EMP{num + 1:03d}"
            except ValueError:
                pass
        return "EMP001"


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


# ==================== Attendance Management ====================

class Shift(models.Model):
    SHIFT_TYPE_CHOICES = [
        ('day', 'Day Shift'),
        ('night', 'Night Shift'),
    ]

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default='')
    start_time = models.TimeField()
    end_time = models.TimeField(null=True, blank=True)
    break_duration = models.PositiveIntegerField(default=60, help_text='Break duration in minutes')
    break_start_time = models.TimeField(null=True, blank=True)
    break_end_time = models.TimeField(null=True, blank=True)
    grace_period = models.PositiveIntegerField(default=15, help_text='Grace period in minutes')
    is_night_shift = models.BooleanField(default=False)
    working_hours = models.DecimalField(max_digits=4, decimal_places=1, default=8.0, validators=[MinValueValidator(0), MaxValueValidator(24)])
    half_day_hours = models.DecimalField(
        max_digits=4, decimal_places=2, default=4.0,
        help_text='Days worked at or below this many hours on this shift are marked Half Day'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Shift'
        verbose_name_plural = 'Shifts'

    def __str__(self):
        return self.name

    @property
    def shift_type(self):
        return 'Night Shift' if self.is_night_shift else 'Day Shift'


class AttendancePolicy(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default='')
    work_hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, default=8.0)
    late_mark_after = models.PositiveIntegerField(default=15, help_text='Minutes after shift start to mark as late')
    early_departure_grace = models.PositiveIntegerField(default=15, help_text='Minutes before shift end allowed to leave early')
    overtime_rate = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, help_text='Overtime rate per hour')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Attendance Policy'
        verbose_name_plural = 'Attendance Policies'

    def __str__(self):
        return self.name


class AttendanceRecord(models.Model):
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('late', 'Late'),
        ('half_day', 'Half Day'),
        ('on_leave', 'On Leave'),
        ('incomplete', 'Incomplete Punch'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='attendance_records'
    )
    date = models.DateField()
    clock_in = models.TimeField(null=True, blank=True)
    clock_out = models.TimeField(null=True, blank=True)
    shift = models.ForeignKey(
        'Shift', on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_records'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='present')
    working_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(24)])
    overtime_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(24)])
    is_holiday = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default='')
    is_early_departure = models.BooleanField(default=False)
    is_late_arrival = models.BooleanField(default=False)
    is_regularized = models.BooleanField(
        default=False,
        help_text='Set when clock in/out was manually corrected (regularization approval or '
                   'Fix Attendance). The biometric auto-sync will not overwrite these fields.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = 'Attendance Record'
        verbose_name_plural = 'Attendance Records'
        unique_together = ['employee', 'date']

    def __str__(self):
        return f"{self.employee.full_name} - {self.date}"

    @property
    def is_incomplete_punch(self):
        """True when clock-in or clock-out is missing but was expected —
        excludes Absent/On Leave days, where no punch is normal, not an error."""
        if self.status in ('absent', 'on_leave'):
            return False
        return not (self.clock_in and self.clock_out)


class AttendanceRegularization(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='regularizations'
    )
    attendance_record = models.ForeignKey(
        'AttendanceRecord', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='regularizations'
    )
    date = models.DateField()
    clock_in = models.TimeField(null=True, blank=True, help_text='Requested clock in')
    clock_out = models.TimeField(null=True, blank=True, help_text='Requested clock out')
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_regularizations'
    )
    is_draft = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Attendance Regularization'
        verbose_name_plural = 'Attendance Regularizations'

    def __str__(self):
        return f"{self.employee} - {self.date}"


# ==================== Employee Weekend / Holiday Assignment ====================

class EmployeeWeekend(models.Model):
    WEEKEND_TYPE_CHOICES = [
        ('weekend', 'Weekend'),
        ('holiday', 'Holiday'),
    ]
    DAY_CHOICES = [
        ('monday',    'Monday'),
        ('tuesday',   'Tuesday'),
        ('wednesday', 'Wednesday'),
        ('thursday',  'Thursday'),
        ('friday',    'Friday'),
        ('saturday',  'Saturday'),
        ('sunday',    'Sunday'),
    ]

    employee       = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='weekend_assignments')
    weekend_type   = models.CharField(max_length=10, choices=WEEKEND_TYPE_CHOICES, default='weekend')
    weekend_days   = models.JSONField(default=list, help_text='List of day names e.g. ["saturday","sunday"]')
    effective_from = models.DateField()
    effective_to   = models.DateField(null=True, blank=True)
    notes          = models.TextField(blank=True, default='')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Employee Weekend'
        verbose_name_plural = 'Employee Weekends'

    def __str__(self):
        days = ', '.join(d.capitalize() for d in (self.weekend_days or []))
        return f"{self.employee.full_name} — {days}"

    @property
    def days_display(self):
        return [d.capitalize() for d in (self.weekend_days or [])]


# ==================== ZKTeco ADMS Device ====================

class ZKDevice(models.Model):
    serial_number = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150, blank=True, default='')
    model_name = models.CharField(max_length=100, blank=True, default='')
    branch = models.CharField(max_length=150, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    face_count = models.PositiveIntegerField(default=0)
    fingerprint_count = models.PositiveIntegerField(default=0)
    transaction_count = models.PositiveIntegerField(default=0)
    user_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_seen']
        verbose_name = 'ZK Device'
        verbose_name_plural = 'ZK Devices'

    def __str__(self):
        return f"{self.name or self.serial_number} ({self.ip_address or 'Unknown IP'})"


# ==================== Biometric Attendance (Raw Punch Log) ====================

class BiometricAttendance(models.Model):
    STATUS_CHOICES = [
        (0, 'Check-In'),
        (1, 'Check-Out'),
        (2, 'Break-Out'),
        (3, 'Break-In'),
        (4, 'OT-In'),
        (5, 'OT-Out'),
    ]

    device = models.ForeignKey(ZKDevice, on_delete=models.SET_NULL, null=True, blank=True, related_name='punches')
    pin = models.CharField(max_length=50, help_text='Employee code from device')
    timestamp = models.DateTimeField()
    status = models.IntegerField(choices=STATUS_CHOICES, default=0)
    verify_mode = models.IntegerField(default=0)
    raw_log = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        unique_together = ['pin', 'timestamp']
        verbose_name = 'Biometric Attendance'
        verbose_name_plural = 'Biometric Attendances'

    def __str__(self):
        return f"PIN {self.pin} @ {self.timestamp}"


# ==================== Holiday Management ====================

class Holiday(models.Model):
    HOLIDAY_TYPE_CHOICES = [
        ('public', 'Public Holiday'),
        ('national', 'National Holiday'),
        ('religious', 'Religious Holiday'),
        ('company', 'Company Holiday'),
        ('other', 'Other'),
    ]

    name = models.CharField(max_length=200)
    holiday_type = models.CharField(max_length=20, choices=HOLIDAY_TYPE_CHOICES, default='public')
    start_date = models.DateField()
    end_date = models.DateField()
    description = models.TextField(blank=True, default='')
    apply_for_all = models.BooleanField(default=True, help_text='Auto-apply this holiday to all active employees')
    is_paid = models.BooleanField(default=True, help_text='Whether this holiday is a paid day')
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_holidays'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Holiday'
        verbose_name_plural = 'Holidays'

    def __str__(self):
        return f"{self.name} ({self.start_date} to {self.end_date})"

    @property
    def total_days(self):
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 0


# ==================== Payroll Models ====================

class SalaryComponent(models.Model):
    TYPE_CHOICES = [
        ('earning', 'Earning'),
        ('deduction', 'Deduction'),
    ]
    CALCULATION_TYPE_CHOICES = [
        ('fixed', 'Fixed Amount'),
        ('variable', 'Variable (Daily Pro-rata)'),
        ('percentage_of_basic', '% of Basic Salary'),
        ('percentage_of_gross', '% of Gross Salary'),
        ('percentage_of_ctc', '% of CTC'),
    ]
    name = models.CharField(max_length=255)
    component_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='earning')
    description = models.TextField(blank=True, default='')
    calculation_type = models.CharField(max_length=30, choices=CALCULATION_TYPE_CHOICES, default='fixed')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_taxable = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Salary Component'
        verbose_name_plural = 'Salary Components'

    def __str__(self):
        return f"{self.name} ({self.get_component_type_display()})"


class EmployeeSalary(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='salaries')
    effective_date = models.DateField()
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    components = models.ManyToManyField(SalaryComponent, blank=True, related_name='employee_salaries')
    pay_ot = models.BooleanField(default=False, help_text='Include overtime pay in payroll calculations')
    sandwich_rule = models.BooleanField(default=False, help_text='Apply sandwich rule: weekends/holidays between absences become unpaid')
    notes = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_date']
        verbose_name = 'Employee Salary'
        verbose_name_plural = 'Employee Salaries'

    def __str__(self):
        return f"{self.employee} - Rs. {self.basic_salary} (from {self.effective_date})"


class PayrollRun(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    FREQUENCY_CHOICES = [
        ('monthly', 'Monthly'),
        ('bi_weekly', 'Bi-Weekly'),
        ('weekly', 'Weekly'),
    ]
    title = models.CharField(max_length=200, default='')
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default='monthly')
    pay_period_start = models.DateField(null=True, blank=True)
    pay_period_end = models.DateField(null=True, blank=True)
    pay_date = models.DateField(null=True, blank=True)
    month = models.PositiveSmallIntegerField(blank=True, null=True)
    year = models.PositiveSmallIntegerField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    employee_count = models.PositiveIntegerField(default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gross_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='payroll_runs'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-pay_date', '-created_at']
        verbose_name = 'Payroll Run'
        verbose_name_plural = 'Payroll Runs'

    def __str__(self):
        return f"{self.title} - {self.get_status_display()}"


class PayrollSetting(models.Model):
    """
    Singleton configuration table for payroll calculation behaviour.
    Access via PayrollSetting.get_settings() — never instantiate directly.
    """
    DIVISOR_CHOICES = [
        ('FIXED_30', 'Fixed 30 days (always divide by 30)'),
        ('ACTUAL_CYCLE_DAYS', 'Actual cycle days (calendar days in pay period)'),
    ]
    salary_divisor_type = models.CharField(
        max_length=20, choices=DIVISOR_CHOICES, default='FIXED_30',
        help_text='Denominator used for DailyRate = MonthlySalary / Divisor'
    )
    weekend_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=1.00,
        help_text='Pay multiplier for days worked on weekends (1=regular, 1.5=time-and-half, 2=double)'
    )
    holiday_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=1.00,
        help_text='Pay multiplier for days worked on public holidays'
    )
    ot_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=1.50,
        validators=[MinValueValidator(Decimal('1.50'))],
        help_text='OT pay multiplier per hour (Nepal Labor Act 2074 default: 1.5x)'
    )
    shift_hours_per_day = models.DecimalField(
        max_digits=4, decimal_places=2, default=8.00,
        help_text='Standard working hours per day — used to derive HourlyRate from DailyRate'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Payroll Setting'
        verbose_name_plural = 'Payroll Settings'

    def __str__(self):
        return f'Payroll Settings (Divisor: {self.get_salary_divisor_type_display()})'

    @classmethod
    def get_settings(cls):
        """Return the singleton settings row, creating it with defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Payslip(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('generated', 'Generated'),
        ('downloaded', 'Downloaded'),
        ('paid', 'Paid'),
    ]
    payslip_number = models.CharField(max_length=50, unique=True, blank=True)
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='payslips')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='payslips')
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Snapshot of basic salary at generation')
    salary_structure = models.JSONField(default=dict, blank=True, help_text='Snapshot of earnings/deductions used in calculation')
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    advance_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Advance payment deduction for this pay period')
    absent_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Absent-based salary deduction for this pay period')
    net_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    paid_date = models.DateField(null=True, blank=True)
    generated_on = models.DateField(null=True, blank=True)
    # Manual adjustment / finalization
    notes = models.TextField(blank=True, null=True, help_text='Notes and internal calculation data')
    is_finalized = models.BooleanField(default=False, help_text='Finalized slips are protected from auto-regeneration')
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='finalized_payslips'
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['payroll_run', 'employee']
        verbose_name = 'Payslip'
        verbose_name_plural = 'Payslips'

    def save(self, *args, **kwargs):
        if not self.payslip_number:
            import datetime
            now = datetime.date.today()
            prefix = f"PS-{now.strftime('%Y%m')}"
            last = Payslip.objects.filter(payslip_number__startswith=prefix).order_by('-payslip_number').first()
            if last:
                try:
                    seq = int(last.payslip_number.split('-')[-1]) + 1
                except (ValueError, IndexError):
                    seq = 1
            else:
                seq = 1
            self.payslip_number = f"{prefix}-{seq:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.payslip_number} - {self.employee}"


# ==================== Leave Management ====================

class LeaveType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    max_days_per_year = models.PositiveIntegerField(default=0, help_text='0 = unlimited')
    color = models.CharField(max_length=7, default='#22c55e', help_text='Hex color code for the leave type')
    is_paid = models.BooleanField(default=True, help_text='Whether this leave type is paid or unpaid')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Leave Type'
        verbose_name_plural = 'Leave Types'

    def __str__(self):
        return self.name


class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='leave_requests'
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests'
    )
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.PositiveIntegerField(default=1)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    attachment = models.FileField(upload_to='leave_attachments/', blank=True, null=True)
    approved_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_leave_requests'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Leave Request'
        verbose_name_plural = 'Leave Requests'

    def __str__(self):
        leave_name = self.leave_type.name if self.leave_type else 'N/A'
        return f"{self.employee.full_name} - {leave_name} ({self.start_date} to {self.end_date})"

    def save(self, *args, **kwargs):
        if self.start_date and self.end_date:
            delta = (self.end_date - self.start_date).days + 1
            self.days = max(delta, 1)
        super().save(*args, **kwargs)


class LeaveBalance(models.Model):
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='leave_balances'
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.CASCADE, related_name='balances'
    )
    year = models.PositiveIntegerField(default=2024)
    allocated_days = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    used_days = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    carry_forward_days = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-year', 'employee__full_name']
        unique_together = ('employee', 'leave_type', 'year')
        verbose_name = 'Leave Balance'
        verbose_name_plural = 'Leave Balances'

    def __str__(self):
        return f"{self.employee.full_name} - {self.leave_type.name} ({self.year})"

    @property
    def remaining_days(self):
        return max(self.allocated_days + self.carry_forward_days - self.used_days, 0)


class LeavePolicy(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    leave_types = models.ManyToManyField(LeaveType, blank=True, related_name='policies')
    carry_forward = models.BooleanField(default=False, help_text='Allow carry forward of unused leaves')
    max_carry_forward_days = models.PositiveIntegerField(default=0)
    min_days_per_application = models.PositiveIntegerField(default=1, help_text='Minimum days allowed per leave application')
    max_days_per_application = models.PositiveIntegerField(default=14, help_text='Maximum days allowed per leave application')
    requires_approval = models.BooleanField(default=True, help_text='Whether leave applications under this policy require approval')
    encashment_allowed = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Leave Policy'
        verbose_name_plural = 'Leave Policies'

    def __str__(self):
        return self.name


class AdvancePayment(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('disbursed', 'Disbursed'),
        ('repaying', 'Repaying'),
        ('cleared', 'Cleared'),
    ]
    REPAYMENT_MODE_CHOICES = [
        ('lump_sum', 'Lump Sum'),
        ('salary_deduction', 'Salary Deduction'),
        ('installments', 'Installments'),
    ]

    advance_number = models.CharField(max_length=30, unique=True, blank=True)
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='advance_payments'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    payment_date = models.DateField(null=True, blank=True)
    reason = models.TextField()
    repayment_mode = models.CharField(
        max_length=20, choices=REPAYMENT_MODE_CHOICES, default='salary_deduction'
    )
    repayment_start_date = models.DateField(null=True, blank=True)
    installment_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    total_installments = models.PositiveIntegerField(null=True, blank=True)
    paid_installments = models.PositiveIntegerField(default=0)
    amount_repaid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_advance_payments'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_advance_payments'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Advance Payment'
        verbose_name_plural = 'Advance Payments'

    def __str__(self):
        return f"{self.advance_number} - {self.employee.full_name} (Rs. {self.amount})"

    @property
    def remaining_amount(self):
        return max(self.amount - self.amount_repaid, 0)

    def save(self, *args, **kwargs):
        if not self.advance_number:
            from django.utils import timezone as tz
            last = AdvancePayment.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.advance_number = f"ADV-{tz.now().year}-{next_id:04d}"
        super().save(*args, **kwargs)


# ==================== Bonus Management ====================

class Bonus(models.Model):
    BONUS_TYPE_CHOICES = [
        ('performance', 'Performance Bonus'),
        ('festival', 'Festival Bonus'),
        ('monthly', 'Monthly Bonus'),
        ('incentive', 'Incentive'),
        ('target', 'Target Bonus'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('paid', 'Paid'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='bonuses')
    bonus_type = models.CharField(max_length=20, choices=BONUS_TYPE_CHOICES, default='other')
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    month = models.PositiveSmallIntegerField(help_text='Payroll month (1-12)')
    year = models.PositiveSmallIntegerField(help_text='Payroll year')
    remarks = models.TextField(blank=True, default='')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    apply_for_all = models.BooleanField(default=False, help_text='Apply this bonus to all active employees')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_bonuses'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_bonuses'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-year', '-month', 'employee__full_name']
        verbose_name = 'Bonus'
        verbose_name_plural = 'Bonuses'

    def __str__(self):
        import calendar
        month_name = calendar.month_name[self.month] if 1 <= self.month <= 12 else str(self.month)
        return f"{self.employee.full_name} — {self.get_bonus_type_display()} ({month_name} {self.year}) Rs.{self.amount}"


# ==================== Payslip Manual Adjustments ====================

class PayslipAdjustment(models.Model):
    ADJUSTMENT_TYPE_CHOICES = [
        ('earning', 'Earning'),
        ('deduction', 'Deduction'),
    ]
    CATEGORY_CHOICES = [
        ('bonus', 'Bonus'),
        ('incentive', 'Incentive'),
        ('arrears', 'Arrears'),
        ('penalty', 'Penalty'),
        ('adjustment', 'Adjustment'),
        ('other', 'Other'),
    ]

    payslip = models.ForeignKey('Payslip', on_delete=models.CASCADE, related_name='adjustments')
    adjustment_type = models.CharField(max_length=15, choices=ADJUSTMENT_TYPE_CHOICES)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='adjustment')
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payslip_adjustments'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['adjustment_type', 'created_at']
        verbose_name = 'Payslip Adjustment'
        verbose_name_plural = 'Payslip Adjustments'

    def __str__(self):
        return f"{self.get_adjustment_type_display()} — {self.description} (Rs.{self.amount})"


class PayslipAuditLog(models.Model):
    payslip = models.ForeignKey('Payslip', on_delete=models.CASCADE, related_name='audit_logs')
    action = models.CharField(max_length=150)
    detail = models.TextField(blank=True, default='')
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payslip_audit_logs'
    )
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-performed_at']
        verbose_name = 'Payslip Audit Log'
        verbose_name_plural = 'Payslip Audit Logs'

    def __str__(self):
        return f"{self.payslip} — {self.action}"



class HRMAuditLog(models.Model):
    model_name = models.CharField(max_length=50)
    record_id = models.PositiveIntegerField()
    action = models.CharField(max_length=50)
    changes = models.JSONField(default=dict, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-performed_at']
        verbose_name = 'HRM Audit Log'
        verbose_name_plural = 'HRM Audit Logs'

    def __str__(self):
        return f"{self.model_name} {self.record_id} - {self.action}"

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum

@receiver(post_save, sender=LeaveRequest)
@receiver(post_delete, sender=LeaveRequest)
def update_leave_balance_used_days(sender, instance, **kwargs):
    if not instance.employee:
        return
    
    # Also ensure a balance exists for the current request's year and leave type
    if instance.leave_type and instance.start_date:
        allocated = instance.leave_type.max_days_per_year if instance.leave_type.max_days_per_year and instance.leave_type.max_days_per_year > 0 else 0
        LeaveBalance.objects.get_or_create(
            employee=instance.employee,
            leave_type=instance.leave_type,
            year=instance.start_date.year,
            defaults={'allocated_days': allocated, 'used_days': 0, 'carry_forward_days': 0}
        )

    # Recalculate for all balances of this employee to handle changes in year/type
    from django.utils import timezone
    for balance in LeaveBalance.objects.filter(employee=instance.employee):
        used = LeaveRequest.objects.filter(
            employee=balance.employee,
            leave_type=balance.leave_type,
            start_date__year=balance.year,
            start_date__lte=timezone.now().date(),
            status__in=['approved', 'pending']
        ).aggregate(total=Sum('days'))['total'] or 0
        
        if balance.used_days != used:
            balance.used_days = used
            balance.save()

@receiver(post_save, sender=AttendanceRecord)
@receiver(post_delete, sender=AttendanceRecord)
@receiver(post_save, sender=EmployeeSalary)
@receiver(post_delete, sender=EmployeeSalary)
def log_hrm_changes(sender, instance, created=False, **kwargs):
    action = 'deleted'
    changes = {}
    if kwargs.get('signal') == post_save:
        action = 'created' if created else 'updated'
        # Basic serialization of fields could be done here, but we just log the action for now.
        changes = {'info': f"{sender.__name__} {action}"}
    
    HRMAuditLog.objects.create(
        model_name=sender.__name__,
        record_id=instance.pk or 0,
        action=action,
        changes=changes
    )
