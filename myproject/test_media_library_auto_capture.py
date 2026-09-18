"""
Verifies that images uploaded during product creation/edit are mirrored into
the shared Media Library (dashboard/views.py: _add_gallery_image_to_media_library,
_add_main_image_to_media_library), so they show up on the /media/ page instead
of only being reachable through the product's own gallery/main-image fields.

Checks:
  - A freshly uploaded gallery image creates a MediaAsset and links back via
    ProductImage.source_asset.
  - Re-running the mirror on the same ProductImage (source_asset already set)
    does NOT create a duplicate MediaAsset — idempotent, matching the existing
    _attach_existing_media_to_product convention.
  - A gallery image that was copied FROM the Media Library (source_asset set
    at creation, as _attach_existing_media_to_product does) is skipped since
    it's already there.
  - A freshly uploaded main product image creates a MediaAsset copy.

Uses real DB writes + real file storage (a tiny in-memory PNG), cleaned up at
the end.
"""
import sys, os
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from dashboard.models import Product, Category, ProductImage, MediaAsset
from dashboard.views import _add_gallery_image_to_media_library, _add_main_image_to_media_library
from accounts.models import CustomUser

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}")


# 1x1 transparent PNG
PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def make_png(name):
    return SimpleUploadedFile(name, PNG_BYTES, content_type='image/png')


class FakeRequest:
    def __init__(self, user):
        self.user = user


user = CustomUser.objects.filter(is_superuser=True).first() or CustomUser.objects.first()
if user is None:
    print("No CustomUser exists in this DB — cannot run this script.")
    sys.exit(1)

category, _ = Category.objects.get_or_create(name='__media_lib_test__', defaults={'slug': 'media-lib-test'})
product = Product.objects.create(
    name='__Media Library Test Product__',
    slug='media-lib-test-product',
    price=1, cost_price=1, stock=1, category=category,
    product_type='simple', user=user,
)

request = FakeRequest(user)
created_asset_ids = []
created_image_paths = []

try:
    print("=" * 70)
    print("TEST 1: fresh gallery upload is mirrored into the Media Library")
    print("=" * 70)
    before_count = MediaAsset.objects.count()
    pi = ProductImage.objects.create(product=product, image=make_png('gallery1.png'))
    created_image_paths.append(pi.image.path)
    _add_gallery_image_to_media_library(pi, request)
    pi.refresh_from_db()
    check("MediaAsset count increased by 1", MediaAsset.objects.count() == before_count + 1)
    check("ProductImage.source_asset was linked", pi.source_asset_id is not None)
    if pi.source_asset_id:
        created_asset_ids.append(pi.source_asset_id)
        created_image_paths.append(pi.source_asset.image.path)

    print("=" * 70)
    print("TEST 2: re-running the mirror on the same image does not duplicate")
    print("=" * 70)
    before_count = MediaAsset.objects.count()
    _add_gallery_image_to_media_library(pi, request)
    check("MediaAsset count unchanged (idempotent)", MediaAsset.objects.count() == before_count)

    print("=" * 70)
    print("TEST 3: images copied FROM the library are not re-mirrored")
    print("=" * 70)
    existing_asset = MediaAsset.objects.create(image=make_png('libsource.png'), title='libsource', uploaded_by=user)
    created_asset_ids.append(existing_asset.id)
    created_image_paths.append(existing_asset.image.path)
    pi2 = ProductImage.objects.create(product=product, image=make_png('gallery2.png'), source_asset=existing_asset)
    created_image_paths.append(pi2.image.path)
    before_count = MediaAsset.objects.count()
    _add_gallery_image_to_media_library(pi2, request)
    check("MediaAsset count unchanged (already from library)", MediaAsset.objects.count() == before_count)

    print("=" * 70)
    print("TEST 4: fresh main product image is mirrored into the Media Library")
    print("=" * 70)
    product.image = make_png('main.png')
    product.save(update_fields=['image'])
    created_image_paths.append(product.image.path)
    before_count = MediaAsset.objects.count()
    _add_main_image_to_media_library(product, request)
    check("MediaAsset count increased by 1", MediaAsset.objects.count() == before_count + 1)
    newest = MediaAsset.objects.order_by('-id').first()
    check("New asset title matches product name", newest.title == product.name)
    created_asset_ids.append(newest.id)
    created_image_paths.append(newest.image.path)

finally:
    print("=" * 70)
    print("Cleaning up test data...")
    for pi_obj in ProductImage.objects.filter(product=product):
        pi_obj.image.delete(save=False)
        pi_obj.delete()
    for asset in MediaAsset.objects.filter(id__in=created_asset_ids):
        asset.image.delete(save=False)
        asset.delete()
    if product.image:
        product.image.delete(save=False)
    product.delete()
    category.delete()

print("=" * 70)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 70)
sys.exit(1 if failed else 0)
