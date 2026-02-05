from django.contrib.auth.models import AbstractUser
from django.db import models
from decimal import Decimal

class CustomUser(AbstractUser):
    """User Model with Granular Custom Permissions"""
    
    ROLE_CHOICES = [
        ('administrator', 'Administrator'),
        ('warehouse', 'Warehouse'),
        ('sales', 'Sales'),
    ]

    phone = models.CharField(max_length=20, blank=True, null=True)
    profile_picture = models.ImageField(
        upload_to='profile_pictures/', 
        blank=True, 
        null=True
    )
    
    # ✅ Vendor/Seller ID for logistics API integration
    vendor_id = models.CharField(max_length=100, blank=True, null=True, unique=True, 
                                help_text="Unique vendor ID for logistics providers like NCM")
    
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='sales')
    email = models.EmailField(unique=True, blank=False)
    phone = models.CharField(max_length=15, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    
    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='deleted_users')
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_users')
    
    # ORDER PERMISSIONS
    can_view_orders = models.BooleanField(default=True)
    can_create_orders = models.BooleanField(default=True)
    can_edit_orders = models.BooleanField(default=True)
    can_delete_orders = models.BooleanField(default=False)
    can_cancel_orders = models.BooleanField(default=False)
    
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
    can_scan_barcodes = models.BooleanField(default=False)
    
    # INVENTORY PERMISSIONS
    can_view_inventory = models.BooleanField(default=True)
    can_manage_inventory = models.BooleanField(default=False)
    can_adjust_stock = models.BooleanField(default=False)
    
    # REPORT PERMISSIONS
    can_view_reports = models.BooleanField(default=False)
    can_view_sales_reports = models.BooleanField(default=False)
    can_view_financial_reports = models.BooleanField(default=False)
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
    
    
    groups = models.ManyToManyField('auth.Group', related_name='custom_user_set', blank=True)
    user_permissions = models.ManyToManyField('auth.Permission', related_name='custom_user_set', blank=True)
    
    REQUIRED_FIELDS = ['email']
    
    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
    
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
        if self.role == 'warehouse':
            self.can_view_orders = True
            self.can_edit_orders = True
            self.can_view_dispatch = True
            self.can_manage_dispatch = True
            self.can_scan_barcodes = True
            self.can_view_inventory = True
            self.can_manage_inventory = True
            self.can_adjust_stock = True
            
        elif self.role == 'sales':
            self.can_view_orders = True
            self.can_create_orders = True
            self.can_edit_orders = True
            self.can_view_products = True
            self.can_view_customers = True
            self.can_create_customers = True
            self.can_edit_customers = True
            self.can_give_discounts = True
            self.max_discount_percent = Decimal('10.00')
    
    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-date_joined']

