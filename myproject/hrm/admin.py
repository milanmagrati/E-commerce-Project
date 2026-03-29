from django.contrib import admin
from .models import Branch, Department, Designation, Employee, EmployeeDocument, AssetType, Asset, LeaveType, LeaveRequest


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
