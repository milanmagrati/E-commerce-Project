from django.contrib import admin
from .models import Branch, Department, Designation


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
