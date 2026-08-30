import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.contrib.auth.hashers import check_password, is_password_usable, make_password
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from dashboard.models import Product


class StoreCustomer(models.Model):
    """A shopper account on the storefront.

    Deliberately NOT `settings.AUTH_USER_MODEL`: that table is the staff user,
    with the RBAC booleans, roles and payroll hanging off it, and a shopper
    must never end up in it. Shoppers are authenticated entirely inside the
    session (`store_customer_id`, see `store/customer_auth.py`), so
    `request.user` stays untouched and no admin decorator can mistake a
    customer for staff.

    Signing in stays optional everywhere: carts, orders and reviews all still
    work for a guest keyed only by `session_key`.
    """
    full_name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, unique=True, db_index=True)
    password = models.CharField(max_length=128)

    # Saved delivery details, used to prefill the order form on a later visit.
    district = models.CharField(max_length=100, blank=True, default='')
    courier_branch = models.CharField(max_length=120, blank=True, default='')
    courier_branch_code = models.CharField(max_length=40, blank=True, default='')
    address = models.CharField(max_length=300, blank=True, default='')

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'store customer'
        verbose_name_plural = 'store customers'

    def __str__(self):
        return '%s <%s>' % (self.full_name, self.email)

    # ── passwords, through the same hashers Django uses for staff ──
    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        """Verify, upgrading the stored hash in place when Django's preferred
        hasher has moved on since the account was created."""
        def setter(new_hash):
            self.password = new_hash
            self.save(update_fields=['password'])
        return check_password(raw_password, self.password, setter)

    def set_unusable_password(self):
        self.password = make_password(None)

    @property
    def has_usable_password(self):
        return is_password_usable(self.password)

    @property
    def first_name(self):
        return (self.full_name or '').strip().split(' ')[0]

    @property
    def initial(self):
        name = (self.full_name or '').strip()
        return name[0].upper() if name else '?'


class ProductReview(models.Model):
    """A product review. Signing in is optional on the storefront, so a review
    is owned by a `customer` when one is signed in and by `session_key` plus a
    typed `guest_name` otherwise. `user` is only ever set for staff."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='store_reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    customer = models.ForeignKey('StoreCustomer', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='reviews')
    session_key = models.CharField(max_length=40, blank=True, default='', db_index=True)
    guest_name = models.CharField(max_length=120, blank=True, default='')
    rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.display_name} - {self.product.name} ({self.rating})'

    @property
    def display_name(self):
        if self.guest_name:
            return self.guest_name
        if self.customer:
            return self.customer.full_name
        if self.user:
            return self.user.first_name or self.user.username
        return 'Anonymous'

    @property
    def initial(self):
        name = self.display_name.strip()
        return name[0].upper() if name else '?'


class Cart(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    customer = models.ForeignKey('StoreCustomer', on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='carts')
    session_key = models.CharField(max_length=40, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Cart {self.pk} - {self.user or self.session_key}'

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def subtotal(self):
        return sum(item.line_total for item in self.items.all())


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='store_cart_items')
    quantity = models.PositiveIntegerField(default=1)
    selected_variant = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        unique_together = ('cart', 'product')

    def __str__(self):
        return f'{self.product.name} x {self.quantity}'

    @property
    def line_total(self):
        return self.product.price * self.quantity


class DiscountCode(models.Model):
    """Coupon a shopper can type into the order form's 'Discount Code' box."""
    DISCOUNT_TYPES = [
        ('percent', 'Percentage off'),
        ('fixed', 'Fixed amount off'),
    ]

    code = models.CharField(max_length=40, unique=True)
    description = models.CharField(max_length=200, blank=True, default='')
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPES, default='percent')
    value = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                help_text="Percent (e.g. 10) or rupees off (e.g. 200)")
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                       help_text="Cap for percentage codes. Blank = no cap.")
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    free_delivery = models.BooleanField(default=False, help_text="Also waive the delivery charge")
    is_active = models.BooleanField(default=True)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = unlimited")
    used_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        super().save(*args, **kwargs)

    def check_valid(self, subtotal):
        """Return (ok, message). `subtotal` is the pre-discount goods total."""
        now = timezone.now()
        if not self.is_active:
            return False, 'This code is no longer active.'
        if self.valid_from and now < self.valid_from:
            return False, 'This code is not active yet.'
        if self.valid_to and now > self.valid_to:
            return False, 'This code has expired.'
        if self.usage_limit is not None and self.used_count >= self.usage_limit:
            return False, 'This code has reached its usage limit.'
        if Decimal(str(subtotal)) < self.min_order_amount:
            return False, f'Valid on orders of Rs. {self.min_order_amount:.0f} or more.'
        return True, ''

    def discount_for(self, subtotal):
        """Rupees off `subtotal`, never more than the subtotal itself."""
        subtotal = Decimal(str(subtotal))
        if self.discount_type == 'percent':
            amount = subtotal * self.value / Decimal('100')
            if self.max_discount is not None:
                amount = min(amount, self.max_discount)
        else:
            amount = self.value
        return min(amount, subtotal).quantize(Decimal('0.01'))


class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]
    ORDER_TYPE_CHOICES = [
        ('confirmed', 'Confirm Order'),
        ('inquiry', 'Inquiry Only'),
    ]
    # Checkout works signed in or not. A signed-in shopper's order carries
    # `customer`; a guest's is tied to the browser session plus the phone
    # number typed (which is what the track-order lookup matches on).
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='store_orders')
    customer = models.ForeignKey('StoreCustomer', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='orders')
    session_key = models.CharField(max_length=40, blank=True, default='', db_index=True)
    order_number = models.CharField(max_length=36, unique=True, editable=False)
    full_name = models.CharField(max_length=200, blank=True)
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default='confirmed')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_code = models.CharField(max_length=40, blank=True, default='')
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping_address = models.TextField()
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    district = models.CharField(max_length=100, blank=True, default='')
    courier_branch = models.CharField(max_length=120, blank=True, default='')
    courier_branch_code = models.CharField(max_length=40, blank=True, default='')
    note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.order_number

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = uuid.uuid4().hex[:12].upper()
        super().save(*args, **kwargs)

    @property
    def is_inquiry(self):
        return self.order_type == 'inquiry'


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, related_name='store_order_items')
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    selected_variant = models.CharField(max_length=500, blank=True, default='')
    reserved_qty = models.IntegerField(default=0, help_text="Units filled from real stock")
    backordered_qty = models.IntegerField(default=0, help_text="Units waiting on new stock")

    def __str__(self):
        return f'{self.product.name if self.product else "Deleted"} x {self.quantity}'

    @property
    def line_total(self):
        return self.price * self.quantity


class Wishlist(models.Model):
    """Wishlist entry, owned by a signed-in `customer` or, for a guest, by the
    browser `session_key`. `user` is kept for rows written before customer
    accounts existed."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             null=True, blank=True, related_name='store_wishlist')
    customer = models.ForeignKey('StoreCustomer', on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='wishlist')
    session_key = models.CharField(max_length=40, blank=True, default='', db_index=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='store_wishlist_items')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        # No unique constraint: the owner is either `user` or `session_key`, and
        # MySQL (this project's DB) refuses conditional unique constraints and
        # treats NULL user rows as distinct anyway. toggle_wishlist() dedupes.
        indexes = [
            models.Index(fields=['session_key', 'product']),
        ]

    def __str__(self):
        return f'{self.user or self.session_key} - {self.product.name}'


class Page(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, help_text="URL-friendly name (e.g., 'about-us')")
    content = models.TextField(help_text="HTML content of the page")
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class DeliverySetting(models.Model):
    """Site-wide delivery defaults — the fallback used whenever no
    :class:`DeliveryCharge` rule matches the district the shopper picked.

    Single row, always read through :meth:`get_solo`, so the storefront never
    has to care whether an administrator has visited the setup page yet.
    """

    inside_valley_charge = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text="Delivery fee for the Kathmandu Valley districts listed below.")
    default_charge = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('100'),
        help_text="Fee used for any district that has no rule of its own.")
    free_delivery_threshold = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('500'),
        help_text="Orders at or above this subtotal ship free, in districts "
                  "that have no rule of their own. 0 disables it.")
    valley_districts = models.CharField(
        max_length=255, default='KATHMANDU, LALITPUR, BHAKTAPUR',
        help_text="Comma-separated districts treated as inside the valley.")
    default_delivery_time = models.CharField(
        max_length=140, blank=True, default='Delivered within 3-5 days',
        help_text="Shown on the order form when a rule has no time of its own.")
    default_delivery_time_np = models.CharField(
        max_length=180, blank=True, default='',
        help_text="Nepali version of the line above (optional).")
    show_covered_areas = models.BooleanField(
        default=True, help_text="Show the branch's covered areas on the order form.")
    show_delivery_time = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Delivery setting'
        verbose_name_plural = 'Delivery settings'

    def __str__(self):
        return 'Delivery settings'

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    @property
    def valley_district_set(self):
        return {
            part.strip().upper()
            for part in (self.valley_districts or '').split(',')
            if part.strip()
        }


class DeliveryCharge(models.Model):
    """One delivery rule: what a district (optionally a single courier branch
    inside it) costs, how long it takes, and which areas it reaches.

    A rule with a blank ``branch_code`` covers the whole district; a rule with
    one set beats it for that branch only. Districts and branch codes are held
    uppercase so lookups match whatever casing NCM returns.
    """

    district = models.CharField(max_length=80, db_index=True)
    branch_code = models.CharField(
        max_length=40, blank=True, default='',
        help_text="Blank = the rule covers every branch in the district.")
    branch_name = models.CharField(max_length=140, blank=True, default='')
    charge = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    free_above = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Subtotal at or above which this rule ships free. "
                  "Blank = this district is always charged.")
    delivery_time = models.CharField(
        max_length=140, blank=True, default='',
        help_text="e.g. 'Delivered within 3-4 days'.")
    delivery_time_np = models.CharField(
        max_length=180, blank=True, default='',
        help_text="e.g. '३-४ दिन भित्र डेलिभरी हुनेछ'.")
    covered_areas = models.TextField(
        blank=True, default='',
        help_text="Comma-separated areas. Blank = fall back to the courier's own list.")
    note = models.CharField(max_length=200, blank=True, default='')
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['district', 'branch_name', 'branch_code']
        unique_together = [('district', 'branch_code')]
        indexes = [models.Index(fields=['district', 'branch_code'])]
        verbose_name = 'Delivery charge'
        verbose_name_plural = 'Delivery charges'

    def __str__(self):
        if self.branch_code:
            return f'{self.district} / {self.branch_name or self.branch_code}'
        return self.district

    def save(self, *args, **kwargs):
        self.district = (self.district or '').strip().upper()
        self.branch_code = (self.branch_code or '').strip().upper()
        self.branch_name = (self.branch_name or '').strip()
        super().save(*args, **kwargs)

    @property
    def scope_label(self):
        return self.branch_name or self.branch_code or 'All branches'

    @property
    def covered_area_list(self):
        raw = (self.covered_areas or '').replace('\n', ',')
        return [a.strip() for a in raw.split(',') if a.strip()]
