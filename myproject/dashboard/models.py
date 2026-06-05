from django.db import models
from django.db.models import Q, Sum
from django.conf import settings  # ✅ Add this import
from django.utils import timezone
from django.contrib.auth import get_user_model
import random
import string
import logging
from decimal import Decimal
from .decimal_utils import safe_decimal, validate_decimal_fields

logger = logging.getLogger(__name__)

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


class Branch(models.Model):
    """Business branch/location model"""
    name = models.CharField(max_length=200, unique=True)
    city = models.CharField(max_length=100)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.city}"

    class Meta:
        ordering = ['name']
        verbose_name_plural = "Branches"


class Product(models.Model):

    PRODUCT_TYPE = (
        ('simple', 'Simple Product'),
        ('variable', 'Variable Product'),
        ('bundle', 'Bundle/Combo Product'),
    )

    STOCK_STATUS = (
        ('in_stock', 'In Stock'),
        ('out_of_stock', 'Out of Stock'),
        ('low_stock', 'Low Stock'),
    )

    COST_PRICE_TYPE = (
        ('fixed', 'Fixed Cost Price'),
        ('variable', 'Variable Cost Price'),
    )

    # User/Owner
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='products')

    # Basic Information
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)

    # Product Type
    product_type = models.CharField(max_length=20, choices=PRODUCT_TYPE, default='simple')

    # Pricing
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cost_price_type = models.CharField(max_length=10, choices=COST_PRICE_TYPE, default='fixed')

    # Inventory
    stock = models.IntegerField(default=0)
    reserved_qty = models.IntegerField(default=0, help_text="Units locked by unshipped orders")
    backordered_qty = models.IntegerField(default=0, help_text="Units ordered beyond available stock")
    backorders_allowed = models.BooleanField(default=False, help_text="Allow orders beyond available stock")
    stock_status = models.CharField(max_length=20, choices=STOCK_STATUS, default='in_stock')
    low_stock_threshold = models.IntegerField(default=0, help_text="Alert when stock falls to or below this value")

    # Media
    image = models.ImageField(upload_to='products/', blank=True, null=True)

    # Identifiers
    barcode = models.CharField(max_length=100, blank=True, null=True)

    # Status Flags
    is_active = models.BooleanField(default=True, null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    # ✅ NEW: Custom Product Flag (for quick sales)
    is_custom_product = models.BooleanField(default=False, help_text="Mark as custom/quick sale product")

    # Dates & Expiry
    manufactured_date = models.DateField(null=True, blank=True, help_text="Date the product was manufactured")
    expiry_date = models.DateField(null=True, blank=True, help_text="Date the product expires")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    @property
    def is_bundle(self):
        """Check if this product is a bundle/combo product."""
        return self.product_type == 'bundle'

    @property
    def average_cost(self):
        """Calculate cost based on cost_price_type setting.
        - fixed: always returns the static cost_price field.
        - variable: returns weighted average from ProductPurchase records,
          falls back to cost_price if no purchases exist.
        Uses a single DB query (or prefetch cache) to avoid N+1 issues.
        """
        if self.cost_price_type == 'fixed':
            return self.cost_price
        # Variable cost price: weighted average from purchase history
        # list() evaluates once and uses prefetch cache if available
        purchases = list(self.product_purchases.all())
        if not purchases:
            return self.cost_price
        total_cost = sum(p.cost_price * p.quantity for p in purchases)
        total_qty = sum(p.quantity for p in purchases)
        if total_qty == 0:
            return self.cost_price
        return (total_cost / total_qty).quantize(Decimal('0.01'))

    @property
    def available_stock(self):
        """Return available stock (unreserved).
        For bundles: min(component.available_stock // qty_required) across all components.
        For simple/variable products: stock minus reserved_qty.
        """
        if self.is_bundle:
            components = self.bundle_components.select_related('component_product').all()
            if not components.exists():
                return 0
            return min(
                (comp.component_product.stock - comp.component_product.reserved_qty) // comp.quantity_required
                for comp in components
            )
        return self.stock - self.reserved_qty

    @property
    def is_expired(self):
        """Check if this product has passed its expiry date."""
        if not self.expiry_date:
            return False
        return self.expiry_date <= timezone.now().date()

    @property
    def days_until_expiry(self):
        """Return the number of days until this product expires (negative if expired)."""
        if not self.expiry_date:
            return None
        delta = self.expiry_date - timezone.now().date()
        return delta.days

    @property
    def expiry_status(self):
        """Return 'expired', 'critical' (<=7 days), 'warning' (<=30 days), 'ok', or 'no_expiry'."""
        days = self.days_until_expiry
        if days is None:
            return 'no_expiry'
        if days < 0:
            return 'expired'
        if days <= 7:
            return 'critical'
        if days <= 30:
            return 'warning'
        return 'ok'

    @property
    def earliest_expiry_batch(self):
        """Return the batch with the earliest expiry date (FIFO order)."""
        return self.batches.filter(
            quantity__gt=0, expiry_date__isnull=False
        ).order_by('expiry_date').first()

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Product'
        verbose_name_plural = 'Products'


class ProductBatch(models.Model):
    """Track individual stock batches for FIFO (First-In First-Out) inventory."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches')
    batch_number = models.CharField(max_length=50, blank=True)
    quantity = models.IntegerField(default=0, help_text="Remaining units in this batch")
    initial_quantity = models.IntegerField(default=0, help_text="Original units when batch was created")
    manufactured_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expiry_date', 'created_at']  # FIFO: oldest expiry first
        verbose_name = 'Product Batch'
        verbose_name_plural = 'Product Batches'

    def __str__(self):
        return f"{self.product.name} - Batch {self.batch_number or self.pk}"

    @property
    def is_expired(self):
        if not self.expiry_date:
            return False
        return self.expiry_date <= timezone.now().date()

    @property
    def days_until_expiry(self):
        if not self.expiry_date:
            return None
        delta = self.expiry_date - timezone.now().date()
        return delta.days

    @property
    def expiry_status(self):
        days = self.days_until_expiry
        if days is None:
            return 'no_expiry'
        if days < 0:
            return 'expired'
        if days <= 7:
            return 'critical'
        if days <= 30:
            return 'warning'
        return 'ok'


class BundleComponent(models.Model):
    """Links a bundle product to its component (child) products with required quantities."""
    bundle_product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='bundle_components',
        help_text="The bundle/combo product"
    )
    component_product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='part_of_bundles',
        help_text="The child component product"
    )
    quantity_required = models.PositiveIntegerField(
        default=1,
        help_text="How many units of this component are needed per bundle"
    )

    class Meta:
        unique_together = ('bundle_product', 'component_product')
        verbose_name = 'Bundle Component'
        verbose_name_plural = 'Bundle Components'

    def __str__(self):
        return f"{self.bundle_product.name} -> {self.component_product.name} x{self.quantity_required}"

    @property
    def line_cost(self):
        """Cost for this component line: unit average_cost * quantity_required."""
        return self.component_product.average_cost * self.quantity_required


class ProductPurchase(models.Model):
    """Track cost price per purchase batch for accurate weighted average cost."""
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='product_purchases',
        help_text="Simple product this purchase is for"
    )
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    purchase_date = models.DateField(auto_now_add=True)

    class Meta:
        ordering = ['-purchase_date']
        verbose_name = 'Product Purchase'
        verbose_name_plural = 'Product Purchases'

    def __str__(self):
        return f"{self.product.name} - {self.quantity} units @ Rs.{self.cost_price}"


class Customer(models.Model):
    CUSTOMER_TYPES = [
        ('retail', 'Retail'),
        ('wholesale', 'Wholesale'),
        ('vip', 'VIP'),
    ]

    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
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


class OrderQuerySet(models.QuerySet):
    """Custom QuerySet for Order model to handle decimal field issues gracefully"""

    def safe_recent(self, limit=6):
        """
        Safely load recent orders, deferring decimal fields to prevent
        decimal.InvalidOperation errors from corrupted database values.
        """
        decimal_fields_to_defer = [
            'discount_amount', 'shipping_charge', 'delivery_charge',
            'expense_amount', 'tax_percent', 'total_amount',
            'partial_amount_paid', 'remaining_amount', 'cod_collected',
            'package_weight'
        ]
        return self.defer(*decimal_fields_to_defer).order_by('-created_at')[:limit]


class OrderManager(models.Manager):
    """Custom manager for Order model"""

    def get_queryset(self):
        return OrderQuerySet(self.model, using=self._db)

    def safe_recent(self, limit=6):
        """Get recent orders safely without decimal conversion issues"""
        return self.get_queryset().safe_recent(limit)


class Order(models.Model):
    LOGISTICS_CHOICES = [
        ('ncm', 'NCM'),
        ('pick_and_drop', 'Pick and Drop'),
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

    # ✅ CUSTOM MANAGER: Use for safe decimal field handling
    objects = OrderManager()

    in_out = models.CharField(max_length=3, choices=IN_OUT_CHOICES, default='in')

    logistics = models.CharField(max_length=50, choices=LOGISTICS_CHOICES, blank=True, null=True)
    status = models.CharField(max_length=50, default='processing')  # Rename from order_status
    dispatch_date = models.DateTimeField(blank=True, null=True)
    order_number = models.CharField(max_length=50, unique=True)

    barcode = models.CharField(max_length=100, blank=True, null=True)  # ADD THIS

    # ✅ FIXED: Changed User to settings.AUTH_USER_MODEL
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_orders')
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    customer_name = models.CharField(max_length=255)
    customer_phone = models.CharField(max_length=20)
    customer_email = models.EmailField(blank=True)

    shipping_address = models.TextField()
    landmark = models.CharField(max_length=255, blank=True)
    order_from = models.CharField(max_length=50)
    order_status = models.CharField(max_length=50, default='processing')

    # ✅ NEW: ForeignKey to Setup for Payment Setup
    payment_setup = models.ForeignKey(
        'Setup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders_payment',
        limit_choices_to={'setup_type': 'payment'}
    )
    payment_method = models.CharField(max_length=50)
    payment_status = models.CharField(max_length=50, default='pending')

    # ForeignKey to Setup for Payment Status Setup
    payment_status_setup = models.ForeignKey(
        'Setup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders_payment_status',
        limit_choices_to={'setup_type': 'payment_status'}
    )

    # ✅ NEW: ForeignKey to Setup for Status Setup
    status_setup = models.ForeignKey(
        'Setup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders_status',
        limit_choices_to={'setup_type': 'status'}
    )

    # ✅ NEW: COD Collected amount
    cod_collected = models.DecimalField(max_digits=18, decimal_places=2, default=0, help_text="Amount collected as COD")

    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    shipping_charge = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    delivery_charge = models.DecimalField(max_digits=18, decimal_places=2, default=0, help_text="NCM delivery charge or logistics charge")
    expense_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, help_text="Other operational expenses")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    notes = models.TextField(blank=True)

    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    admin_notes = models.TextField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_partial_payment = models.BooleanField(default=False)
    partial_amount_paid = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    remaining_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    next_followup_date = models.DateField(null=True, blank=True, help_text="Scheduled next follow-up date")
    FOLLOWUP_TYPE_CHOICES = [
        ('call', 'Call'),
        ('whatsapp', 'WhatsApp'),
        ('email', 'Email'),
        ('visit', 'Visit'),
    ]
    followup_type = models.CharField(max_length=20, choices=FOLLOWUP_TYPE_CHOICES, null=True, blank=True)
    followup_assigned_to = models.ForeignKey(
        'accounts.CustomUser', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assigned_followups'
    )
    followup_done = models.BooleanField(default=False)

    def __str__(self):
        return self.order_number

     # ✅ ADD THESE NCM INTEGRATION FIELDS
    ncm_order_id = models.IntegerField(blank=True, null=True, unique=True, db_index=True)
    ncm_status = models.CharField(max_length=100, blank=True)
    ncm_created_at = models.DateTimeField(blank=True, null=True)

    # NCM Branch details (use these for API calls)
    ncm_from_branch = models.CharField(max_length=100, blank=True, default='TINKUNE')
    ncm_destination_branch = models.CharField(max_length=100, blank=True)

    # Delivery type for NCM
    NCM_DELIVERY_TYPES = [
        ('Door2Door', 'Door to Door'),
        ('Branch2Door', 'Branch to Door'),
        ('Door2Branch', 'Door to Branch'),
        ('Branch2Branch', 'Branch to Branch'),
    ]
    ncm_delivery_type = models.CharField(max_length=20, choices=NCM_DELIVERY_TYPES, default='Door2Door')

    # Weight for shipping calculation
    package_weight = models.DecimalField(max_digits=8, decimal_places=2, default=1.0, help_text="Weight in kg")

    # Pick and Drop Integration Fields
    pnd_order_id = models.CharField(max_length=100, blank=True, null=True, unique=True, db_index=True)
    pnd_status = models.CharField(max_length=100, blank=True)
    pnd_created_at = models.DateTimeField(blank=True, null=True)
    pnd_destination_branch = models.CharField(max_length=100, blank=True)
    pnd_tracking_url = models.URLField(max_length=500, blank=True)

    # Selected API config for logistics
    api_config = models.ForeignKey(
        'LogisticsAPIConfig',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
        help_text='Selected logistics API configuration'
    )

    # NCM Exchange Order Fields
    EXCHANGE_STATUS_CHOICES = [
        ('', 'Not Exchanged'),
        ('pending', 'Pending'),
        ('created', 'Created'),
        ('failed', 'Failed'),
    ]
    ncm_exchange_cust_order = models.IntegerField(blank=True, null=True, help_text="NCM exchange customer order ID")
    ncm_exchange_ven_order = models.IntegerField(blank=True, null=True, help_text="NCM exchange vendor order ID")
    exchange_status = models.CharField(max_length=20, choices=EXCHANGE_STATUS_CHOICES, blank=True, default='')

    def calculate_totals(self):
        """Calculate order totals based on items, discount, shipping, and tax"""
        from decimal import Decimal

        subtotal = sum(item.total for item in self.items.all()) or Decimal('0.00')
        after_discount = subtotal - self.discount_amount
        tax_amount = (after_discount * self.tax_percent) / 100
        self.total_amount = after_discount + tax_amount + self.shipping_charge

    def save(self, *args, **kwargs):
        """Validate and sanitize decimal fields before saving."""
        # Validate all decimal fields to prevent InvalidOperation errors
        self, _ = validate_decimal_fields(self)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-created_at']


class OrderItem(models.Model):
    """Enhanced Order Item Model"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('Product', on_delete=models.SET_NULL, null=True)
    product_variation = models.ForeignKey('ProductVariation', on_delete=models.SET_NULL, null=True, blank=True)

    product_name = models.CharField(max_length=255, default='')
    product_sku = models.CharField(max_length=100, blank=True, null=True)
    variation_name = models.CharField(max_length=255, blank=True, null=True)
    quantity = models.IntegerField(default=1)
    price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # Added for inventory reservation and backorder tracking
    reserved_qty = models.IntegerField(default=0, help_text="Units filled from real stock")
    backordered_qty = models.IntegerField(default=0, help_text="Units waiting on new stock")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product_name} x {self.quantity}"

    def save(self, *args, **kwargs):
        # Calculate total with proper decimal handling
        from .decimal_utils import validate_order_item_decimal_fields, safe_decimal

        # Ensure price and quantity are valid
        if self.price is None:
            self.price = Decimal('0')
        if self.quantity is None:
            self.quantity = 0

        # Calculate total safely
        try:
            self.total = safe_decimal(
                Decimal(str(self.price)) * self.quantity,
                max_digits=18,
                decimal_places=2,
                default='0'
            )
        except Exception as e:
            logger.error(f"Error calculating OrderItem total: {e}")
            self.total = Decimal('0')

        # Validate all decimal fields to prevent InvalidOperation errors
        self, _ = validate_order_item_decimal_fields(self)
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
    low_stock_threshold = models.IntegerField(default=0, help_text="Alert when variation stock falls to or below this value")
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
        ('redirected', 'Order Redirected'),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='activity_logs')
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)

    # ✅ FIXED: Changed User to settings.AUTH_USER_MODEL
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    field_name = models.CharField(max_length=100, blank=True)
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)

    description = models.TextField(blank=True)

    # Metadata for storing detailed information (e.g., old customer details during redirections)
    # ✅ Uses dict (callable) as default - Django will call it for each instance
    metadata = models.JSONField(default=dict, blank=True, null=True, help_text="Additional data for specific action types (e.g., old customer details for redirections)")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.order.order_number} - {self.get_action_type_display()}"

    class Meta:
        ordering = ['-created_at']


class OrderAdminNote(models.Model):
    """Track individual admin notes added to orders with history"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='admin_notes_list')
    content = models.TextField(help_text="Admin's note content")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_order_notes')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.order.order_number} - Note by {self.created_by.username} on {self.created_at.date()}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Order Admin Note"
        verbose_name_plural = "Order Admin Notes"


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
    return_reason = models.CharField(max_length=50, choices=RETURN_REASON_CHOICES, blank=True, default='')
    return_status = models.CharField(max_length=50, choices=RETURN_STATUS_CHOICES, default='pending')
    refund_type = models.CharField(max_length=50, choices=REFUND_TYPE_CHOICES, default='full_refund')

    # Financial
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    refund_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    restocking_fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)

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

    # Batch tracking - groups returns created together via bulk
    batch_id = models.CharField(max_length=50, blank=True, null=True, db_index=True,
                                help_text="Groups bulk-created returns together")

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
    price = models.DecimalField(max_digits=18, decimal_places=2)
    total = models.DecimalField(max_digits=18, decimal_places=2)

    # Return specific
    return_quantity = models.PositiveIntegerField(default=1)
    good_qty = models.PositiveIntegerField(default=0, help_text="Quantity in good/resellable condition")
    damaged_qty = models.PositiveIntegerField(default=0, help_text="Quantity that is damaged/defective")
    refund_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

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
        ('pick_and_drop', 'Pick and Drop'),
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

    def get_success_count(self):
        """Count orders that were successfully dispatched"""
        return self.items.filter(dispatch_status='success').count()

    def get_failed_count(self):
        """Count orders that failed (e.g. already dispatched)"""
        return self.items.filter(dispatch_status='failed').count()

    def get_not_found_count(self):
        """Count orders that were not found in system"""
        return self.items.filter(dispatch_status='not_found').count()

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

    DISPATCH_STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('not_found', 'Not Found'),
    ]

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
    dispatch_status = models.CharField(
        max_length=20,
        choices=DISPATCH_STATUS_CHOICES,
        default='not_found',
        db_index=True
    )
    failure_reason = models.CharField(max_length=255, blank=True, default='')
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


# ✅ Logistics API Configuration Model for dynamic API key management
class LogisticsAPIConfig(models.Model):
    LOGISTICS_PROVIDER_CHOICES = [
        ('ncm', 'NCM Logistics'),
        ('pick_and_drop', 'Pick and Drop'),
        ('other', 'Other'),
    ]

    api_name = models.CharField(max_length=100, help_text="Descriptive name for this API configuration")
    logistics_provider = models.CharField(max_length=50, choices=LOGISTICS_PROVIDER_CHOICES)
    api_key = models.CharField(max_length=500)
    api_secret = models.CharField(max_length=500, blank=True, default='', help_text="API Secret (required for Pick and Drop)")
    base_urls = models.JSONField(default=list, help_text="List of base URLs for this API")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='api_configs'
    )

    class Meta:
        ordering = ['logistics_provider', 'api_name']
        verbose_name = 'Logistics API Configuration'
        verbose_name_plural = 'Logistics API Configurations'

    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"{self.api_name} ({self.get_logistics_provider_display()}) - {status}"

    def get_primary_base_url(self):
        """Return the first base URL or empty string"""
        if self.base_urls and len(self.base_urls) > 0:
            return self.base_urls[0].rstrip('/')
        return ''

    def get_base_url_v2(self):
        """Return the second base URL (v2) or empty string"""
        if self.base_urls and len(self.base_urls) > 1:
            return self.base_urls[1].rstrip('/')
        return ''


# ✅ NEW: Setup/Configuration Model for dynamic dropdowns
class Setup(models.Model):
    SETUP_TYPES = [
        ('payment', 'Payment Setup'),
        ('status', 'Status Setup'),
        ('payment_status', 'Payment Status Setup'),
        ('order_source', 'Order Source'),
    ]

    setup_type = models.CharField(max_length=50, choices=SETUP_TYPES)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Default selection for this setup type in order forms")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Setup'
        verbose_name_plural = 'Setups'
        unique_together = ('setup_type', 'name')
        ordering = ['setup_type', 'name']

    def __str__(self):
        return f"{self.get_setup_type_display()} - {self.name}"


class StaffPerformance(models.Model):
    """Track staff member performance metrics"""
    staff_member = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='staff_performance')

    # Metrics
    total_orders = models.IntegerField(default=0)
    successful_orders = models.IntegerField(default=0)
    return_count = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    success_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Percentage 0-100")

    # Period tracking
    period_start = models.DateField(auto_now_add=True)
    period_end = models.DateField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def calculate_metrics(self):
        """Calculate performance metrics based on orders created by this staff member"""
        from decimal import Decimal

        orders = Order.objects.filter(created_by=self.staff_member, is_deleted=False)
        self.total_orders = orders.count()

        # Successful = delivered orders
        self.successful_orders = orders.filter(
            Q(order_status='delivered') | Q(status='delivered')
        ).count()

        # Calculate success rate
        if self.total_orders > 0:
            self.success_rate = Decimal(str((self.successful_orders / self.total_orders) * 100)).quantize(
                Decimal('0.01')
            )
        else:
            self.success_rate = Decimal('0')

        # Calculate total revenue from paid orders
        revenue_data = orders.filter(payment_status='paid').aggregate(
            total=Sum('total_amount')
        )
        self.total_revenue = revenue_data['total'] or Decimal('0')

        # Count returns
        self.return_count = ReturnRequest.objects.filter(
            order__created_by=self.staff_member
        ).count()

        self.save()

    def __str__(self):
        return f"{self.staff_member.get_full_name() or self.staff_member.username} - {self.success_rate}%"

    class Meta:
        verbose_name = 'Staff Performance'
        verbose_name_plural = 'Staff Performance Metrics'
        ordering = ['-success_rate', '-total_revenue']


# ==================== STAFF TARGET MODEL ====================

class StaffTarget(models.Model):
    """Track targets assigned to staff members"""
    TARGET_TYPE_CHOICES = [
        ('sales', 'Sales'),
        ('warehouse', 'Warehouse'),
    ]
    PERIOD_CHOICES = [
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ]

    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='targets')
    target_type = models.CharField(max_length=20, choices=TARGET_TYPE_CHOICES)
    target_value = models.DecimalField(max_digits=18, decimal_places=2)
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES, default='monthly')
    start_date = models.DateField()
    end_date = models.DateField()
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='targets_set')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Staff Target'
        verbose_name_plural = 'Staff Targets'

    def __str__(self):
        return f"{self.staff.get_full_name() or self.staff.username} - {self.get_target_type_display()} ({self.get_period_display()})"


# ==================== PURCHASE MANAGEMENT MODELS ====================

class Supplier(models.Model):
    """Supplier model for purchase management"""
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0,
                                          help_text="Opening balance (amount owed to supplier)")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']
        verbose_name = 'Supplier'
        verbose_name_plural = 'Suppliers'

    def get_total_purchases(self):
        return self.purchases.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')

    def get_total_paid(self):
        return self.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    def get_outstanding(self):
        return self.opening_balance + self.get_total_purchases() - self.get_total_paid()


class Purchase(models.Model):
    """Purchase invoice model"""
    PAYMENT_STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('partial', 'Partial'),
        ('unpaid', 'Unpaid'),
    ]

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='purchases')
    purchase_date = models.DateField(default=timezone.now)
    invoice_number = models.CharField(max_length=100, unique=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='unpaid')
    payment_method = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name='created_purchases')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.invoice_number} - {self.supplier.name}"

    class Meta:
        ordering = ['-purchase_date', '-created_at']
        verbose_name = 'Purchase'
        verbose_name_plural = 'Purchases'

    def get_total_paid(self):
        return self.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    def get_remaining(self):
        return self.total_amount - self.get_total_paid()

    def update_payment_status(self):
        paid = self.get_total_paid()
        if paid >= self.total_amount:
            self.payment_status = 'paid'
        elif paid > 0:
            self.payment_status = 'partial'
        else:
            self.payment_status = 'unpaid'
        self.save(update_fields=['payment_status'])

    def recalculate_total(self):
        total = self.purchase_items.aggregate(total=Sum('total'))['total'] or Decimal('0')
        self.total_amount = total
        self.save(update_fields=['total_amount'])


class PurchaseItem(models.Model):
    """Individual items in a purchase"""
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='purchase_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='purchase_items')
    product_variation = models.ForeignKey(
        'ProductVariation', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_items', help_text="Specific variation for variable products"
    )
    quantity = models.IntegerField(default=1)
    rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} x {self.quantity} @ {self.rate}"

    class Meta:
        verbose_name = 'Purchase Item'
        verbose_name_plural = 'Purchase Items'

    def save(self, *args, **kwargs):
        self.total = Decimal(str(self.rate)) * self.quantity
        super().save(*args, **kwargs)


class SupplierPayment(models.Model):
    """Payment records for suppliers"""
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='payments')
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='payments',
                                 null=True, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    payment_date = models.DateField(default=timezone.now)
    payment_method = models.CharField(max_length=50, blank=True)
    reference_no = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name='recorded_supplier_payments')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment of Rs.{self.amount} to {self.supplier.name}"

    class Meta:
        ordering = ['-payment_date', '-created_at']
        verbose_name = 'Supplier Payment'
        verbose_name_plural = 'Supplier Payments'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.purchase:
            self.purchase.update_payment_status()


class OrderFollowUp(models.Model):
    """Internal follow-up / comment system for orders"""
    FOLLOWUP_TYPE_CHOICES = [
        ('called_no_answer', 'Called - No Answer'),
        ('called_answered', 'Called - Answered'),
        ('whatsapp_sent', 'WhatsApp Sent'),
        ('email_sent', 'Email Sent'),
        ('custom_note', 'Custom Note'),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='followups'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='order_followups'
    )
    followup_type = models.CharField(
        max_length=30,
        choices=FOLLOWUP_TYPE_CHOICES,
        default='custom_note'
    )
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Order Follow-Up'
        verbose_name_plural = 'Order Follow-Ups'

    def __str__(self):
        return f"Follow-up on {self.order.order_number} by {self.user} ({self.get_followup_type_display()})"


class CompanySetup(models.Model):
    """Singleton model to store company branding and theme configuration."""

    THEME_CHOICES = [
        ('default',  'Default Blue'),
        ('dark',     'Dark Mode'),
        ('purple',   'Purple Elegance'),
        ('green',    'Forest Green'),
        ('orange',   'Sunset Orange'),
        ('red',      'Ruby Red'),
        ('teal',     'Teal Ocean'),
        ('indigo',   'Deep Indigo'),
    ]

    company_name = models.CharField(max_length=200, default='Trendy Shopping')
    tagline = models.CharField(max_length=300, blank=True, default='')
    logo = models.ImageField(upload_to='company/', blank=True, null=True)
    favicon = models.ImageField(upload_to='company/', blank=True, null=True)
    theme = models.CharField(max_length=30, choices=THEME_CHOICES, default='default')
    primary_color = models.CharField(max_length=7, default='#5e72e4')
    secondary_color = models.CharField(max_length=7, default='#825ee4')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Company Setup'
        verbose_name_plural = 'Company Setup'

    def __str__(self):
        return self.company_name

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)


# ==================== API Sync Settings ====================
class APISettings(models.Model):
    """Singleton model to store configurable API calling intervals and times."""

    order_sync_interval = models.PositiveIntegerField(
        default=14400,
        help_text="How often (in seconds) to auto-sync orders from NCM. Default: 14400 (4 hours).",
    )
    webhook_check_interval = models.PositiveIntegerField(
        default=30,
        help_text="How often (in seconds) to check for pending webhook updates. Default: 30.",
    )
    ncm_api_timeout = models.PositiveIntegerField(
        default=30,
        help_text="Timeout (in seconds) for each NCM API request. Default: 30.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'API Settings'
        verbose_name_plural = 'API Settings'

    def __str__(self):
        return f"API Settings (sync every {self.order_sync_interval}s)"

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)


# ==================== RTV STATUS (local, not from NCM) ====================
class RTVStatus(models.Model):
    """User-defined statuses for RTV orders (stored locally, not fetched from NCM)"""
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(
        max_length=7, default='#667eea',
        help_text="Hex color code (e.g. #667eea)"
    )
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'RTV Status'
        verbose_name_plural = 'RTV Statuses'

    def __str__(self):
        return self.name


# ==================== NCM RTV (Return to Vendor) ====================
class RTVOrder(models.Model):
    """Tracks orders returned to vendor via NCM API"""
    order_id = models.IntegerField(unique=True, help_text="NCM order ID")
    comment = models.TextField(blank=True, default='')
    vendor_return = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    rtv_marked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When NCM staff marked this order as vendor_return (from RTV comment added_time)",
    )
    # NCM order fields (populated from v2 vendor/orders API)
    receiver_name = models.CharField(max_length=255, blank=True, default='')
    receiver_phone = models.CharField(max_length=50, blank=True, default='')
    receiver_address = models.CharField(max_length=500, blank=True, default='')
    from_branch = models.CharField(max_length=100, blank=True, default='')
    to_branch = models.CharField(max_length=100, blank=True, default='')
    cod_charge = models.CharField(max_length=20, blank=True, default='')
    delivery_charge = models.CharField(max_length=20, blank=True, default='')
    tracking_id = models.CharField(max_length=100, blank=True, default='')
    last_status = models.CharField(max_length=100, blank=True, default='')
    product_description = models.TextField(blank=True, default='')
    rtv_status = models.ForeignKey(
        'RTVStatus',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rtv_orders',
        help_text="Locally assigned status for this RTV",
    )
    vendor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='rtv_orders',
    )
    api_config = models.ForeignKey(
        'LogisticsAPIConfig',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rtv_orders',
        help_text="Which API config (portal) this RTV belongs to",
    )

    class Meta:
        ordering = ['-rtv_marked_at', '-created_at']
        verbose_name = 'RTV Order'
        verbose_name_plural = 'RTV Orders'

    def __str__(self):
        return f"RTV #{self.order_id} by {self.vendor}"


class RTVFollowUp(models.Model):
    """Follow-up / note system for RTV (Return to Vendor) orders"""
    FOLLOWUP_TYPE_CHOICES = [
        ('called_no_answer', 'Called - No Answer'),
        ('called_answered', 'Called - Answered'),
        ('whatsapp_sent', 'WhatsApp Sent'),
        ('email_sent', 'Email Sent'),
        ('custom_note', 'Custom Note'),
    ]

    rtv_order = models.ForeignKey(
        RTVOrder,
        on_delete=models.CASCADE,
        related_name='followups',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='rtv_followups',
    )
    followup_type = models.CharField(
        max_length=30,
        choices=FOLLOWUP_TYPE_CHOICES,
        default='custom_note',
    )
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'RTV Follow-Up'
        verbose_name_plural = 'RTV Follow-Ups'

    def __str__(self):
        return f"RTV Follow-up on #{self.rtv_order.order_id} by {self.user}"


# ==================== MAINTENANCE MODE ====================
class MaintenanceMode(models.Model):
    """Singleton model to control system-wide maintenance mode."""
    is_enabled = models.BooleanField(default=False, help_text="When enabled, non-admin users see a maintenance overlay.")
    message = models.CharField(
        max_length=500,
        default='System is under maintenance. Please check back later.',
        blank=True,
        help_text="Custom message shown on the maintenance overlay."
    )
    enabled_at = models.DateTimeField(null=True, blank=True)
    enabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='maintenance_enabled',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Maintenance Mode'
        verbose_name_plural = 'Maintenance Mode'

    def __str__(self):
        return f"Maintenance Mode: {'ON' if self.is_enabled else 'OFF'}"

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)


class MaintenanceLog(models.Model):
    """Logs every enable/disable event of Maintenance Mode with timestamp."""
    ACTION_CHOICES = [
        ('enabled', 'Enabled'),
        ('disabled', 'Disabled'),
    ]
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='maintenance_logs',
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Maintenance Log'
        verbose_name_plural = 'Maintenance Logs'

    def __str__(self):
        return f"Maintenance {self.action} by {self.performed_by} at {self.timestamp}"

class GlobalNotice(models.Model):
    content = models.TextField(help_text="Rich text content for the notice")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    display_from = models.DateTimeField(null=True, blank=True, help_text="When to start showing the notice")
    display_until = models.DateTimeField(null=True, blank=True, help_text="When to stop showing the notice")

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notice {self.id} created at {self.created_at}"
