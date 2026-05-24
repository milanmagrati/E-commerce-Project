import hashlib
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


# ─────────────────────────────────────────────────────────
# 1. BILL UPLOAD & OCR RESULTS
# ─────────────────────────────────────────────────────────

class BillUpload(models.Model):
    """A single bill image/PDF uploaded by an admin or customer."""

    STATUS_CHOICES = [
        ('pending', 'Pending OCR'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('review', 'Needs Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('failed', 'OCR Failed'),
    ]

    OCR_PROVIDER_CHOICES = [
        ('aws_textract', 'AWS Textract'),
        ('google_docai', 'Google Document AI'),
        ('manual', 'Manual Entry'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to='bill_uploads/%Y/%m/')
    file_name = models.CharField(max_length=255, blank=True)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True,
                                 help_text='SHA-256 hash for duplicate detection')

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='bill_uploads',
    )
    customer = models.ForeignKey(
        'dashboard.Customer', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='bill_uploads',
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    ocr_provider = models.CharField(max_length=30, choices=OCR_PROVIDER_CHOICES, blank=True)
    ocr_confidence = models.FloatField(null=True, blank=True, help_text='Overall OCR confidence 0-100')

    # Raw OCR response stored for auditing
    raw_ocr_json = models.JSONField(default=dict, blank=True)
    raw_ocr_text = models.TextField(blank=True)

    # Processing metadata
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True)

    # Duplicate detection
    is_duplicate = models.BooleanField(default=False)
    duplicate_of = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='duplicates',
    )

    # Admin review fields
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reviewed_bills',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Bill Upload'
        verbose_name_plural = 'Bill Uploads'
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['file_hash']),
        ]

    def __str__(self):
        return f'Bill {str(self.id)[:8]} – {self.get_status_display()}'

    def save(self, *args, **kwargs):
        if self.file and not self.file_hash:
            self.file_hash = self._compute_hash()
        if self.file and not self.file_name:
            self.file_name = self.file.name.split('/')[-1]
        super().save(*args, **kwargs)

    def _compute_hash(self):
        h = hashlib.sha256()
        for chunk in self.file.chunks():
            h.update(chunk)
        return h.hexdigest()


class ExtractedBill(models.Model):
    """Normalized / structured data extracted from a BillUpload."""

    upload = models.OneToOneField(BillUpload, on_delete=models.CASCADE, related_name='extracted')

    # Header fields
    customer_name = models.CharField(max_length=255, blank=True)
    shop_name = models.CharField(max_length=255, blank=True)
    invoice_number = models.CharField(max_length=100, blank=True, db_index=True)
    bill_date = models.DateField(null=True, blank=True)

    # Totals
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Field-level confidence scores (0-100)
    confidence_customer_name = models.FloatField(null=True, blank=True)
    confidence_shop_name = models.FloatField(null=True, blank=True)
    confidence_invoice_number = models.FloatField(null=True, blank=True)
    confidence_bill_date = models.FloatField(null=True, blank=True)
    confidence_subtotal = models.FloatField(null=True, blank=True)
    confidence_tax = models.FloatField(null=True, blank=True)
    confidence_total = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Extracted Bill'
        verbose_name_plural = 'Extracted Bills'

    def __str__(self):
        return f'Extracted: {self.invoice_number or self.upload}'

    @property
    def min_confidence(self):
        scores = [
            self.confidence_customer_name,
            self.confidence_shop_name,
            self.confidence_invoice_number,
            self.confidence_bill_date,
            self.confidence_subtotal,
            self.confidence_tax,
            self.confidence_total,
        ]
        valid = [s for s in scores if s is not None]
        return min(valid) if valid else None


class ExtractedLineItem(models.Model):
    """Individual line item from the bill."""

    bill = models.ForeignKey(ExtractedBill, on_delete=models.CASCADE, related_name='line_items')

    # Raw OCR data
    raw_description = models.CharField(max_length=500, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    line_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Matched product
    matched_product = models.ForeignKey(
        'dashboard.Product', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='bill_line_items',
    )
    match_confidence = models.FloatField(null=True, blank=True, help_text='Product match confidence 0-100')
    match_method = models.CharField(max_length=30, blank=True,
                                    help_text='exact, alias, fuzzy, category_predict')

    # Category prediction for unknown items
    predicted_category = models.ForeignKey(
        'dashboard.Category', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='predicted_bill_items',
    )

    order_index = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order_index']
        verbose_name = 'Extracted Line Item'
        verbose_name_plural = 'Extracted Line Items'

    def __str__(self):
        return f'{self.raw_description} x{self.quantity}'


# ─────────────────────────────────────────────────────────
# 2. PRODUCT MATCHING & ALIASES
# ─────────────────────────────────────────────────────────

class ProductAlias(models.Model):
    """Maps OCR-extracted product names to actual catalog products."""

    product = models.ForeignKey(
        'dashboard.Product', on_delete=models.CASCADE, related_name='aliases',
    )
    alias_name = models.CharField(max_length=255, db_index=True, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Product Alias'
        verbose_name_plural = 'Product Aliases'
        ordering = ['alias_name']

    def __str__(self):
        return f'"{self.alias_name}" → {self.product.name}'


# ─────────────────────────────────────────────────────────
# 3. REWARD POINTS
# ─────────────────────────────────────────────────────────

class RewardConfig(models.Model):
    """Global reward configuration (singleton-ish)."""

    points_per_currency_unit = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('1.00'),
        help_text='Reward points earned per 1 unit of currency spent',
    )
    min_bill_total_for_reward = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('100.00'),
        help_text='Minimum bill total required to earn rewards',
    )
    max_points_per_bill = models.PositiveIntegerField(
        default=5000, help_text='Maximum points earnable from a single bill',
    )
    points_expiry_days = models.PositiveIntegerField(
        default=365, help_text='Days until points expire (0 = never)',
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Reward Configuration'
        verbose_name_plural = 'Reward Configuration'

    def __str__(self):
        return f'Reward Config ({"Active" if self.is_active else "Inactive"})'

    @classmethod
    def get_config(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class RewardTransaction(models.Model):
    """Records each reward-point transaction (earn or redeem)."""

    TYPE_CHOICES = [
        ('earned', 'Earned'),
        ('redeemed', 'Redeemed'),
        ('expired', 'Expired'),
        ('adjusted', 'Admin Adjustment'),
    ]

    customer = models.ForeignKey(
        'dashboard.Customer', on_delete=models.CASCADE, related_name='reward_transactions',
    )
    bill_upload = models.ForeignKey(
        BillUpload, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reward_transactions',
    )
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    points = models.IntegerField(help_text='Positive for earn, negative for redeem/expire')
    balance_after = models.IntegerField(default=0)
    description = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Reward Transaction'
        verbose_name_plural = 'Reward Transactions'

    def __str__(self):
        return f'{self.customer} {self.get_transaction_type_display()} {self.points} pts'


class CustomerRewardBalance(models.Model):
    """Denormalized current reward balance per customer."""

    customer = models.OneToOneField(
        'dashboard.Customer', on_delete=models.CASCADE, related_name='reward_balance',
    )
    total_earned = models.IntegerField(default=0)
    total_redeemed = models.IntegerField(default=0)
    total_expired = models.IntegerField(default=0)
    current_balance = models.IntegerField(default=0)
    last_earned_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Customer Reward Balance'
        verbose_name_plural = 'Customer Reward Balances'

    def __str__(self):
        return f'{self.customer.name}: {self.current_balance} pts'


# ─────────────────────────────────────────────────────────
# 4. TRENDING PRODUCT ANALYTICS
# ─────────────────────────────────────────────────────────

class BillProductTrend(models.Model):
    """Aggregated trend data per product from bills."""

    product = models.ForeignKey(
        'dashboard.Product', on_delete=models.CASCADE, related_name='bill_trends',
    )
    period_date = models.DateField(help_text='First day of the week/month period')
    period_type = models.CharField(max_length=10, choices=[('week', 'Weekly'), ('month', 'Monthly')])
    total_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bill_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('product', 'period_date', 'period_type')
        ordering = ['-period_date']
        verbose_name = 'Bill Product Trend'
        verbose_name_plural = 'Bill Product Trends'

    def __str__(self):
        return f'{self.product.name} – {self.period_type} {self.period_date}'
