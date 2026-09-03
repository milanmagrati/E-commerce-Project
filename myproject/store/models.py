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
    # Set for a variable product: which ProductVariation the shopper chose. A
    # cart may hold several lines for one product, one per variation.
    variation = models.ForeignKey('dashboard.ProductVariation', on_delete=models.CASCADE,
                                  null=True, blank=True, related_name='store_cart_items')
    quantity = models.PositiveIntegerField(default=1)
    selected_variant = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        unique_together = ('cart', 'product', 'variation')

    def __str__(self):
        return f'{self.product.name} x {self.quantity}'

    @property
    def base_unit_price(self):
        """List price of one unit, before any quantity break — the variation's
        own price when there is one."""
        if self.variation:
            return self.variation.price
        return self.product.price

    @property
    def bulk_price(self):
        """The whole quantity-break picture for this line, priced at the
        quantity actually in the cart. See :mod:`store.bulk_discounts`."""
        from . import bulk_discounts
        # The cart template reads unit_price, line_total, the tier and the
        # saving off the same row, so the quote is memoised — keyed on the
        # quantity, the only thing that can change it inside one request.
        cached = getattr(self, '_bulk_quote', None)
        if cached and cached[0] == self.quantity:
            return cached[1]
        quote = bulk_discounts.price_for(
            self.product, self.variation, self.quantity, self.base_unit_price)
        self._bulk_quote = (self.quantity, quote)
        return quote

    @property
    def unit_price(self):
        """Price of one unit as this line is currently priced — the quantity
        break applies from the moment the cart holds enough units."""
        return self.bulk_price['unit']

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    @property
    def bulk_tier(self):
        """The rung in force on this line, or None at list price."""
        return self.bulk_price['tier']

    @property
    def bulk_saved(self):
        """Rupees this line is saving against the list price."""
        return self.bulk_price['saved']

    @property
    def variant_label(self):
        """What to show under the product name in the cart / checkout."""
        if self.variation:
            return self.variation.display_label
        return self.selected_variant

    @property
    def available_stock(self):
        """Stock ceiling for the quantity stepper — nets off units already
        promised to orders that have not shipped."""
        if self.variation:
            return self.variation.available_stock
        return self.product.available_stock


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


class BulkDiscount(models.Model):
    """A quantity break — "buy 3, save 10%" — administered from
    Setup → Bulk Discounts.

    One rule points at exactly one target: a single variation, a single
    product, a category, or the whole shop. It carries a ladder of
    :class:`BulkDiscountTier` rows, each saying what one unit costs once the
    shopper takes ``min_qty`` or more.

    :mod:`store.bulk_discounts` picks the most specific live rule for a line
    (variation ▸ product ▸ category ▸ everything) and prices it. Nothing here
    is quoted directly to a shopper — always go through that module, so the
    card, the product page, the cart and the order all agree.
    """

    SCOPE_VARIATION = 'variation'
    SCOPE_PRODUCT = 'product'
    SCOPE_CATEGORY = 'category'
    SCOPE_ALL = 'all'
    SCOPE_CHOICES = [
        (SCOPE_VARIATION, 'One variation'),
        (SCOPE_PRODUCT, 'One product'),
        (SCOPE_CATEGORY, 'A whole category'),
        (SCOPE_ALL, 'Every product'),
    ]
    # How specific each scope is. A variation rule beats the product rule it
    # sits under, which beats the category rule, which beats a shop-wide one.
    SCOPE_RANK = {
        SCOPE_VARIATION: 40,
        SCOPE_PRODUCT: 30,
        SCOPE_CATEGORY: 20,
        SCOPE_ALL: 10,
    }

    name = models.CharField(
        max_length=140, blank=True, default='',
        help_text="Internal name, e.g. 'Beard oil — festival bundle'.")
    scope = models.CharField(max_length=12, choices=SCOPE_CHOICES, default=SCOPE_PRODUCT)

    # Exactly one of these is set, matching `scope`. `product` is also filled in
    # for a variation rule so the setup page can group and search by product.
    product = models.ForeignKey(Product, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='bulk_discounts')
    variation = models.ForeignKey('dashboard.ProductVariation', on_delete=models.CASCADE,
                                  null=True, blank=True, related_name='bulk_discounts')
    category = models.ForeignKey('dashboard.Category', on_delete=models.CASCADE,
                                 null=True, blank=True, related_name='bulk_discounts')

    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(
        default=0, help_text="When two rules target the same thing, the higher number wins.")
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Blank = live immediately.")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Blank = never expires.")

    show_on_cards = models.BooleanField(
        default=True, help_text="Show the offer on listing/home product cards (simple products).")
    badge_text = models.CharField(
        max_length=60, blank=True, default='',
        help_text="Overrides the automatic 'Save 10%' line. Blank = generated.")
    note = models.CharField(max_length=200, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', '-updated_at']
        indexes = [
            models.Index(fields=['scope', 'is_active']),
            models.Index(fields=['product', 'is_active']),
            models.Index(fields=['variation', 'is_active']),
        ]
        verbose_name = 'bulk discount'
        verbose_name_plural = 'bulk discounts'

    def __str__(self):
        return self.name or f'{self.get_scope_display()} — {self.target_label}'

    def save(self, *args, **kwargs):
        # Keep the target columns honest: only the one the scope names is kept,
        # so a rule edited from 'product' to 'category' cannot go on matching
        # the product it used to point at.
        if self.scope == self.SCOPE_VARIATION:
            # Always re-derived, never merely filled in: a rule re-pointed at
            # an option of a different product would otherwise keep the old
            # product on it and turn up under the wrong name on the setup page.
            if self.variation_id:
                self.product_id = self.variation.product_id
            self.category = None
        elif self.scope == self.SCOPE_PRODUCT:
            self.variation = None
            self.category = None
        elif self.scope == self.SCOPE_CATEGORY:
            self.product = None
            self.variation = None
        else:
            self.product = None
            self.variation = None
            self.category = None
        super().save(*args, **kwargs)

    @property
    def scope_rank(self):
        return self.SCOPE_RANK.get(self.scope, 0)

    @property
    def target_label(self):
        """What this rule applies to, in one line, for the setup table."""
        if self.scope == self.SCOPE_VARIATION and self.variation:
            return f'{self.variation.product.name} — {self.variation.display_label}'
        if self.scope == self.SCOPE_PRODUCT and self.product:
            return self.product.name
        if self.scope == self.SCOPE_CATEGORY and self.category:
            return self.category.name
        if self.scope == self.SCOPE_ALL:
            return 'Every product'
        return '— target removed —'

    @property
    def is_live(self):
        """Active *and* inside its schedule window right now."""
        if not self.is_active:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    @property
    def schedule_state(self):
        """'live' | 'scheduled' | 'expired' | 'off' — drives the status pill."""
        if not self.is_active:
            return 'off'
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return 'scheduled'
        if self.ends_at and now > self.ends_at:
            return 'expired'
        return 'live'

    @property
    def base_price(self):
        """The price the tiers discount from, when the rule has a single
        target. Category/shop-wide rules have no one price — they are priced
        per line at quote time."""
        if self.variation_id:
            return self.variation.price
        if self.product_id:
            return self.product.price
        return None

    @property
    def tier_count(self):
        return self.tiers.count()


class BulkDiscountTier(models.Model):
    """One rung of a :class:`BulkDiscount` ladder: from ``min_qty`` units up,
    each unit costs what ``discount_type`` + ``value`` work out to."""

    TYPE_PERCENT = 'percent'
    TYPE_AMOUNT = 'amount'
    TYPE_PRICE = 'price'
    TYPE_CHOICES = [
        (TYPE_PERCENT, '% off each'),
        (TYPE_AMOUNT, 'Rs. off each'),
        (TYPE_PRICE, 'Fixed price each'),
    ]

    rule = models.ForeignKey(BulkDiscount, on_delete=models.CASCADE, related_name='tiers')
    min_qty = models.PositiveIntegerField(
        default=2, validators=[MinValueValidator(1)],
        help_text="Units the shopper must take for this rung to apply.")
    discount_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_PERCENT)
    value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    label = models.CharField(
        max_length=60, blank=True, default='',
        help_text="Optional name for this rung, e.g. 'Family pack'.")

    class Meta:
        ordering = ['min_qty']
        unique_together = [('rule', 'min_qty')]
        verbose_name = 'bulk discount tier'
        verbose_name_plural = 'bulk discount tiers'

    def __str__(self):
        return f'{self.min_qty}+ → {self.offer_label}'

    @property
    def offer_label(self):
        """'Save 10%' / 'Rs. 200 off each' / 'Rs. 1,800 each'."""
        if self.discount_type == self.TYPE_PERCENT:
            return f'Save {self.value:.0f}%'
        if self.discount_type == self.TYPE_AMOUNT:
            return f'Rs. {self.value:,.0f} off each'
        return f'Rs. {self.value:,.0f} each'

    def unit_price_from(self, base_price):
        """What one unit costs at this rung, given the undiscounted price.

        Never returns a negative price and never returns more than the base —
        a mis-typed rule can only fail towards charging the normal price.
        """
        base = Decimal(str(base_price or 0))
        if base <= 0:
            return Decimal('0.00')
        if self.discount_type == self.TYPE_PERCENT:
            pct = max(Decimal('0'), min(self.value, Decimal('100')))
            price = base * (Decimal('100') - pct) / Decimal('100')
        elif self.discount_type == self.TYPE_AMOUNT:
            price = base - max(Decimal('0'), self.value)
        else:
            price = max(Decimal('0'), self.value)
        price = min(max(price, Decimal('0')), base)
        return price.quantize(Decimal('0.01'))


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
    variation = models.ForeignKey('dashboard.ProductVariation', on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name='store_order_items')
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


class BackInStockNotice(models.Model):
    """A shopper asking to be told when a sold-out product — or one specific
    variation of it — can be bought again.

    Works for a guest (keyed by session) or a signed-in shopper. One pending
    row per contact address and target; ``notified_at`` is stamped once the
    alert has been sent so it never fires twice.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
                                related_name='back_in_stock_notices')
    variation = models.ForeignKey('dashboard.ProductVariation', on_delete=models.CASCADE,
                                  null=True, blank=True,
                                  related_name='back_in_stock_notices')
    customer = models.ForeignKey('StoreCustomer', on_delete=models.SET_NULL,
                                 null=True, blank=True,
                                 related_name='back_in_stock_notices')
    session_key = models.CharField(max_length=40, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product', 'notified_at']),
            models.Index(fields=['variation', 'notified_at']),
        ]
        verbose_name = 'back-in-stock notice'
        verbose_name_plural = 'back-in-stock notices'

    def __str__(self):
        who = self.email or self.phone or self.session_key or 'guest'
        what = self.variation.display_label if self.variation else self.product.name
        return f'{who} -> {what}'

    @property
    def target_label(self):
        return self.variation.display_label if self.variation else self.product.name


# ============================================================================
#  PRODUCT PAGE THEME  (Setup → Product Page Theme)
# ============================================================================
#  The storefront ships two product-page designs. Theme 1 is the classic
#  layout in `store/product_detail.html`; Theme 2 is the three-column
#  conversion landing page in `store/product_detail_conversion.html`.
#
#  `ProductPageTheme` is the singleton holding the global choice plus every
#  piece of Theme 2 copy. `ProductThemeOverride` is the per-product row: a
#  blank field there means "inherit the global", which is the whole precedence
#  rule. Resolution lives in `store/theme2.py` — never read these rows
#  directly from a view or a template.
# ============================================================================


LAYOUT_THEME1 = 'theme1'
LAYOUT_THEME2 = 'theme2'

LAYOUT_CHOICES = [
    (LAYOUT_THEME1, 'Theme 1 - Classic (current design)'),
    (LAYOUT_THEME2, 'Theme 2 - Conversion landing page'),
]

# The per-product select adds one more option on top of those: '' = inherit.
PRODUCT_LAYOUT_CHOICES = [('', 'Use the global setting')] + LAYOUT_CHOICES


class ProductPageTheme(models.Model):
    """Site-wide product-page design + every default string Theme 2 renders.

    Singleton, read through `store.theme2.settings_snapshot()` which caches it;
    the setup page busts that cache after each write.
    """

    layout = models.CharField(
        max_length=10, choices=LAYOUT_CHOICES, default=LAYOUT_THEME1,
        help_text="Which design every product page uses unless the product overrides it.")

    # -- Column C: the info rail --
    highlights = models.TextField(
        blank=True, default='',
        help_text="PRODUCT HIGHLIGHTS bullets - one per line.")
    pay_chips = models.CharField(
        max_length=300, blank=True, default='',
        help_text="Payment chips, comma separated (e.g. Prepaid, COD).")
    pay_bullets = models.TextField(
        blank=True, default='',
        help_text="PAYMENT & OFFERS bullets - one per line.")

    return_label = models.CharField(max_length=80, blank=True, default='')
    return_value = models.CharField(max_length=160, blank=True, default='')
    warranty_label = models.CharField(max_length=80, blank=True, default='')
    warranty_value = models.CharField(max_length=160, blank=True, default='')
    shipping_label = models.CharField(max_length=80, blank=True, default='')
    shipping_value = models.CharField(max_length=160, blank=True, default='')

    # -- Column B: the buy box --
    trust_json = models.TextField(
        blank=True, default='',
        help_text='Trust row, as JSON: [{"icon": "delivery", "text": "..."}]. '
                  'Icons: delivery, payment, secure, return, warranty.')
    buy_label = models.CharField(max_length=60, blank=True, default='')

    # -- Column A: media and content --
    info_title = models.CharField(max_length=80, blank=True, default='')
    video_title = models.CharField(max_length=80, blank=True, default='')
    videos = models.TextField(
        blank=True, default='',
        help_text="One clip per line: video | poster | creator. Self-hosted "
                  "files only - a YouTube/Vimeo embed cannot take the custom "
                  "play, mute and expand controls.")
    stat_text = models.TextField(
        blank=True, default='',
        help_text="Trust stat. First line is the big figure, the rest is the "
                  "supporting copy.")
    stat_image = models.CharField(max_length=300, blank=True, default='')
    benefit_text = models.CharField(max_length=300, blank=True, default='')
    benefit_image = models.CharField(max_length=300, blank=True, default='')
    steps_title = models.CharField(max_length=80, blank=True, default='')
    steps = models.TextField(
        blank=True, default='',
        help_text="One step per line: title | instruction | image. Numbers are "
                  "generated from the line order - do not type them.")
    ingredients_title = models.CharField(max_length=80, blank=True, default='')
    ingredients = models.TextField(
        blank=True, default='',
        help_text="One per line: name | image | short note.")

    # -- The one-step COD checkout --
    buy_action = models.CharField(
        max_length=10,
        choices=[('modal', 'Open the one-step COD checkout'),
                 ('checkout', 'Add to cart and go to the checkout page')],
        default='modal')
    ship_fee = models.CharField(
        max_length=40, blank=True, default='',
        help_text="Flat delivery fee for Theme 2 orders. Leave blank to use "
                  "Setup > Delivery Charge Setup, which prices by district.")
    checkout_title = models.CharField(max_length=80, blank=True, default='')
    place_label = models.CharField(max_length=60, blank=True, default='')
    cod_label = models.CharField(max_length=80, blank=True, default='')
    phone_prefix = models.CharField(max_length=10, blank=True, default='')
    phone_hint = models.CharField(max_length=160, blank=True, default='')

    # -- The minimal shell --
    footer_address = models.CharField(max_length=300, blank=True, default='')
    footer_phone = models.CharField(max_length=120, blank=True, default='')
    footer_credit = models.CharField(max_length=200, blank=True, default='')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'product page theme'
        verbose_name_plural = 'product page theme'

    def __str__(self):
        return dict(LAYOUT_CHOICES).get(self.layout, self.layout)

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj


class ProductThemeOverride(models.Model):
    """One product's departures from the global Theme 2 settings.

    Every text field here is blank by default and blank means *inherit* - so a
    product with a row that only sets `shipping_value` still shows the global
    highlights, videos and stat.
    """

    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name='page_theme')

    layout = models.CharField(
        max_length=10, choices=PRODUCT_LAYOUT_CHOICES, blank=True, default='',
        help_text="Blank inherits the global product-page design.")

    highlights = models.TextField(blank=True, default='')
    pay_chips = models.CharField(max_length=300, blank=True, default='')
    pay_bullets = models.TextField(blank=True, default='')
    return_value = models.CharField(max_length=160, blank=True, default='')
    warranty_value = models.CharField(max_length=160, blank=True, default='')
    shipping_value = models.CharField(max_length=160, blank=True, default='')
    videos = models.TextField(blank=True, default='')
    stat_text = models.TextField(blank=True, default='')
    stat_image = models.CharField(max_length=300, blank=True, default='')
    benefit_text = models.CharField(max_length=300, blank=True, default='')
    benefit_image = models.CharField(max_length=300, blank=True, default='')
    steps = models.TextField(blank=True, default='')
    ingredients = models.TextField(blank=True, default='')
    ship_fee = models.CharField(max_length=40, blank=True, default='')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'product page override'
        verbose_name_plural = 'product page overrides'

    def __str__(self):
        return 'Theme settings for %s' % self.product.name


class ReviewVote(models.Model):
    """One helpful / not-helpful vote on one review, from one visitor.

    A signed-in shopper is matched on `customer`; a guest on `session_key`, so
    the vote survives a reload without an account.

    `voter_key` is what the uniqueness is actually enforced on: `c:<id>` for an
    account, `s:<session>` for a guest. A pair of partial unique constraints
    would read more naturally, but the production database is MySQL, which
    silently declines to create conditional constraints — leaving one click per
    visitor as a promise nothing keeps. One always-populated column is a rule
    the database can hold.
    """

    UP = 'up'
    DOWN = 'down'
    VALUE_CHOICES = [(UP, 'Helpful'), (DOWN, 'Not helpful')]

    review = models.ForeignKey(ProductReview, on_delete=models.CASCADE,
                               related_name='votes')
    customer = models.ForeignKey('StoreCustomer', on_delete=models.CASCADE,
                                 null=True, blank=True, related_name='review_votes')
    session_key = models.CharField(max_length=40, blank=True, default='', db_index=True)
    voter_key = models.CharField(max_length=48, db_index=True)
    value = models.CharField(max_length=4, choices=VALUE_CHOICES, default=UP)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('review', 'voter_key')

    def __str__(self):
        return '%s on review %s' % (self.value, self.review_id)

    @staticmethod
    def key_for(customer, session_key):
        """The one identifier a vote is deduplicated on."""
        return 'c:%s' % customer.pk if customer else 's:%s' % (session_key or '')
