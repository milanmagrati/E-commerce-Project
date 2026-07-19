from django.contrib import admin
from .models import Branch, Department, Designation, Employee, EmployeeDocument, AssetType, Asset, LeaveType, LeaveRequest, PayrollSetting


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'phone', 'email', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('name', 'city', 'email')


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'status', 'created_at')
    list_filter = ('status', 'branch')
    search_fields = ('name', 'branch__name')


@admin.register(Designation)
class DesignationAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'status', 'created_at')
    list_filter = ('status', 'department')
    search_fields = ('name', 'department__name')


class EmployeeDocumentInline(admin.TabularInline):
    model = EmployeeDocument
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'employee_id', 'department', 'designation', 'employee_status', 'date_of_joining')
    list_filter = ('employee_status', 'employment_type', 'department', 'branch')
    search_fields = ('full_name', 'employee_id', 'email', 'phone')
    inlines = [EmployeeDocumentInline]


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('name', 'description')


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'asset_type', 'asset_code', 'status', 'condition', 'assigned_to', 'location')
    list_filter = ('status', 'condition', 'asset_type', 'depreciation_method')
    search_fields = ('name', 'asset_code', 'serial_number', 'location', 'supplier')


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'max_days_per_year', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'days', 'status', 'created_at')
    list_filter = ('status', 'leave_type')
    search_fields = ('employee__full_name', 'employee__employee_id', 'reason')


@admin.register(PayrollSetting)
class PayrollSettingAdmin(admin.ModelAdmin):
    """Singleton admin — prevent adding new rows; only edit the one that exists."""
    list_display = ('salary_divisor_type', 'weekend_multiplier', 'holiday_multiplier', 'ot_multiplier', 'shift_hours_per_day', 'updated_at')
    fieldsets = (
        ('Salary Divisor', {
            'description': 'FIXED_30 → daily rate = salary/30 always. ACTUAL_CYCLE_DAYS → daily rate = salary / number of calendar days in pay cycle.',
            'fields': ('salary_divisor_type',),
        }),
        ('Pay Multipliers', {
            'description': 'Multipliers applied when employees work on weekends, holidays, or overtime.',
            'fields': ('weekend_multiplier', 'holiday_multiplier', 'ot_multiplier'),
        }),
        ('Shift Configuration', {
            'fields': ('shift_hours_per_day',),
        }),
    )

    def has_add_permission(self, request):
        # Only one settings row allowed
        return not PayrollSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


from .models import Payslip

@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = ('payslip_number', 'employee', 'payroll_run', 'net_salary', 'status', 'created_at')
    list_filter = ('status', 'payroll_run')
    search_fields = ('payslip_number', 'employee__full_name', 'employee__employee_id')
    readonly_fields = ('basic_salary', 'gross_salary', 'total_deductions', 'advance_deduction', 'absent_deduction', 'net_salary', 'salary_structure')

