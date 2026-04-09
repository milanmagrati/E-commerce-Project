from django import template
from django.db.models import Avg
from store.models import ProductReview

register = template.Library()


@register.simple_tag
def product_avg_rating(product):
    reviews = ProductReview.objects.filter(product=product)
    if reviews.exists():
        return round(reviews.aggregate(avg=Avg('rating'))['avg'], 1)
    return 0


@register.simple_tag
def product_review_count(product):
    return ProductReview.objects.filter(product=product).count()


@register.simple_tag
def product_image_url(product):
    """Get the best image URL for a product."""
    if product.image:
        return product.image.url
    img = product.images.filter(is_featured=True).first()
    if img:
        return img.image.url
    img = product.images.first()
    if img:
        return img.image.url
    return ''
