"""Setup → Product Page Theme.

Which design the storefront's product pages use, and every string the second
design renders. The global choice lives on the `store.ProductPageTheme`
singleton; a product that should differ gets a `store.ProductThemeOverride`
row, where a blank field means "inherit the global" — that is the whole
precedence rule, and `store/theme2.py` is the only place it is applied.

Every write busts the storefront's theme cache, the same way the Delivery
Charge and Bulk Discount setup pages bust theirs.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_only
from dashboard.models import Product
from store import theme2
from store.models import (LAYOUT_CHOICES, PRODUCT_LAYOUT_CHOICES,
                          ProductPageTheme, ProductThemeOverride, ThemeMedia)

# The global settings screen writes exactly these, and nothing else. Every one
# is a text input or a select on purpose: no checkboxes, so a partially
# submitted form can never silently switch a boolean off.
GLOBAL_TEXT_FIELDS = (
    'highlights', 'pay_chips', 'pay_bullets',
    'return_label', 'return_value', 'warranty_label', 'warranty_value',
    'shipping_label', 'shipping_value', 'trust_json', 'buy_label',
    'info_title', 'video_title', 'videos',
    'ship_fee', 'checkout_title', 'place_label', 'cod_label',
    'phone_prefix', 'phone_hint',
    'stat_text', 'stat_image', 'benefit_text', 'benefit_image',
    'before_after_title', 'before_after',
    'steps_title', 'steps', 'ingredients_title', 'ingredients',
    'footer_address', 'footer_phone', 'footer_credit',
)

BUY_ACTIONS = {'modal', 'checkout'}


def _meta_fields():
    """key -> (label, textarea rows, help). Rendered and saved off one list, so
    the form and the save loop cannot drift apart."""
    return {
        'highlights': ('Product highlights', 4, 'One bullet per line.'),
        'pay_chips': ('Payment chips', 1, 'Comma separated, e.g. Prepaid, COD.'),
        'pay_bullets': ('Payment bullets', 3, 'One bullet per line.'),
        'return_value': ('Return policy', 1, ''),
        'warranty_value': ('Warranty', 1, ''),
        'shipping_value': ('Shipping line', 1, ''),
        'videos': ('Videos', 4,
                   'Self-hosted clips only — a YouTube or Vimeo embed cannot take '
                   'the play, mute and expand controls. Vertical 9:16 fits the strip.'),
        'stat_text': ('Trust stat', 3, 'First line is the big figure, the rest is the supporting copy.'),
        'stat_image': ('Trust stat image', 1, 'Upload a photo, or paste a URL.'),
        'benefit_text': ('Benefit tagline', 1, ''),
        'benefit_image': ('Benefit image', 1, 'Upload a photo, or paste a URL.'),
        'before_after': ('Before &amp; after', 4,
                         'One pair per line. A pair needs both halves to render.'),
        'steps': ('How to use', 5,
                  'Numbers come from the order of the rows.'),
        'ingredients': ('Key ingredients', 4, 'Drawn as a picture-and-label grid.'),
        'ship_fee': ('Delivery fee', 1,
                     'A flat fee for this product’s one-step checkout. Blank uses '
                     'the store-wide fee, and a blank store-wide fee uses Delivery '
                     'Charge Setup, which prices by district.'),
    }


def _saved(request, message):
    """Every mutation ends the same way: bust the storefront cache, say so."""
    theme2.invalidate_cache()
    messages.success(request, message)
    return redirect('product_theme_setup')


# ──────────────────── Page ────────────────────

@login_required
@admin_only
def product_theme_setup(request):
    setting = ProductPageTheme.get_solo()

    search = (request.GET.get('q') or '').strip()
    only = (request.GET.get('only') or '').strip()

    products = Product.objects.filter(is_deleted=False).select_related('page_theme')
    if search:
        products = products.filter(Q(name__icontains=search) | Q(slug__icontains=search))
    if only == 'theme2':
        products = products.filter(page_theme__layout='theme2')
    elif only == 'theme1':
        products = products.filter(page_theme__layout='theme1')
    elif only == 'custom':
        products = products.filter(page_theme__isnull=False)
    products = products.order_by('name')

    page = Paginator(products, 25).get_page(request.GET.get('page'))

    overrides = ProductThemeOverride.objects.all()
    stats = {
        'global_layout': dict(LAYOUT_CHOICES).get(setting.layout, setting.layout),
        'overrides': overrides.count(),
        'on_theme2': overrides.filter(layout='theme2').count(),
        'on_theme1': overrides.filter(layout='theme1').count(),
    }

    return render(request, 'dashboard/product_theme_setup.html', {
        'page_title': 'Product Page Theme',
        'setting': setting,
        'layout_choices': LAYOUT_CHOICES,
        'product_layout_choices': PRODUCT_LAYOUT_CHOICES,
        'meta_fields': [
            {'key': key, 'label': label, 'rows': rows, 'help': help_text}
            for key, (label, rows, help_text) in _meta_fields().items()
        ],
        'products': page,
        'search': search,
        'only': only,
        'stats': stats,
    })


@login_required
@admin_only
def product_theme_product_json(request, product_id):
    """One product's override, for the edit drawer. Blank fields mean inherit,
    so the drawer shows the global value as the placeholder."""
    product = get_object_or_404(Product, pk=product_id, is_deleted=False)
    row = ProductThemeOverride.objects.filter(product=product).first()

    data = {'id': product.pk, 'name': product.name,
            'layout': row.layout if row else '',
            'resolved': theme2.layout_for(product, row),
            'fields': {}, 'inherited': {}}
    for key in _meta_fields():
        data['fields'][key] = getattr(row, key, '') if row else ''
        # What the product page will show if this box is left empty.
        data['inherited'][key] = theme2.settings_snapshot().get(key, '') or theme2.DEFAULTS.get(key, '')
    return JsonResponse(data)


# ──────────────────── Global settings ────────────────────

@login_required
@admin_only
@require_POST
def product_theme_save(request):
    setting = ProductPageTheme.get_solo()

    layout = (request.POST.get('layout') or '').strip()
    if layout not in theme2.layouts():
        messages.error(request, '❌ Pick a product page design.')
        return redirect('product_theme_setup')
    setting.layout = layout

    buy_action = (request.POST.get('buy_action') or '').strip()
    setting.buy_action = buy_action if buy_action in BUY_ACTIONS else 'modal'

    for key in GLOBAL_TEXT_FIELDS:
        value = (request.POST.get(key) or '').strip()
        field = ProductPageTheme._meta.get_field(key)
        if field.max_length:
            value = value[:field.max_length]
        setattr(setting, key, value)

    setting.save()
    return _saved(request, '✅ Saved the product page theme.')


# ──────────────────── Per-product override ────────────────────

@login_required
@admin_only
@require_POST
def product_theme_product_save(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_deleted=False)

    layout = (request.POST.get('layout') or '').strip()
    if layout and layout not in theme2.layouts():
        messages.error(request, '❌ That is not a product page design.')
        return redirect('product_theme_setup')

    row, _created = ProductThemeOverride.objects.get_or_create(product=product)
    row.layout = layout
    for key in _meta_fields():
        value = (request.POST.get(key) or '').strip()
        field = ProductThemeOverride._meta.get_field(key)
        if field.max_length:
            value = value[:field.max_length]
        setattr(row, key, value)
    row.save()

    # A row where every single box came back blank says nothing at all; keeping
    # it would only make the "products with their own settings" count lie.
    if not row.layout and not any(getattr(row, key) for key in _meta_fields()):
        row.delete()
        return _saved(request, f'✅ {product.name} is back to the global settings.')

    return _saved(request, f'✅ Saved the page settings for {product.name}.')


@login_required
@admin_only
@require_POST
def product_theme_product_reset(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_deleted=False)
    ProductThemeOverride.objects.filter(product=product).delete()
    return _saved(request, f'🗑️ {product.name} is back to the global settings.')


# ──────────────────── Uploads ────────────────────
# Photos and clips are uploaded here rather than pasted as URLs, because
# knowing a media URL is not a thing a shop owner should have to do. What gets
# written into the theme's text fields is still the file's URL, so a pasted CDN
# link keeps working and nothing is locked to this table.


def _media_payload(row):
    return {
        'id': row.pk,
        'url': row.url,
        'kind': row.kind,
        'title': str(row),
        'size': row.size_label,
    }


@login_required
@admin_only
def product_theme_media_list(request):
    """The uploaded photos and clips, newest first, for the picker grid."""
    kind = (request.GET.get('kind') or '').strip()
    rows = ThemeMedia.objects.all()
    if kind in (ThemeMedia.KIND_IMAGE, ThemeMedia.KIND_VIDEO):
        rows = rows.filter(kind=kind)
    return JsonResponse({'media': [_media_payload(r) for r in rows[:120]]})


@login_required
@admin_only
@require_POST
def product_theme_media_upload(request):
    """Take one uploaded photo or clip and hand back the URL to insert.

    Refuses by extension and by size rather than trusting the browser's
    content type, which is trivially wrong and trivially forged.
    """
    upload = request.FILES.get('file')
    if upload is None:
        return JsonResponse({'success': False, 'message': 'No file was sent.'}, status=400)

    kind = ThemeMedia.kind_for(upload.name)
    if not kind:
        return JsonResponse({
            'success': False,
            'message': 'That file type is not supported. Use %s for photos, or %s for clips.' % (
                ', '.join(e.lstrip('.') for e in ThemeMedia.IMAGE_EXTENSIONS),
                ', '.join(e.lstrip('.') for e in ThemeMedia.VIDEO_EXTENSIONS)),
        }, status=400)

    ceiling = (ThemeMedia.MAX_VIDEO_BYTES if kind == ThemeMedia.KIND_VIDEO
               else ThemeMedia.MAX_IMAGE_BYTES)
    if upload.size > ceiling:
        return JsonResponse({
            'success': False,
            'message': 'That file is %.1f MB. The limit for a %s is %d MB.' % (
                upload.size / (1024 * 1024), kind, ceiling // (1024 * 1024)),
        }, status=400)

    row = ThemeMedia.objects.create(
        file=upload, kind=kind, title=upload.name[:255],
        file_size=upload.size, uploaded_by=request.user)
    return JsonResponse({'success': True, 'media': _media_payload(row)})


@login_required
@admin_only
@require_POST
def product_theme_media_delete(request, media_id):
    """Remove one upload. The file goes too — an orphaned blob nobody can see
    in the picker is just disk that never comes back."""
    row = get_object_or_404(ThemeMedia, pk=media_id)
    if row.file:
        row.file.delete(save=False)
    row.delete()
    return JsonResponse({'success': True})
