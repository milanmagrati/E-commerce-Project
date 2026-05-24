"""
Product matching service – maps OCR-extracted line-item descriptions
to existing catalog products via exact match, alias lookup, fuzzy
matching, and category prediction.
"""
import logging
from difflib import SequenceMatcher

from django.db.models import Q

from dashboard.models import Product, Category
from .models import ProductAlias

logger = logging.getLogger('bill_rewards')

# Minimum similarity ratio to consider a fuzzy match valid
FUZZY_THRESHOLD = 0.60


def match_product(raw_description):
    """
    Attempt to find a matching product for *raw_description*.

    Returns (product, confidence, method) or (None, 0, '').
    """
    if not raw_description or not raw_description.strip():
        return None, 0, ''

    desc = raw_description.strip()
    desc_lower = desc.lower()

    # 1. Exact name match
    try:
        product = Product.objects.filter(
            is_deleted=False, is_active=True, name__iexact=desc
        ).first()
        if product:
            return product, 100, 'exact'
    except Exception as e:
        logger.warning(f'Exact match error: {e}')

    # 2. Alias lookup
    try:
        alias = ProductAlias.objects.select_related('product').filter(
            alias_name__iexact=desc
        ).first()
        if alias and not alias.product.is_deleted:
            return alias.product, 95, 'alias'
    except Exception as e:
        logger.warning(f'Alias match error: {e}')

    # 3. Substring / contains match
    try:
        candidates = Product.objects.filter(
            is_deleted=False, is_active=True
        ).filter(
            Q(name__icontains=desc) | Q(barcode__iexact=desc)
        )[:5]
        if candidates.exists():
            return candidates.first(), 80, 'substring'
    except Exception as e:
        logger.warning(f'Substring match error: {e}')

    # 4. Fuzzy match against all product names
    try:
        best_ratio = 0
        best_product = None
        products = Product.objects.filter(
            is_deleted=False, is_active=True
        ).values_list('id', 'name')[:500]

        for pid, pname in products:
            ratio = SequenceMatcher(None, desc_lower, pname.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_product = pid

        if best_ratio >= FUZZY_THRESHOLD and best_product:
            product = Product.objects.get(pk=best_product)
            return product, round(best_ratio * 100, 1), 'fuzzy'
    except Exception as e:
        logger.warning(f'Fuzzy match error: {e}')

    # 5. No match – attempt category prediction
    predicted_cat = predict_category(desc)
    return None, 0, 'category_predict' if predicted_cat else ''


def predict_category(description):
    """
    Simple keyword-based category prediction for unmatched items.
    Returns a Category object or None.
    """
    if not description:
        return None

    desc_lower = description.lower()
    categories = Category.objects.all()

    for cat in categories:
        if cat.name.lower() in desc_lower or desc_lower in cat.name.lower():
            return cat

    # Check category slugs
    for cat in categories:
        if cat.slug and cat.slug.replace('-', ' ') in desc_lower:
            return cat

    return None


def create_alias_from_match(raw_description, product, user=None):
    """Convenience: persist a new alias so future bills auto-match."""
    if not raw_description or not product:
        return None
    alias, created = ProductAlias.objects.get_or_create(
        alias_name=raw_description.strip(),
        defaults={'product': product, 'created_by': user},
    )
    return alias if created else None
