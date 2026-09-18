"""
One-off backfill: mirrors product images uploaded BEFORE the Media Library
auto-capture feature existed (dashboard/views.py _add_gallery_image_to_media_library /
_add_main_image_to_media_library) into MediaAsset, so they show up on /media/
alongside anything uploaded from now on.

Covers:
  - ProductImage rows with no source_asset yet (gallery images never picked
    from, or mirrored into, the library).
  - Product.image (main image) for products whose main image isn't already
    represented by one of that product's own gallery images (skipped in that
    case to avoid storing two copies of the same file).

Safe to re-run: gallery images already linked (source_asset set) are skipped
by _add_gallery_image_to_media_library itself; main images are skipped here
if a MediaAsset with a matching title + file size already exists.
"""
import sys, os
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from dashboard.models import Product, ProductImage, MediaAsset
from dashboard.views import _add_gallery_image_to_media_library, _add_main_image_to_media_library


class FakeRequest:
    def __init__(self, user):
        self.user = user


print("=" * 70)
print("STEP 1: backfilling gallery images without a Media Library asset")
print("=" * 70)
gallery_created = 0
gallery_qs = ProductImage.objects.filter(source_asset__isnull=True).select_related('product', 'product__user')
for pi in gallery_qs:
    before = MediaAsset.objects.count()
    _add_gallery_image_to_media_library(pi, FakeRequest(pi.product.user))
    if MediaAsset.objects.count() > before:
        gallery_created += 1
        print(f"  [OK] ProductImage {pi.pk} ({pi.product.name}) -> new MediaAsset")
    else:
        print(f"  [SKIP/ERROR] ProductImage {pi.pk} ({pi.product.name}) — see logs/django_errors.log if unexpected")

print()
print("=" * 70)
print("STEP 2: backfilling main product images")
print("=" * 70)
main_created = 0
main_skipped_dup = 0
for product in Product.objects.all():
    if not product.image:
        continue

    # Already represented by one of this product's own gallery images (e.g. it was
    # promoted via "set as default") — that file was just mirrored in step 1, or
    # already had a source_asset link. A second copy would just duplicate storage.
    if ProductImage.objects.filter(product=product, image=product.image.name).exists():
        main_skipped_dup += 1
        continue

    # Idempotency guard for re-runs: skip if this exact main image already has an
    # asset copy (same title + byte size).
    try:
        size = product.image.size
    except Exception:
        size = None
    if size is not None and MediaAsset.objects.filter(title=product.name[:255], file_size=size).exists():
        main_skipped_dup += 1
        continue

    before = MediaAsset.objects.count()
    _add_main_image_to_media_library(product, FakeRequest(product.user))
    if MediaAsset.objects.count() > before:
        main_created += 1
        print(f"  [OK] Product {product.pk} ({product.name}) -> new MediaAsset")
    else:
        print(f"  [SKIP/ERROR] Product {product.pk} ({product.name}) — see logs/django_errors.log if unexpected")

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Gallery images mirrored : {gallery_created}")
print(f"Main images mirrored    : {main_created}")
print(f"Main images skipped (dup): {main_skipped_dup}")
print(f"Total MediaAsset now    : {MediaAsset.objects.count()}")
