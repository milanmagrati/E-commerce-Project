from django.contrib import admin
from .models import (
    BillUpload, ExtractedBill, ExtractedLineItem,
    ProductAlias, RewardConfig, RewardTransaction,
    CustomerRewardBalance, BillProductTrend,
)


class ExtractedLineItemInline(admin.TabularInline):
    model = ExtractedLineItem
    extra = 0
    readonly_fields = ('raw_description', 'quantity', 'unit_price', 'line_amount',
                       'matched_product', 'match_confidence', 'match_method')


class ExtractedBillInline(admin.StackedInline):
    model = ExtractedBill
    extra = 0


@admin.register(BillUpload)
class BillUploadAdmin(admin.ModelAdmin):
    list_display = ('id', 'file_name', 'status', 'ocr_provider', 'ocr_confidence',
                    'is_duplicate', 'customer', 'created_at')
    list_filter = ('status', 'ocr_provider', 'is_duplicate')
    search_fields = ('file_name', 'customer__name')
    readonly_fields = ('id', 'file_hash', 'raw_ocr_json', 'raw_ocr_text')
    inlines = [ExtractedBillInline]


@admin.register(ExtractedBill)
class ExtractedBillAdmin(admin.ModelAdmin):
    list_display = ('upload', 'invoice_number', 'shop_name', 'customer_name',
                    'bill_date', 'total_amount')
    search_fields = ('invoice_number', 'shop_name', 'customer_name')
    inlines = [ExtractedLineItemInline]


@admin.register(ProductAlias)
class ProductAliasAdmin(admin.ModelAdmin):
    list_display = ('alias_name', 'product', 'created_by', 'created_at')
    search_fields = ('alias_name', 'product__name')


@admin.register(RewardConfig)
class RewardConfigAdmin(admin.ModelAdmin):
    list_display = ('points_per_currency_unit', 'min_bill_total_for_reward',
                    'max_points_per_bill', 'is_active')


@admin.register(RewardTransaction)
class RewardTransactionAdmin(admin.ModelAdmin):
    list_display = ('customer', 'transaction_type', 'points', 'balance_after', 'created_at')
    list_filter = ('transaction_type',)
    search_fields = ('customer__name',)


@admin.register(CustomerRewardBalance)
class CustomerRewardBalanceAdmin(admin.ModelAdmin):
    list_display = ('customer', 'current_balance', 'total_earned', 'total_redeemed')
    search_fields = ('customer__name',)


@admin.register(BillProductTrend)
class BillProductTrendAdmin(admin.ModelAdmin):
    list_display = ('product', 'period_type', 'period_date', 'total_quantity', 'bill_count')
    list_filter = ('period_type',)
