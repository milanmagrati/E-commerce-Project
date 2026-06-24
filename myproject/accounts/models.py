from django.contrib.auth.models import AbstractUser
from django.db import models
from decimal import Decimal


class Role(models.Model):
    """Dynamic Role model - roles are created here and referenced by users"""
    name = models.CharField(max_length=50, unique=True, help_text="Internal name used in code (e.g. administrator, sales, warehouse)")
    display_name = models.CharField(max_length=100, help_text="Human-readable name shown in UI")
    description = models.TextField(blank=True, default='')
    default_permissions = models.JSONField(default=dict, blank=True)
    is_system = models.BooleanField(default=False, help_text="System roles cannot be deleted without extra confirmation")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_name']
        verbose_name = 'Role'
        verbose_name_plural = 'Roles'

    def __str__(self):
        return self.display_name

    @property
    def user_count(self):
        return CustomUser.objects.filter(role=self.name, is_deleted=False).count()


class CustomUser(AbstractUser):
    """User Model with Granular Custom Permissions"""

    profile_picture = models.ImageField(
        upload_to='profile_pictures/', 
        blank=True, 
        null=True
    )
    
    # ✅ Vendor/Seller ID for logistics API integration
    vendor_id = models.CharField(max_length=100, blank=True, null=True, unique=True, 
                                help_text="Unique vendor ID for logistics providers like NCM")
    
    role = models.CharField(max_length=50, default='sales')
    email = models.EmailField(unique=True, blank=False)
    phone = models.CharField(max_length=20, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    
    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='deleted_users')
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_users')
    
    # ORDER PERMISSIONS
    can_view_orders = models.BooleanField(default=True)
    can_view_orders_list = models.BooleanField(default=False, verbose_name="Can View All Orders List")
    can_create_orders = models.BooleanField(default=True)
    can_edit_orders = models.BooleanField(default=False)
    can_delete_orders = models.BooleanField(default=False)
    can_cancel_orders = models.BooleanField(default=False)
    can_view_on_hold_orders = models.BooleanField(default=False)
    can_export_orders = models.BooleanField(default=False, verbose_name="Can Export Orders to Excel")
    can_access_offer_price = models.BooleanField(default=False, verbose_name="Can Access Offer Price")
    
    # PRODUCT PERMISSIONS
    can_view_products = models.BooleanField(default=True)
    can_create_products = models.BooleanField(default=False)
    can_edit_products = models.BooleanField(default=False)
    can_delete_products = models.BooleanField(default=False)
    
    # CUSTOMER PERMISSIONS
    can_view_customers = models.BooleanField(default=True)
    can_create_customers = models.BooleanField(default=True)
    can_edit_customers = models.BooleanField(default=True)
    can_delete_customers = models.BooleanField(default=False)
    
    # DISPATCH PERMISSIONS
    can_view_dispatch = models.BooleanField(default=False)
    can_manage_dispatch = models.BooleanField(default=False)
    can_delete_dispatch = models.BooleanField(default=False)
    can_scan_barcodes = models.BooleanField(default=False)
    
    # INVENTORY PERMISSIONS
    can_view_inventory = models.BooleanField(default=True)
    can_manage_inventory = models.BooleanField(default=False)
    can_adjust_stock = models.BooleanField(default=False)
    can_view_inventory_cost = models.BooleanField(default=False, verbose_name="Can View Inventory Cost")
    can_toggle_product_price = models.BooleanField(default=False, verbose_name="Toggle Product Price")
    can_view_selling_unit_price = models.BooleanField(default=False, verbose_name="View Selling Unit Price")
    can_view_cost_unit_price = models.BooleanField(default=False, verbose_name="View Cost Unit Price")

    # STOCK VALUATION PERMISSIONS
    can_view_valuation_selling = models.BooleanField(default=False, verbose_name="Valuation by Selling Price")
    can_view_valuation_cost = models.BooleanField(default=False, verbose_name="Valuation by Cost Price")
    can_toggle_stock_valuation = models.BooleanField(default=False, verbose_name="Toggle Stock Valuation")

    # REPORT PERMISSIONS
    can_view_reports = models.BooleanField(default=False)
    can_view_sales_reports = models.BooleanField(default=False)
    can_view_daily_sales_reports = models.BooleanField(default=False, verbose_name="Can View Daily Sales Reports")
    can_view_product_sales_reports = models.BooleanField(default=False, verbose_name="Can View Product Sales Reports")
    can_view_financial_reports = models.BooleanField(default=False)
    can_view_total_revenue = models.BooleanField(default=False, verbose_name="Can View Total Revenue")
    can_export_data = models.BooleanField(default=False)
    
    # PRICING PERMISSIONS
    can_view_cost_price = models.BooleanField(default=False)
    can_edit_prices = models.BooleanField(default=False)
    can_give_discounts = models.BooleanField(default=True)
    max_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    
     # ✅ RETURN MANAGEMENT PERMISSIONS
    can_view_returns = models.BooleanField(default=False, verbose_name="Can View Returns")
    can_create_returns = models.BooleanField(default=False, verbose_name="Can Create Returns")
    can_edit_returns = models.BooleanField(default=False, verbose_name="Can Edit Returns")
    can_delete_returns = models.BooleanField(default=False, verbose_name="Can Delete Returns")
    can_approve_returns = models.BooleanField(default=False, verbose_name="Can Approve/Reject Returns")
    can_process_refunds = models.BooleanField(default=False, verbose_name="Can Process Refunds")

    # STAFF TARGET PERMISSIONS
    can_view_targets = models.BooleanField(default=False, verbose_name="Can View All Targets")
    can_set_targets = models.BooleanField(default=False, verbose_name="Can Set Targets")
    can_edit_targets = models.BooleanField(default=False, verbose_name="Can Edit Targets")
    can_delete_targets = models.BooleanField(default=False, verbose_name="Can Delete Targets")
    can_view_own_targets = models.BooleanField(default=True, verbose_name="Can View Own Targets")

    # PURCHASE MANAGEMENT PERMISSIONS
    can_view_purchases = models.BooleanField(default=False, verbose_name="Can View Purchases")
    can_create_purchases = models.BooleanField(default=False, verbose_name="Can Create Purchases")
    can_manage_suppliers = models.BooleanField(default=False, verbose_name="Can Manage Suppliers")
    can_make_supplier_payments = models.BooleanField(default=False, verbose_name="Can Make Supplier Payments")

    # STAFF PERFORMANCE PERMISSIONS
    can_view_staff_performance = models.BooleanField(default=False, verbose_name="Can View Staff Performance")

    # CITY MANAGEMENT PERMISSIONS
    can_view_cities = models.BooleanField(default=False, verbose_name="Can View Cities")
    can_add_cities = models.BooleanField(default=False, verbose_name="Can Add Cities")
    can_edit_cities = models.BooleanField(default=False, verbose_name="Can Edit Cities")
    can_delete_cities = models.BooleanField(default=False, verbose_name="Can Delete Cities")

    # DASHBOARD PERMISSIONS
    can_view_dashboard = models.BooleanField(default=True, verbose_name="Can View Dashboard")
    can_view_low_stock_alerts = models.BooleanField(default=False, verbose_name="Can View Low Stock Alerts")

    # NCM LOGISTICS PERMISSIONS
    can_view_ncm_orders = models.BooleanField(default=False, verbose_name="Can View NCM Orders")
    can_create_ncm_orders = models.BooleanField(default=False, verbose_name="Can Create NCM Orders")
    can_edit_ncm_orders = models.BooleanField(default=False, verbose_name="Can Edit NCM Orders")
    can_delete_ncm_orders = models.BooleanField(default=False, verbose_name="Can Delete NCM Orders")
    can_view_ncm_bulk_logs = models.BooleanField(default=False, verbose_name="Can View NCM Bulk Logs")
    can_manage_ncm_bulk_logs = models.BooleanField(default=False, verbose_name="Can Manage NCM Bulk Logs")
    can_view_ncm_trash = models.BooleanField(default=False, verbose_name="Can View NCM Trash")
    can_sync_ncm_orders = models.BooleanField(default=False, verbose_name="Can Sync NCM Orders")
    can_view_ncm_branches = models.BooleanField(default=False, verbose_name="Can View NCM Branches")
    can_manage_ncm_branches = models.BooleanField(default=False, verbose_name="Can Manage NCM Branches")

    # HRM PERMISSIONS
    can_view_hrm = models.BooleanField(default=False, verbose_name="Can View HRM")
    can_view_hrm_hr_management = models.BooleanField(default=False, verbose_name="Can View HR Management")
    can_view_hrm_asset_management = models.BooleanField(default=False, verbose_name="Can View Asset Management")
    can_view_hrm_attendance = models.BooleanField(default=False, verbose_name="Can View Attendance")
    can_view_hrm_payroll = models.BooleanField(default=False, verbose_name="Can View Payroll Management")

    # TODO / TICKETING PERMISSIONS
    can_access_todo = models.BooleanField(default=False, verbose_name="Access to Todo/Ticketing")

    # FOLLOW UP PERMISSIONS
    can_access_follow_ups = models.BooleanField(default=False, verbose_name="Can Access Follow Ups")
    can_setup_follow_up_status = models.BooleanField(default=False, verbose_name="Can Setup Follow Up Status")

    groups = models.ManyToManyField('auth.Group', related_name='custom_user_set', blank=True)
    user_permissions = models.ManyToManyField('auth.Permission', related_name='custom_user_set', blank=True)
    
    REQUIRED_FIELDS = ['email']
    
    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    def get_role_display(self):
        """Get human-readable role name from Role model"""
        try:
            role_obj = Role.objects.get(name=self.role)
            return role_obj.display_name
        except Role.DoesNotExist:
            return self.role.title() if self.role else 'Unknown'
    
    @property
    def is_administrator(self):
        return self.role == 'administrator' or self.is_superuser
    
    def soft_delete(self, deleted_by_user):
        from django.utils import timezone
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by_user
        self.save()
    
    def restore(self):
        self.is_deleted = False
        self.is_active = True
        self.deleted_at = None
        self.deleted_by = None
        self.save()
    
    def set_default_permissions_by_role(self):
        """Auto-set permissions based on role"""
        if not self.role:
            return

        if self.role == 'administrator':
            for perm in [
                'can_view_dashboard', 'can_view_total_revenue', 'can_view_low_stock_alerts',
                'can_view_orders', 'can_create_orders', 'can_edit_orders', 
                'can_delete_orders', 'can_cancel_orders', 'can_view_on_hold_orders', 
                'can_export_orders', 'can_view_orders_list', 'can_access_offer_price',
                'can_view_products', 'can_create_products', 'can_edit_products', 'can_delete_products',
                'can_view_customers', 'can_create_customers', 'can_edit_customers', 'can_delete_customers',
                'can_view_returns', 'can_create_returns', 'can_edit_returns',
                'can_delete_returns', 'can_approve_returns', 'can_process_refunds',
                'can_view_targets', 'can_set_targets', 'can_edit_targets',
                'can_delete_targets', 'can_view_own_targets',
                'can_view_dispatch', 'can_manage_dispatch', 'can_delete_dispatch', 'can_scan_barcodes',
                'can_view_inventory', 'can_manage_inventory', 'can_adjust_stock',
                'can_view_inventory_cost', 'can_toggle_product_price',
                'can_view_selling_unit_price', 'can_view_cost_unit_price',
                'can_view_valuation_selling', 'can_view_valuation_cost', 'can_toggle_stock_valuation',
                'can_view_reports', 'can_view_sales_reports', 'can_view_financial_reports', 'can_export_data',
                'can_view_purchases', 'can_create_purchases', 'can_manage_suppliers', 'can_make_supplier_payments',
                'can_view_staff_performance',
                'can_view_cities', 'can_add_cities', 'can_edit_cities', 'can_delete_cities',
                'can_view_ncm_orders', 'can_create_ncm_orders', 'can_edit_ncm_orders',
                'can_delete_ncm_orders', 'can_view_ncm_bulk_logs', 'can_manage_ncm_bulk_logs',
                'can_view_ncm_trash', 'can_sync_ncm_orders', 'can_view_ncm_branches', 'can_manage_ncm_branches',
                'can_view_hrm', 'can_view_hrm_hr_management', 'can_view_hrm_asset_management',
                'can_view_hrm_attendance', 'can_view_hrm_payroll',
                'can_access_todo', 'can_access_follow_ups', 'can_setup_follow_up_status',
                'can_view_cost_price', 'can_edit_prices', 'can_give_discounts'
            ]:
                setattr(self, perm, True)
            self.max_discount_percent = Decimal('100.00')
            return

        try:
            role_obj = Role.objects.get(name=self.role)
            default_perms = role_obj.default_permissions
            
            if 'permissions' in default_perms:
                for perm in default_perms['permissions']:
                    if hasattr(self, perm):
                        setattr(self, perm, True)
            
            if 'max_discount_percent' in default_perms:
                self.max_discount_percent = Decimal(str(default_perms['max_discount_percent']))
                
        except Role.DoesNotExist:
            pass
    
    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-date_joined']

