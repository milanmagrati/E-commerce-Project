from django.db import models, transaction
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
    def is_variable(self):
        """Check if this product is a variable product (has variations)."""
        return self.product_type == 'variable'

    @property
    def active_variations(self):
        """Variations a shopper may actually buy: active and not switched off.

        `is_active` is nullable on the model, so `exclude(is_active=False)`
        keeps both True and NULL rows.
        """
        return (self.variations
                .exclude(is_active=False)
                .exclude(status='inactive')
                .order_by('created_at'))

    @property
    def has_variations(self):
        """True only for a variable product that has at least one buyable
        variation — a 'variable' product with none behaves like a simple one."""
        return self.is_variable and self.active_variations.exists()

    @property
    def variation_price_range(self):
        """(min_price, max_price) across buyable variations, or (price, price)
        when there are none."""
        prices = [v.price for v in self.active_variations]
        if not prices:
            return (self.price, self.price)
        return (min(prices), max(prices))

    @property
    def in_stock_variation_exists(self):
        # Cheap, listing-page check: real units on hand and the row not switched
        # off. The committed-orders-aware figure is ProductVariation.available_stock,
        # used on the product page and at add-to-cart / checkout.
        return any(v.is_in_stock for v in self.active_variations)

    @property
    def storefront_available(self):
        """Whether the storefront should let this product be ordered at all."""
        if self.has_variations:
            return self.in_stock_variation_exists
        return self.available_stock > 0 or self.backorders_allowed

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

    # Manual status override (see services/status_override.py).
    # Staff can set an order's status by hand while the parcel is still sitting
    # at some earlier logistics status. These two fields let the sync paths tell
    # "staff decided this" apart from "stale value nobody touched", so a hand-set
    # status survives until the parcel actually moves.
    manual_status_override_at = models.DateTimeField(
        blank=True, null=True,
        help_text="When staff last set this order's status by hand"
    )
    manual_status_override_ncm_status = models.CharField(
        max_length=100, blank=True, default='',
        help_text="Raw logistics status in force at the time of that manual change"
    )

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

    @property
    def amount_due(self):
        """Amount still collectible from the customer (the COD figure).

        A partially paid order has already had `partial_amount_paid` collected
        up front, so what the courier must collect is `remaining_amount`, not
        `total_amount`. Every place that quotes "what will be collected" — the
        redirect COD field, the Possible Redirection match list — must use this
        rather than total_amount, or a partially paid order gets charged twice.
        """
        total = self.total_amount or Decimal('0.00')
        if not self.is_partial_payment:
            return total
        # remaining_amount is the field of record, but older rows exist with the
        # flag set and no figure behind it — derive one rather than quoting the
        # gross total, which would bill the customer for what they already paid.
        due = (
            self.remaining_amount
            if self.remaining_amount is not None
            else total - (self.partial_amount_paid or Decimal('0.00'))
        )
        # Never quote a negative amount to collect: an overpaid or corrupted row
        # would otherwise send a negative COD to the courier.
        return due if due > Decimal('0.00') else Decimal('0.00')

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

    @property
    def display_label(self):
        """Human-readable name for this variation, for the storefront and
        order lines. Prefers the explicit name, falls back to the SKU."""
        return self.variation_name or self.sku

    @property
    def is_in_stock(self):
        """Cheap on-hand check: units in stock and the row not marked
        out-of-stock. `available_stock` is the figure that also nets off
        unshipped orders — use that on the storefront and at checkout."""
        return self.stock > 0 and self.status != 'out_of_stock'

    # Order statuses at or past the point where dispatch has already
    # subtracted `stock` (or the order is void) — so their units are no
    # longer "waiting to ship" and must not be counted again.
    _SETTLED_ORDER_STATUSES = {
        'dispatched', 'packed', 'shipped', 'in_transit', 'in transit',
        'out_for_delivery', 'delivered', 'return', 'returned',
        'return_processing', 'redirected', 'cancelled', 'canceled',
        'rejected', 'trash', 'pickup_created', 'pickup created', 'inquiry',
    }

    @property
    def committed_qty(self):
        """Units of this variation already promised to orders that have not
        been dispatched yet (dispatch is the single place `stock` is
        decremented). Derived from the dashboard OrderItems every storefront
        order is mirrored into, so there is no reservation counter to drift."""
        from django.db.models import Sum
        from django.db.models.functions import Lower
        rows = (OrderItem.objects
                .filter(product_variation_id=self.pk)
                .annotate(_st=Lower('order__order_status'))
                .exclude(_st__in=self._SETTLED_ORDER_STATUSES)
                .aggregate(n=Sum('quantity')))
        return rows['n'] or 0

    @property
    def available_stock(self):
        """Units on hand minus what unshipped orders have already claimed."""
        return max(self.stock - self.committed_qty, 0)

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
    source_asset = models.ForeignKey(
        'MediaAsset', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='product_uses',
        help_text='Media Library asset this gallery image was copied from, if any.'
    )
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

    # When the logistics provider says the event actually happened, as opposed
    # to created_at = when we found out about it. NULL for locally-originated
    # actions (staff edits, redirections), which is the vast majority of rows.
    #
    # Keeping both is the point: NCM webhooks arrive late, and status sync runs
    # on page load / cron, so "NCM marked this on Jul 20" and "we recorded it
    # on Jul 30" are different facts and the UI shows both.
    event_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="Provider-reported event time (NCM added_time / webhook timestamp). "
                  "NULL for locally-originated actions.",
    )

    @property
    def effective_at(self):
        """Best known time this event happened — provider time if we have it."""
        return self.event_at or self.created_at

    def __str__(self):
        return f"{self.order.order_number} - {self.get_action_type_display()}"

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # Every read site filters by order and sorts newest-first.
            models.Index(fields=['order', '-created_at'], name='oal_order_created_idx'),
        ]


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

    # ── Outcome counters ─────────────────────────────────────────────────────
    # These are read once per row on the dispatch list, so they must not fire a
    # query per call. Resolution order:
    #   1. an annotation set by the list view (`annot_success_count`, ...)
    #   2. the prefetch cache (`prefetch_related('items')`)
    #   3. a single grouped query, cached on the instance
    def _status_counts(self):
        annotated = {}
        for status in ('success', 'failed', 'not_found'):
            value = getattr(self, f'annot_{status}_count', None)
            if value is not None:
                annotated[status] = value
        if len(annotated) == 3:
            return annotated

        cached = getattr(self, '_status_counts_cache', None)
        if cached is not None:
            return cached

        counts = {'success': 0, 'failed': 0, 'not_found': 0}
        prefetched = getattr(self, '_prefetched_objects_cache', None) or {}
        if 'items' in prefetched:
            for item in prefetched['items']:
                counts[item.dispatch_status] = counts.get(item.dispatch_status, 0) + 1
        else:
            for row in self.items.values('dispatch_status').annotate(c=models.Count('id')):
                counts[row['dispatch_status']] = row['c']

        self._status_counts_cache = counts
        return counts

    def get_linked_orders_count(self):
        """Count order IDs that resolved to a real Order row"""
        return self.items.filter(order__isnull=False).count()

    def get_unlinked_orders_count(self):
        """Count order IDs that couldn't be matched to an Order row"""
        return self.items.filter(order__isnull=True).count()

    def get_success_count(self):
        """Count orders that were successfully dispatched"""
        return self._status_counts().get('success', 0)

    def get_failed_count(self):
        """Count orders that failed (e.g. already dispatched)"""
        return self._status_counts().get('failed', 0)

    def get_not_found_count(self):
        """Count scanned IDs that were not found in the system"""
        return self._status_counts().get('not_found', 0)

    def get_item_count(self):
        """Number of scanned rows actually recorded for this batch"""
        value = getattr(self, 'annot_item_count', None)
        if value is not None:
            return value
        counts = self._status_counts()
        return sum(counts.get(s, 0) for s in ('success', 'failed', 'not_found'))

    def get_issue_count(self):
        """Every scanned ID that did NOT dispatch — failed + not found.

        This is the number the "Failed" column shows: a scanned ID that was
        never found in the system is just as much a failure to the packer as
        one rejected for being already dispatched, and counting only
        `dispatch_status='failed'` made those rows silently read as 0.
        """
        return self.get_failed_count() + self.get_not_found_count()

    def get_unrecorded_count(self):
        """Scanned IDs claimed by `total_orders` that have no DispatchItem row.

        Legacy batches created before per-item tracking existed have
        total_orders > 0 with zero items; surface that instead of rendering a
        misleading 0/0.
        """
        return max(0, (self.total_orders or 0) - self.get_item_count())

    def get_outcome(self):
        """Coarse batch outcome used for badges and row colouring."""
        if self.get_item_count() == 0:
            return 'unrecorded' if (self.total_orders or 0) > 0 else 'empty'
        success = self.get_success_count()
        issues = self.get_issue_count()
        if issues and success:
            return 'partial'
        if issues:
            return 'failed'
        return 'completed'

    def get_success_rate(self):
        """Percentage of recorded scans that dispatched successfully (0-100)."""
        total = self.get_item_count()
        if not total:
            return 0
        return round((self.get_success_count() * 100.0) / total)

    def get_failed_items(self):
        """All problem items (failed + not found), ready for display."""
        return [
            item for item in self.items.all()
            if item.dispatch_status != 'success'
        ]

    def log(self, event, message, level='info', item=None, order_ref='', user=None):
        """Append an audit entry for this batch. Never raises.

        The write runs in its own savepoint: these calls happen inside the
        dispatch's `transaction.atomic()` block, and swallowing a database error
        without rolling back to a savepoint would poison the outer transaction
        so every later query in the batch fails too.
        """
        try:
            with transaction.atomic():
                return DispatchLog.objects.create(
                    dispatch=self,
                    item=item,
                    level=level,
                    event=event,
                    message=(message or '')[:1000],
                    order_ref=(order_ref or (item.scanned_order_id if item else ''))[:100],
                    user=user if (user is not None and getattr(user, 'is_authenticated', False)) else None,
                )
        except Exception:  # logging must never break a dispatch
            return None

    def refresh_from_db(self, *args, **kwargs):
        # Drop memoised outcome counts so a reloaded row re-reads them.
        self._status_counts_cache = None
        return super().refresh_from_db(*args, **kwargs)

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

    # Machine-readable cause, so the UI can group/colour failures without
    # string-matching the human sentence in `failure_reason`.
    FAILURE_CODE_CHOICES = [
        ('already_dispatched', 'Already dispatched'),
        ('not_found', 'Order ID not found'),
        ('status_reverted', 'Status moved away from dispatched'),
        ('error', 'Processing error'),
    ]

    FAILURE_CODE_HINTS = {
        'already_dispatched': 'This order ID was scanned into an earlier batch that is still active. '
                              'Remove it from this batch, or restore/clear the earlier dispatch first.',
        'not_found': 'No order matched this ID by order number or barcode. '
                     'Check for a mis-scan, a trimmed prefix, or an order that was deleted.',
        'status_reverted': 'The order was dispatched in this batch but its status was later changed '
                           'away from "dispatched", so the stock deduction was rolled back.',
        'error': 'The order could not be processed. See the activity log for the underlying error.',
    }

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
    failure_code = models.CharField(
        max_length=32,
        choices=FAILURE_CODE_CHOICES,
        blank=True,
        default='',
        db_index=True
    )
    failed_at = models.DateTimeField(null=True, blank=True)
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

    def mark_failed(self, reason, code='error', save=True):
        """Flag this scan as failed with a reason + machine code."""
        self.dispatch_status = 'failed'
        self.failure_reason = (reason or '')[:255]
        self.failure_code = code if code in dict(self.FAILURE_CODE_CHOICES) else 'error'
        self.failed_at = timezone.now()
        if save:
            self.save(update_fields=['dispatch_status', 'failure_reason', 'failure_code', 'failed_at'])
        return self

    def mark_not_found(self, reason='', save=True):
        """Flag this scan as unmatched — it is a failure, just a different cause."""
        self.dispatch_status = 'not_found'
        self.failure_reason = (reason or 'Order ID not found in the system')[:255]
        self.failure_code = 'not_found'
        self.failed_at = timezone.now()
        if save:
            self.save(update_fields=['dispatch_status', 'failure_reason', 'failure_code', 'failed_at'])
        return self

    def mark_success(self, save=True):
        """Flag this scan as dispatched and clear any earlier failure detail."""
        self.dispatch_status = 'success'
        self.failure_reason = ''
        self.failure_code = ''
        self.failed_at = None
        if save:
            self.save(update_fields=['dispatch_status', 'failure_reason', 'failure_code', 'failed_at'])
        return self

    @property
    def is_problem(self):
        return self.dispatch_status != 'success'

    def get_failure_reason_display(self):
        """Human sentence for the reason column — never blank for a problem row."""
        if self.dispatch_status == 'success':
            return ''
        if self.failure_reason:
            return self.failure_reason
        if self.dispatch_status == 'not_found':
            return 'Order ID not found in the system'
        return 'Failed — no reason was recorded for this scan'

    def get_failure_hint(self):
        """Actionable next step for this failure cause, if we know one."""
        code = self.failure_code
        if not code and self.dispatch_status == 'not_found':
            code = 'not_found'
        return self.FAILURE_CODE_HINTS.get(code, '')


class DispatchLog(models.Model):
    """Append-only audit trail for a dispatch batch.

    Every scan outcome, stock movement and later status change writes a row
    here so the detail page can explain exactly what happened and when.
    """

    LEVEL_CHOICES = [
        ('info', 'Info'),
        ('success', 'Success'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ]

    EVENT_CHOICES = [
        ('batch_created', 'Batch created'),
        ('order_dispatched', 'Order dispatched'),
        ('order_failed', 'Order failed'),
        ('order_not_found', 'Order not found'),
        ('stock_oversold', 'Stock oversold'),
        ('status_reverted', 'Status reverted'),
        ('batch_completed', 'Batch completed'),
        ('batch_error', 'Batch error'),
    ]

    dispatch = models.ForeignKey(
        Dispatch,
        on_delete=models.CASCADE,
        related_name='logs'
    )
    item = models.ForeignKey(
        DispatchItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs'
    )
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='info', db_index=True)
    event = models.CharField(max_length=32, choices=EVENT_CHOICES, default='batch_created', db_index=True)
    message = models.TextField()
    order_ref = models.CharField(max_length=100, blank=True, default='')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dispatch_logs'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['created_at', 'id']
        verbose_name = 'Dispatch Log'
        verbose_name_plural = 'Dispatch Logs'
        indexes = [
            models.Index(fields=['dispatch', 'created_at']),
        ]

    def __str__(self):
        return f"[{self.level}] {self.event} — {self.dispatch_id}"

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
        ('followup_status', 'Follow-up Status'),
    ]

    setup_type = models.CharField(max_length=50, choices=SETUP_TYPES)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=50, blank=True, null=True, help_text="Hex color code for badges")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Default selection for this setup type in order forms")
    sort_order = models.IntegerField(default=0, help_text="Manual drag-and-drop display order within a setup type")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Setup'
        verbose_name_plural = 'Setups'
        unique_together = ('setup_type', 'name')
        ordering = ['setup_type', 'sort_order', 'name']

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
        from django.core.cache import cache
        cache.delete('ctx_company_setup')


class LandingPageSettings(models.Model):
    """Singleton model holding all editable copy/config for the public marketing
    landing page (templates/landing.html). Repeatable content (stat rows, brand
    logos, feature/integration cards) lives in related models below."""

    # Hero
    hero_pill_text = models.CharField(max_length=100, blank=True, default='The commerce OS for Nepal')
    hero_headline = models.CharField(max_length=200, blank=True, default='Run your entire shop from one system')
    hero_subtext = models.TextField(
        blank=True,
        default='Online and offline selling, inventory, courier logistics, customer messaging '
                'and your team — one connected infrastructure instead of six disconnected tools.'
    )
    hero_cta_primary_text = models.CharField(max_length=50, blank=True, default='Start selling')
    hero_cta_primary_link = models.CharField(max_length=200, blank=True, default='')
    hero_cta_secondary_text = models.CharField(max_length=50, blank=True, default='Explore platform')
    hero_cta_secondary_link = models.CharField(max_length=200, blank=True, default='#platform')
    hero_note_text = models.CharField(
        max_length=200, blank=True,
        default='Nepali time zone, rupee amounts and local courier networks out of the box'
    )

    # Trusted-by brands
    trusted_label = models.CharField(max_length=150, blank=True, default='Trusted by growing Nepali brands')

    # Solutions section (two fixed split cards)
    solutions_eyebrow = models.CharField(max_length=50, blank=True, default='Solutions')
    solutions_heading = models.CharField(max_length=200, blank=True, default='Choose how you want to sell')
    solutions_subtext = models.TextField(
        blank=True,
        default='Whether you sell through Instagram DMs, a storefront, or a counter in Kathmandu '
                '— it runs on the same stock and the same orders.'
    )
    solution_card1_title = models.CharField(max_length=100, blank=True, default='Online sellers')
    solution_card1_text = models.TextField(
        blank=True,
        default='Storefront, Instagram and Facebook messaging, two-way Google Sheets sync, '
                'courier handoff and COD tracking — all against one set of stock.'
    )
    solution_card2_title = models.CharField(max_length=100, blank=True, default='Physical stores')
    solution_card2_text = models.TextField(
        blank=True,
        default='POS counter billing, barcode-driven product lookup and per-branch order tracking '
                '— sharing the same inventory as everything you sell online.'
    )

    # Platform section header (cards are LandingFeatureCard, section='platform')
    platform_eyebrow = models.CharField(max_length=50, blank=True, default='Platform')
    platform_heading = models.CharField(max_length=200, blank=True, default='A full selling infrastructure')
    platform_subtext = models.CharField(max_length=250, blank=True, default='Every part of the operation — not just a storefront.')

    # Integrations section header (cards are LandingFeatureCard, section='integration')
    integrations_eyebrow = models.CharField(max_length=50, blank=True, default='Integrations')
    integrations_heading = models.CharField(max_length=200, blank=True, default='Connected to the tools you already use')

    # CTA band
    cta_heading = models.CharField(max_length=200, blank=True, default='Ready to run it all from one place?')
    cta_subtext = models.CharField(max_length=250, blank=True, default='Sign in to your dashboard, or browse the storefront to see it in action.')
    cta_primary_text = models.CharField(max_length=50, blank=True, default='Get started')
    cta_secondary_text = models.CharField(max_length=50, blank=True, default='Visit the store')

    # Footer / verified business details
    business_address = models.CharField(max_length=255, blank=True, default='')
    business_pan = models.CharField(max_length=100, blank=True, default='')
    business_contact = models.CharField(max_length=150, blank=True, default='')

    # Footer social links
    social_instagram = models.URLField(max_length=300, blank=True, default='')
    social_facebook = models.URLField(max_length=300, blank=True, default='')
    social_tiktok = models.URLField(max_length=300, blank=True, default='')
    social_whatsapp = models.URLField(max_length=300, blank=True, default='')
    social_email = models.EmailField(max_length=150, blank=True, default='')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Landing Page Setup'
        verbose_name_plural = 'Landing Page Setup'

    def __str__(self):
        return 'Landing Page Settings'

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)
        from django.core.cache import cache
        cache.delete('landing_page_settings')


class LandingStatItem(models.Model):
    """A row in the hero's mock stats panel."""

    GROUP_CHOICES = [
        ('primary', 'Top row (dot + right-aligned value)'),
        ('secondary', 'Stock row (below divider)'),
    ]
    DOT_CHOICES = [
        ('amber', 'Amber'),
        ('green', 'Green'),
        ('blue', 'Blue'),
        ('', 'None'),
    ]

    settings = models.ForeignKey(LandingPageSettings, on_delete=models.CASCADE, related_name='stat_items')
    label = models.CharField(max_length=100)
    value = models.CharField(max_length=100, blank=True, default='')
    dot_color = models.CharField(max_length=10, choices=DOT_CHOICES, blank=True, default='')
    group = models.CharField(max_length=10, choices=GROUP_CHOICES, default='primary')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['group', 'order', 'id']
        verbose_name = 'Landing Stat Item'

    def __str__(self):
        return self.label


class LandingBrandLogo(models.Model):
    """A logo/name shown in the 'Trusted by growing Nepali brands' row."""

    settings = models.ForeignKey(LandingPageSettings, on_delete=models.CASCADE, related_name='brand_logos')
    name = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='landing/brands/', blank=True, null=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'Landing Brand Logo'

    def __str__(self):
        return self.name


class LandingFeatureCard(models.Model):
    """A card in either the Platform features grid or the Integrations grid."""

    SECTION_CHOICES = [
        ('platform', 'Platform feature'),
        ('integration', 'Integration'),
    ]

    settings = models.ForeignKey(LandingPageSettings, on_delete=models.CASCADE, related_name='feature_cards')
    section = models.CharField(max_length=15, choices=SECTION_CHOICES, default='platform')
    icon = models.CharField(max_length=60, default='fas fa-star', help_text='Font Awesome class, e.g. "fas fa-box"')
    title = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True, default='')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['section', 'order', 'id']
        verbose_name = 'Landing Feature Card'

    def __str__(self):
        return self.title


# ==================== API Sync Settings ====================
class APISettings(models.Model):
    """Singleton model to store configurable API calling intervals and times."""

    order_sync_interval = models.PositiveIntegerField(
        default=900,
        help_text="How often (in seconds) the SERVER runs the background NCM bulk status "
                  "sync. This is the real API cadence - it costs NCM requests. Default: 900 (15 min).",
    )
    page_refresh_interval = models.PositiveIntegerField(
        default=30,
        help_text="How often (in seconds) an open page re-reads order status from the local "
                  "database to repaint badges. Costs no NCM requests. Default: 30.",
    )
    ncm_api_timeout = models.PositiveIntegerField(
        default=30,
        help_text="Timeout (in seconds) for each NCM API request. Default: 30.",
    )
    bulk_sync_included_statuses = models.JSONField(
        default=list,
        blank=True,
        help_text="Order statuses eligible for the background NCM bulk status sync. Empty = use default (sync all except cancelled/delivered/return/returned/return_initiated/return_approved).",
    )
    bulk_sync_fetch_event_times = models.BooleanField(
        default=True,
        help_text="During background sync, fetch NCM's real event time for each order whose "
                  "status changed (one extra API request per changed order). When off, activity "
                  "log entries are timestamped with the sync run's own time instead.",
    )

    # --- Background sync scheduler state (see ncm/scheduler.py) -------------
    # This project runs on shared hosting with no Celery beat and no crontab,
    # so the bulk sync is driven by whichever web request first notices it is
    # due. These three columns are the cross-process lock and clock that makes
    # that safe: they are written with queryset.update() (never .save()) so the
    # claim is a single atomic statement and so `updated_at` keeps meaning
    # "when an admin last edited these settings".
    last_bulk_sync_started_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the most recent background bulk sync began. The due-check "
                  "measures from here (start-to-start), so a run that outlasts the "
                  "interval can't immediately retrigger itself.",
    )
    last_bulk_sync_finished_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the most recent background bulk sync finished. Display only.",
    )
    bulk_sync_running_since = models.DateTimeField(
        null=True, blank=True,
        help_text="Non-null while a bulk sync holds the lock. Bumped periodically by the "
                  "running sync so a long run isn't mistaken for a crashed one; a value "
                  "older than the stale window is treated as abandoned and taken over.",
    )
    last_bulk_sync_summary = models.JSONField(
        default=dict, blank=True,
        help_text="Result of the most recent background bulk sync "
                  "(total_orders / updated_count / errors).",
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

    # Where rtv_marked_at came from. Ranked: a lower-ranked source must never
    # overwrite a higher-ranked one (see apply_rtv_marked_at in
    # services/ncm_service.py). 'order_created' is the known-wrong legacy
    # value — the sync used to fall back to NCM's *order creation* date, which
    # then stuck forever because every repair query filtered on
    # rtv_marked_at IS NULL. Tracking provenance is what makes those rows
    # findable and fixable instead of permanent.
    SOURCE_UNKNOWN = ''
    SOURCE_ORDER_CREATED = 'order_created'
    SOURCE_STATUS_TIMELINE = 'status_timeline'
    SOURCE_NCM_STAFF_COMMENT = 'ncm_staff_comment'
    SOURCE_COMMENT = 'comment'
    SOURCE_WEBHOOK = 'webhook'
    SOURCE_MANUAL = 'manual'

    RTV_MARKED_SOURCES = [
        (SOURCE_UNKNOWN, 'Unknown'),
        (SOURCE_ORDER_CREATED, 'NCM order created_date (wrong — legacy)'),
        (SOURCE_STATUS_TIMELINE, 'NCM status timeline return step (approximate)'),
        (SOURCE_NCM_STAFF_COMMENT, 'Latest NCM Staff comment (approximate)'),
        (SOURCE_COMMENT, 'NCM "RTV marked" comment'),
        (SOURCE_WEBHOOK, 'NCM order_marked_rtv webhook'),
        (SOURCE_MANUAL, 'Marked locally via this app'),
    ]

    SOURCE_RANK = {
        SOURCE_UNKNOWN: 0,
        SOURCE_ORDER_CREATED: 1,
        SOURCE_STATUS_TIMELINE: 2,
        SOURCE_NCM_STAFF_COMMENT: 3,
        SOURCE_COMMENT: 4,
        SOURCE_WEBHOOK: 4,
        SOURCE_MANUAL: 4,
    }

    TRUSTED_SOURCES = (SOURCE_COMMENT, SOURCE_WEBHOOK, SOURCE_MANUAL)

    order_id = models.IntegerField(unique=True, help_text="NCM order ID")
    comment = models.TextField(blank=True, default='')
    vendor_return = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    rtv_marked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When NCM staff marked this order as vendor_return (from RTV comment added_time)",
    )
    rtv_marked_at_source = models.CharField(
        max_length=20, choices=RTV_MARKED_SOURCES, default=SOURCE_UNKNOWN,
        blank=True, db_index=True,
        help_text="Where rtv_marked_at came from; drives which rows get re-verified",
    )
    rtv_marked_at_checked_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last time we asked NCM for this order's RTV date. Repair passes take "
                  "the least-recently-checked rows so a bounded batch size converges.",
    )
    ncm_created_date = models.DateTimeField(
        null=True, blank=True,
        help_text="NCM's order creation date. Kept for filtering/reference only — "
                  "this is NOT when the RTV was marked.",
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

    @property
    def rtv_marked_at_is_trusted(self):
        """True when rtv_marked_at came from an authoritative source.

        Untrusted values are still displayed (flagged approximate) rather than
        hidden, but they stay in the repair queue until NCM confirms them.
        """
        return bool(self.rtv_marked_at) and self.rtv_marked_at_source in self.TRUSTED_SOURCES


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
        from django.core.cache import cache
        cache.delete('ctx_maintenance_mode')


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

    DISPLAY_FREQ_CHOICES = [
        ('every_refresh', 'Every Refresh'),
        ('once_per_session', 'Once Per Session'),
        ('once_per_hour', 'Once Per Hour'),
        ('once_per_day', 'Once Per Day'),
        ('once_per_week', 'Once Per Week'),
        ('once_only', 'Once Only'),
    ]
    display_frequency = models.CharField(max_length=20, choices=DISPLAY_FREQ_CHOICES, default='every_refresh', help_text="How often to show the notice to a user")

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notice {self.id} created at {self.created_at}"


class FollowUp(models.Model):
    """Model to store Follow-ups data"""
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    lead_source = models.CharField(max_length=100, blank=True)
    # Legacy single-product FK (kept for backward compat, use products M2M instead)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='follow_ups_single')
    # Multiple products (preferred)
    products = models.ManyToManyField(Product, blank=True, related_name='follow_ups_multi')
    product_variations = models.ManyToManyField('ProductVariation', blank=True, related_name='follow_ups_multi')
    followup_1 = models.CharField(max_length=255, blank=True)
    followup_2 = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=100, blank=True)
    remarks = models.TextField(blank=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.IntegerField(default=1)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.phone}"

    @property
    def latest_followup_log(self):
        # Returns the latest log overall that is a followup note or entry created
        for log in self.logs.all():
            if log.field_changed == 'Entry Created' or log.field_changed.startswith('Followup'):
                return log
        return None

    @property
    def all_followup_logs(self):
        return self.logs.all()

    @property
    def followup_count(self):
        return self.logs.filter(field_changed__startswith='Followup').count()

    def get_all_products(self):
        """Return M2M products if any, else fall back to the legacy FK product."""
        m2m = list(self.products.all())
        if m2m:
            return m2m
        if self.product:
            return [self.product]
        return []

    def get_formatted_products(self):
        """Return formatted products and variations for frontend rendering."""
        formatted = []
        for p in self.products.all():
            formatted.append({
                'id': str(p.id),
                'name': p.name,
                'price': p.price
            })
        for v in self.product_variations.all():
            formatted.append({
                'id': f"v_{v.id}",
                'name': f"{v.product.name} - {v.variation_name or v.sku}",
                'price': v.price
            })
        if not formatted and self.product:
            formatted.append({
                'id': str(self.product.id),
                'name': self.product.name,
                'price': self.product.price
            })
        return formatted


class ContentAccount(models.Model):
    account_id = models.CharField(max_length=100, blank=True, null=True, verbose_name='Id')
    user_name = models.CharField(max_length=100, blank=True, null=True)
    gmail = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    password = models.CharField(max_length=100, blank=True, null=True)
    managed_by = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    account_type = models.CharField(max_length=100, blank=True, null=True, verbose_name='Type')
    order = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.account_id} - {self.user_name}"

    class Meta:
        ordering = ['order', '-created_at']
        verbose_name_plural = 'Content Accounts'
class StaffReport(models.Model):
    staff_name = models.CharField(max_length=255)
    report_date = models.CharField(max_length=255)
    platform = models.CharField(max_length=255, blank=True, null=True)
    no_of_posts = models.TextField(blank=True, null=True)
    views = models.TextField(blank=True, null=True)
    likes = models.TextField(blank=True, null=True)
    comments = models.TextField(blank=True, null=True)
    follower_growth = models.TextField(blank=True, null=True)
    punctuality = models.TextField(blank=True, null=True)
    behaviour = models.TextField(blank=True, null=True)
    leave_and_wfh = models.TextField(blank=True, null=True)
    notes_remarks = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.staff_name} - {self.report_date}"

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Staff Reports'

class FollowUpLog(models.Model):
    follow_up = models.ForeignKey(FollowUp, on_delete=models.CASCADE, related_name='logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    field_changed = models.CharField(max_length=50)
    old_value = models.TextField(blank=True, null=True)
    new_value = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.follow_up.name} - {self.field_changed} updated"


class FollowUpPresence(models.Model):
    """Temporary storage for presence indicators (typing/viewing) on Follow-ups"""
    followup = models.ForeignKey(FollowUp, on_delete=models.CASCADE, related_name='presences')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    action = models.CharField(max_length=20, choices=[('viewing', 'Viewing'), ('typing', 'Typing')])
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('followup', 'user')
        indexes = [
            models.Index(fields=['last_seen']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.action} on {self.followup.id}"


class MediaCategory(models.Model):
    """User-defined category for organizing the shared Media Library."""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Media Categories'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base_slug = slugify(self.name) or 'category'
            slug = base_slug
            i = 1
            while MediaCategory.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                i += 1
                slug = f"{base_slug}-{i}"
            self.slug = slug
        super().save(*args, **kwargs)


class MediaAsset(models.Model):
    """A reusable image in the shared Media Library, pickable from any product's gallery."""
    image = models.ImageField(upload_to='media_library/%Y/%m/')
    title = models.CharField(max_length=255, blank=True)
    category = models.ForeignKey(MediaCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='assets')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='media_assets')
    file_size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Media Asset'
        verbose_name_plural = 'Media Assets'

    def __str__(self):
        return self.title or f"Media {self.pk}"
