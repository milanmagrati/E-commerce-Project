from django.db import models
from django.conf import settings  # ✅ Add this import
from django.utils import timezone
from django.contrib.auth import get_user_model
import random
import string

# ✅ Remove this line:
# from django.contrib.auth.models import User


class Category(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Categories"


class Product(models.Model):
    
    PRODUCT_TYPE = (
        ('simple', 'Simple Product'),
        ('variable', 'Variable Product'),
    )
    
    product_type = models.CharField(max_length=20, choices=PRODUCT_TYPE, default='simple')
    STOCK_STATUS = (
        ('in_stock', 'In Stock'),
        ('out_of_stock', 'Out of Stock'),
        ('low_stock', 'Low Stock'),
    )

    # ✅ FIXED: Changed User to settings.AUTH_USER_MODEL
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock = models.IntegerField(default=0)
    stock_status = models.CharField(max_length=20, choices=STOCK_STATUS, default='in_stock')
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    barcode = models.CharField(max_length=100, blank=True, null=True)  # ADD THIS
    is_active = models.BooleanField(default=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']
        

class Customer(models.Model):
    CUSTOMER_TYPES = [
        ('retail', 'Retail'),
        ('wholesale', 'Wholesale'),
        ('vip', 'VIP'),
    ]
    
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)
    alternate_phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, default='Nepal')
    address = models.TextField()
    landmark = models.CharField(max_length=255, blank=True)
    customer_type = models.CharField(max_length=20, choices=CUSTOMER_TYPES, default='retail')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['-created_at']


class Order(models.Model):
    LOGISTICS_CHOICES = [
        ('ncm', 'NCM'),
        ('sundarijal', 'Sundarijal'),
        ('express', 'Express'),
        ('local', 'Local Delivery'),
        ('other', 'Other'),
    ]
    
    branch_city = models.CharField(max_length=100)
    
    IN_OUT_CHOICES = [
        ('in', 'IN'),
        ('out', 'OUT'),
    ]
    in_out = models.CharField(max_length=3, choices=IN_OUT_CHOICES, default='in')
    
    logistics = models.CharField(max_length=50, choices=LOGISTICS_CHOICES, blank=True, null=True)
    status = models.CharField(max_length=50, default='processing')  # Rename from order_status
    dispatch_date = models.DateTimeField(blank=True, null=True)
    order_number = models.CharField(max_length=50, unique=True)
    
    barcode = models.CharField(max_length=100, blank=True, null=True)  # ADD THIS 
    
    # ✅ FIXED: Changed User to settings.AUTH_USER_MODEL
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_orders')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    customer_name = models.CharField(max_length=255)
    customer_phone = models.CharField(max_length=20)
    customer_email = models.EmailField(blank=True)
    
    shipping_address = models.TextField()
    landmark = models.CharField(max_length=255, blank=True)
    order_from = models.CharField(max_length=50)
    order_status = models.CharField(max_length=50, default='processing')
    payment_method = models.CharField(max_length=50)
    payment_status = models.CharField(max_length=50, default='pending')
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=13)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True)
    
    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    admin_notes = models.TextField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    is_partial_payment = models.BooleanField(default=False)
    partial_amount_paid = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    remaining_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    def __str__(self):
        return self.order_number
    
    def calculate_totals(self):
        """Calculate order totals based on items, discount, shipping, and tax"""
        from decimal import Decimal
        
        subtotal = sum(item.total for item in self.items.all()) or Decimal('0.00')
        after_discount = subtotal - self.discount_amount
        tax_amount = (after_discount * self.tax_percent) / 100
        self.total_amount = after_discount + tax_amount + self.shipping_charge
    
    class Meta:
        ordering = ['-created_at']


class OrderItem(models.Model):
    """Enhanced Order Item Model"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('Product', on_delete=models.SET_NULL, null=True)
    product_variation = models.ForeignKey('ProductVariation', on_delete=models.SET_NULL, null=True, blank=True)
    
    product_name = models.CharField(max_length=255, default='')
    product_sku = models.CharField(max_length=100, blank=True, null=True)
    quantity = models.IntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product_name} x {self.quantity}"

    def save(self, *args, **kwargs):
        self.total = self.price * self.quantity
        super().save(*args, **kwargs)


class ProductAttribute(models.Model):
    """Product attributes like Size, Color, Material"""
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class ProductAttributeValue(models.Model):
    """Values for attributes like Small, Medium, Large for Size"""
    attribute = models.ForeignKey(ProductAttribute, on_delete=models.CASCADE, related_name='values')
    value = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"

    class Meta:
        ordering = ['attribute', 'value']


class ProductVariation(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('out_of_stock', 'Out of Stock'),
    ]
    
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variations')
    variation_name = models.CharField(max_length=200, blank=True, null=True)  # ✅ ADD THIS LINE
    sku = models.CharField(max_length=100, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    is_active = models.BooleanField(default=True, null=True, blank=True)
    image = models.ImageField(upload_to='variations/', blank=True, null=True)
    barcode = models.CharField(max_length=100, blank=True, null=True)  # ADD THIS
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.product.name} - {self.sku}"
    
    class Meta:
        ordering = ['sku']


class VariationAttributeValue(models.Model):
    """Links variations to their attribute values"""
    variation = models.ForeignKey(ProductVariation, on_delete=models.CASCADE, related_name='attribute_values')
    attribute_value = models.ForeignKey(ProductAttributeValue, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.variation.sku} - {self.attribute_value}"

    class Meta:
        unique_together = ('variation', 'attribute_value')


class ProductImage(models.Model):
    """Model for storing multiple product images"""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='products/gallery/')
    alt_text = models.CharField(max_length=255, blank=True, null=True)
    is_featured = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', '-created_at']
        verbose_name = 'Product Image'
        verbose_name_plural = 'Product Images'

    def __str__(self):
        return f"{self.product.name} - Image {self.id}"

    def save(self, *args, **kwargs):
        if not self.alt_text:
            self.alt_text = f"{self.product.name} - Gallery Image"
        super().save(*args, **kwargs)


class ProductVariantOption(models.Model):
    """Store variant options like Color, Size, Material"""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variant_options')
    option_name = models.CharField(max_length=100)
    option_values = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.product.name} - {self.option_name}"
    
    def get_values_list(self):
        return [v.strip() for v in self.option_values.split(',') if v.strip()]


class OrderActivityLog(models.Model):
    """Track all order changes and activities"""
    ACTION_TYPES = [
        ('created', 'Order Created'),
        ('status_changed', 'Status Changed'),
        ('payment_changed', 'Payment Status Changed'),
        ('tracking_added', 'Tracking Number Added'),
        ('tracking_updated', 'Tracking Number Updated'),
        ('notes_added', 'Admin Notes Added'),
        ('notes_updated', 'Admin Notes Updated'),
        ('updated', 'Order Updated'),
    ]
    
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='activity_logs')
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)
    
    # ✅ FIXED: Changed User to settings.AUTH_USER_MODEL
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    
    field_name = models.CharField(max_length=100, blank=True)
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.order.order_number} - {self.get_action_type_display()}"
    
    class Meta:
        ordering = ['-created_at']


class StockIn(models.Model):
    """Track incoming stock movements"""
    STOCK_IN_TYPES = (
        ('purchase', 'Purchase Order'),
        ('return', 'Customer Return'),
        ('adjustment', 'Stock Adjustment'),
        ('transfer', 'Transfer In'),
        ('other', 'Other'),
    )
    
    reference_number = models.CharField(max_length=50, unique=True, editable=False, blank=True)
    stock_in_type = models.CharField(max_length=20, choices=STOCK_IN_TYPES, default='purchase')
    supplier_name = models.CharField(max_length=200, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    total_quantity = models.IntegerField(default=0)
    total_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # ✅ FIXED: Changed User to settings.AUTH_USER_MODEL
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='stock_ins')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Stock In'
        verbose_name_plural = 'Stock Ins'
    
    def __str__(self):
        return f"{self.reference_number} - {self.get_stock_in_type_display()}"
    
    def save(self, *args, **kwargs):
        if not self.reference_number:
            from datetime import datetime
            
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            random_str = ''.join(random.choices(string.digits, k=4))
            self.reference_number = f"SI-{timestamp}-{random_str}"
        
        super().save(*args, **kwargs)


class StockInItem(models.Model):
    """Individual items in a stock in transaction"""
    stock_in = models.ForeignKey(StockIn, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_in_items')
    product_variation = models.ForeignKey(
        ProductVariation, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name='stock_in_items'
    )
    quantity = models.IntegerField(default=1)
    unit_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    notes = models.CharField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Stock In Item'
        verbose_name_plural = 'Stock In Items'
    
    def __str__(self):
        if self.product_variation:
            return f"{self.product.name} ({self.product_variation.sku}) - Qty: {self.quantity}"
        return f"{self.product.name} - Qty: {self.quantity}"


class City(models.Model):
    """City model to store valley/out-valley classification"""
    VALLEY_STATUS_CHOICES = [
        ('valley', 'Valley'),
        ('out_valley', 'Out Valley'),
    ]
    
    name = models.CharField(max_length=100, unique=True)
    valley_status = models.CharField(
        max_length=20, 
        choices=VALLEY_STATUS_CHOICES,
        default='valley'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "Cities"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.get_valley_status_display()})"
    
    

# Add these imports at the top
User = get_user_model()

# ==================== RETURN MANAGEMENT MODELS ====================
class ReturnRequest(models.Model):
    """Main return request model"""
    
    RETURN_STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('received', 'Item Received'),
        ('inspecting', 'Quality Inspection'),
        ('approved_refund', 'Approved for Refund'),
        ('approved_exchange', 'Approved for Exchange'),
        ('refunded', 'Refunded'),
        ('exchanged', 'Exchanged'),
        ('cancelled', 'Cancelled'),
    ]
    
    RETURN_REASON_CHOICES = [
        ('defective', 'Defective/Damaged Product'),
        ('wrong_item', 'Wrong Item Received'),
        ('not_as_described', 'Not as Described'),
        ('size_issue', 'Size/Fit Issue'),
        ('quality_issue', 'Quality Issue'),
        ('changed_mind', 'Changed Mind'),
        ('ordered_by_mistake', 'Ordered by Mistake'),
        ('late_delivery', 'Late Delivery'),
        ('other', 'Other'),
    ]
    
    REFUND_TYPE_CHOICES = [
        ('full_refund', 'Full Refund'),
        ('partial_refund', 'Partial Refund'),
        ('store_credit', 'Store Credit'),
        ('exchange', 'Exchange for Another Item'),
        ('no_refund', 'No Refund'),
    ]
    
    CONDITION_CHOICES = [
        ('new', 'New/Unused'),
        ('opened', 'Opened but Unused'),
        ('used', 'Used - Good Condition'),
        ('damaged', 'Damaged'),
        ('defective', 'Defective'),
    ]
    
    # Basic Info
    rma_number = models.CharField(max_length=50, unique=True, editable=False)
    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='returns')
    customer = models.ForeignKey('Customer', on_delete=models.CASCADE, related_name='returns', null=True, blank=True)
    customer_name = models.CharField(max_length=255)
    customer_phone = models.CharField(max_length=20)
    customer_email = models.EmailField(blank=True, null=True)
    
    # Return Details
    return_reason = models.CharField(max_length=50, choices=RETURN_REASON_CHOICES)
    return_status = models.CharField(max_length=50, choices=RETURN_STATUS_CHOICES, default='pending')
    refund_type = models.CharField(max_length=50, choices=REFUND_TYPE_CHOICES, default='full_refund')
    
    # Financial
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    restocking_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Quality Check
    condition_received = models.CharField(max_length=50, choices=CONDITION_CHOICES, blank=True, null=True)
    quality_check_notes = models.TextField(blank=True, null=True)
    quality_checked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='quality_checked_returns')
    quality_checked_at = models.DateTimeField(null=True, blank=True)
    
    # Images
    return_image_1 = models.ImageField(upload_to='returns/', blank=True, null=True)
    return_image_2 = models.ImageField(upload_to='returns/', blank=True, null=True)
    return_image_3 = models.ImageField(upload_to='returns/', blank=True, null=True)
    
    # Notes
    customer_notes = models.TextField(blank=True, null=True, help_text="Customer's reason for return")
    admin_notes = models.TextField(blank=True, null=True, help_text="Internal admin notes")
    rejection_reason = models.TextField(blank=True, null=True)
    
    # Tracking
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_returns')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_returns')
    approved_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    
    # ✅ SOFT DELETE FIELDS
    is_deleted = models.BooleanField(default=False)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deleted_returns')
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Return Request'
        verbose_name_plural = 'Return Requests'
    
    def __str__(self):
        return f"{self.rma_number} - {self.customer_name}"
    
    def save(self, *args, **kwargs):
        if not self.rma_number:
            # Generate RMA number: RMA-YYYYMMDD-XXXX
            today = timezone.now()
            date_str = today.strftime('%Y%m%d')
            last_return = ReturnRequest.objects.filter(
                rma_number__startswith=f'RMA-{date_str}'
            ).order_by('-rma_number').first()
            
            if last_return:
                last_num = int(last_return.rma_number.split('-')[-1])
                new_num = last_num + 1
            else:
                new_num = 1
            
            self.rma_number = f'RMA-{date_str}-{new_num:04d}'
        
        super().save(*args, **kwargs)
    
    def get_status_display_class(self):
        """Return Bootstrap class for status badge"""
        status_classes = {
            'pending': 'warning',
            'approved': 'info',
            'rejected': 'danger',
            'received': 'primary',
            'inspecting': 'secondary',
            'approved_refund': 'success',
            'approved_exchange': 'success',
            'refunded': 'success',
            'exchanged': 'success',
            'cancelled': 'dark',
        }
        return status_classes.get(self.return_status, 'secondary')
    
    # ✅ SOFT DELETE METHOD
    def soft_delete(self, user):
        """Move to trash instead of permanent delete"""
        self.is_deleted = True
        self.deleted_by = user
        self.deleted_at = timezone.now()
        self.save()
    
    # ✅ RESTORE METHOD
    def restore(self):
        """Restore from trash"""
        self.is_deleted = False
        self.deleted_by = None
        self.deleted_at = None
        self.save()


class ReturnItem(models.Model):
    """Items in a return request"""
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name='items')
    order_item = models.ForeignKey('OrderItem', on_delete=models.CASCADE)
    product = models.ForeignKey('Product', on_delete=models.CASCADE)
    product_variation = models.ForeignKey('ProductVariation', on_delete=models.SET_NULL, null=True, blank=True)
    
    product_name = models.CharField(max_length=255)
    product_sku = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    
    # Return specific
    return_quantity = models.PositiveIntegerField(default=1)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Inventory action
    restocked = models.BooleanField(default=False)
    restocked_at = models.DateTimeField(null=True, blank=True)
    restocked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['id']
    
    def __str__(self):
        return f"{self.product_name} x{self.return_quantity}"


class ReturnActivityLog(models.Model):
    """Activity log for return requests"""
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name='activity_logs')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action_type = models.CharField(max_length=50)  # created, approved, rejected, received, refunded, trashed, restored, etc.
    description = models.TextField()
    field_name = models.CharField(max_length=100, blank=True, null=True)
    old_value = models.TextField(blank=True, null=True)
    new_value = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.return_request.rma_number} - {self.action_type}"



class Dispatch(models.Model):
    """Dispatch/Batch management for orders"""
    
    STATUS_CHOICES = [
        ('pending', 'Pending payment'),
        ('processing', 'Processing'),
        ('confirmed', 'Confirmed'),
        ('packed', 'Packed'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
        ('dispatched', 'Dispatched'),
    ]
    
    LOGISTICS_CHOICES = [
        ('ncm', 'NCM'),
        ('sundarijal', 'Sundarijal'),
        ('express', 'Express'),
        ('local', 'Local Delivery'),
        ('other', 'Other'),
    ]
    
    # Basic Info
    batch_number = models.CharField(max_length=50, unique=True, db_index=True)
    logistics = models.CharField(max_length=50, choices=LOGISTICS_CHOICES)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='dispatched')
    total_orders = models.IntegerField(default=0)
    notes = models.TextField(blank=True, null=True)
    
    # User Tracking
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='created_dispatches'
    )
    
    # ✅ SOFT DELETE FIELDS
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='deleted_dispatches'
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Dispatch'
        verbose_name_plural = 'Dispatches'
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['batch_number']),
            models.Index(fields=['is_deleted']),
        ]
    
    def __str__(self):
        return f"{self.batch_number} - {self.logistics}"
    
    def get_order_ids(self):
        """Return list of all scanned order IDs in this dispatch"""
        return [item.scanned_order_id for item in self.items.all()]
    
    def get_linked_orders_count(self):
        """Count orders that were successfully linked"""
        return self.items.filter(order__isnull=False).count()
    
    def get_unlinked_orders_count(self):
        """Count order IDs that couldn't be found in system"""
        return self.items.filter(order__isnull=True).count()
    
    # ✅ SOFT DELETE METHOD
    def soft_delete(self, user):
        """Move to trash instead of permanent delete"""
        self.is_deleted = True
        self.deleted_by = user
        self.deleted_at = timezone.now()
        self.save()
    
    # ✅ RESTORE METHOD
    def restore(self):
        """Restore from trash"""
        self.is_deleted = False
        self.deleted_by = None
        self.deleted_at = None
        self.save()


class DispatchItem(models.Model):
    """Individual order items in a dispatch batch"""
    
    dispatch = models.ForeignKey(
        Dispatch, 
        on_delete=models.CASCADE, 
        related_name='items'
    )
    scanned_order_id = models.CharField(max_length=100, db_index=True)
    order = models.ForeignKey(
        'Order', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='dispatch_items'
    )
    scanned_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['scanned_at']
        verbose_name = 'Dispatch Item'
        verbose_name_plural = 'Dispatch Items'
        indexes = [
            models.Index(fields=['scanned_order_id']),
            models.Index(fields=['scanned_at']),
        ]
    
    def __str__(self):
        return f"{self.scanned_order_id} in {self.dispatch.batch_number}"
    
    def is_linked(self):
        """Check if order was successfully linked"""
        return self.order is not None
    
    def get_order_status(self):
        """Get the status of linked order"""
        if self.order:
            return self.order.get_order_status_display()
        return "Not Found"
    
    def get_customer_name(self):
        """Get customer name from linked order"""
        if self.order:
            return self.order.customer_name
        return "N/A"
