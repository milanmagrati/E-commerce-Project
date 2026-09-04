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

from accounts.decorators import admin_only, has_any_permission
from dashboard.models import Product
from store import theme2
from store.models import (LAYOUT_CHOICES, LAYOUT_THEME2, PRODUCT_LAYOUT_CHOICES,
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
    'info_media', 'features_title', 'features',
    'footer_address', 'footer_phone', 'footer_credit',
)

BUY_ACTIONS = {'modal', 'checkout'}


# Every field a single product may set for itself, in the order both editing
# surfaces draw them: the drawer on this screen, and the panel on the product
# add/edit form. One list, so the two cannot drift and the save loop cannot
# miss a box the form happily posted.
#
# `widget` is what draws it: 'text', 'textarea', 'image' (upload + URL box) or
# 'repeater' (the row editor over the hidden textarea, keyed by `spec`).
FIELD_SPECS = (
    # -- what the shopper sees first --
    {'key': 'videos', 'label': 'Videos', 'widget': 'repeater', 'spec': 'videos',
     'rows': 4, 'group': 'media',
     'help': 'Upload the clips themselves - self-hosted files only, because the '
             'play, mute and expand controls are bound to a real video element '
             'and a YouTube or Vimeo embed cannot take them. Vertical 9:16 is '
             'the shape the strip is built for. The title and description show '
             'under each clip.'},
    {'key': 'info_media', 'label': 'Description gallery', 'widget': 'repeater',
     'spec': 'info_media', 'rows': 4, 'group': 'media',
     'help': 'A photo with its own words underneath, repeated. This is what '
             'fills the Product information block above the written description.'},

    # -- proof --
    {'key': 'before_after', 'label': 'Before & after', 'widget': 'repeater',
     'spec': 'before_after', 'rows': 4, 'group': 'proof',
     'help': 'Both halves are required - a before with no after is a photo of '
             'a problem.'},
    {'key': 'stat_text', 'label': 'Trust stat', 'widget': 'textarea', 'rows': 3,
     'group': 'proof',
     'help': 'First line is the big figure, the rest is the supporting copy.'},
    {'key': 'stat_image', 'label': 'Trust stat image', 'widget': 'image',
     'rows': 1, 'group': 'proof', 'help': 'Upload a photo, or paste a URL.'},
    {'key': 'benefit_text', 'label': 'Benefit tagline', 'widget': 'text',
     'rows': 1, 'group': 'proof', 'help': ''},
    {'key': 'benefit_image', 'label': 'Benefit image', 'widget': 'image',
     'rows': 1, 'group': 'proof', 'help': 'Upload a photo, or paste a URL.'},

    # -- teach it --
    {'key': 'steps', 'label': 'How to use', 'widget': 'repeater', 'spec': 'steps',
     'rows': 5, 'group': 'teach',
     'help': 'Numbers come from the order of the rows - move them with the '
             'arrows rather than renumbering by hand.'},
    {'key': 'ingredients', 'label': 'Key ingredients', 'widget': 'repeater',
     'spec': 'ingredients', 'rows': 4, 'group': 'teach',
     'help': 'Drawn as a picture-and-label grid.'},
    {'key': 'features', 'label': 'Features / manual', 'widget': 'repeater',
     'spec': 'features', 'rows': 4, 'group': 'teach',
     'help': 'A ticked list. The description is optional - a row with only a '
             'title is a plain feature bullet.'},

    # -- headings --
    {'key': 'video_title', 'label': 'Video section title', 'widget': 'text',
     'rows': 1, 'group': 'titles', 'help': ''},
    {'key': 'before_after_title', 'label': 'Before & after title',
     'widget': 'text', 'rows': 1, 'group': 'titles', 'help': ''},
    {'key': 'steps_title', 'label': 'How-to title', 'widget': 'text',
     'rows': 1, 'group': 'titles', 'help': ''},
    {'key': 'ingredients_title', 'label': 'Ingredients title', 'widget': 'text',
     'rows': 1, 'group': 'titles', 'help': ''},
    {'key': 'features_title', 'label': 'Features title', 'widget': 'text',
     'rows': 1, 'group': 'titles', 'help': ''},
    {'key': 'info_title', 'label': 'Description title', 'widget': 'text',
     'rows': 1, 'group': 'titles', 'help': ''},
    {'key': 'buy_label', 'label': 'Buy button label', 'widget': 'text',
     'rows': 1, 'group': 'titles', 'help': ''},

    # -- the info rail --
    {'key': 'highlights', 'label': 'Product highlights', 'widget': 'textarea',
     'rows': 4, 'group': 'rail', 'help': 'One bullet per line.'},
    {'key': 'pay_chips', 'label': 'Payment chips', 'widget': 'text', 'rows': 1,
     'group': 'rail', 'help': 'Comma separated, e.g. Prepaid, COD.'},
    {'key': 'pay_bullets', 'label': 'Payment bullets', 'widget': 'textarea',
     'rows': 3, 'group': 'rail', 'help': 'One bullet per line.'},
    {'key': 'return_value', 'label': 'Return policy', 'widget': 'text',
     'rows': 1, 'group': 'rail', 'help': ''},
    {'key': 'warranty_value', 'label': 'Warranty', 'widget': 'text', 'rows': 1,
     'group': 'rail', 'help': ''},
    {'key': 'shipping_value', 'label': 'Shipping line', 'widget': 'text',
     'rows': 1, 'group': 'rail', 'help': ''},

    # -- checkout --
    {'key': 'ship_fee', 'label': 'Delivery fee', 'widget': 'text', 'rows': 1,
     'group': 'checkout',
     'help': 'A flat fee for this product\'s one-step checkout. Blank uses the '
             'store-wide fee, and a blank store-wide fee uses Delivery Charge '
             'Setup, which prices by district.'},
)

# The groups the product form draws, in order, with the heading each gets.
FIELD_GROUPS = (
    ('media', 'Videos and photos'),
    ('proof', 'Proof'),
    ('teach', 'How to use, ingredients and features'),
    ('titles', 'Section headings'),
    ('rail', 'Info rail'),
    ('checkout', 'Checkout'),
)

# The product form prefixes its inputs, because it posts into the same request
# as ProductForm, where a box called `videos` or `features` would be anyone's.
PRODUCT_FORM_PREFIX = 'pt_'


def _meta_fields():
    """key -> spec, for the drawer and the per-product save loop."""
    return dict((spec['key'], spec) for spec in FIELD_SPECS)


def save_product_theme(request, product):
    """Write this product's Theme 2 settings from a product add/edit post.

    Called from `product_add` and `product_edit`, which is where a shop owner
    is already standing when they think about the page a product gets. It is a
    no-op unless the panel was actually rendered - a post from anywhere else
    must never blank a product's landing page by omission.

    Returns True when something was written or cleared.
    """
    if request.POST.get(PRODUCT_FORM_PREFIX + 'present') != '1':
        return False

    layout = (request.POST.get(PRODUCT_FORM_PREFIX + 'layout') or '').strip()
    if layout and layout not in theme2.layouts():
        layout = ''

    values = {}
    for spec in FIELD_SPECS:
        key = spec['key']
        value = (request.POST.get(PRODUCT_FORM_PREFIX + key) or '').strip()
        model_field = ProductThemeOverride._meta.get_field(key)
        if model_field.max_length:
            value = value[:model_field.max_length]
        values[key] = value

    row = ProductThemeOverride.objects.filter(product=product).first()

    # Nothing filled in at all says nothing at all: keeping an empty row would
    # only make the "products with their own settings" count lie.
    if not layout and not any(values.values()):
        if row is not None:
            row.delete()
            theme2.invalidate_cache()
            return True
        return False

    if row is None:
        row = ProductThemeOverride(product=product)
    row.layout = layout
    for key, value in values.items():
        setattr(row, key, value)
    row.save()
    theme2.invalidate_cache()
    return True


def product_theme_form_context(product=None, post=None):
    """What the panel on the product add/edit form needs to draw itself.

    A product that does not exist yet (the add form) simply has no row, so
    every box opens blank and every placeholder shows the global value - which
    is exactly what the unsaved product will inherit.

    `post` is the rejected request when the product form failed validation.
    Re-reading it is not a nicety: a page written across a dozen boxes and then
    thrown away because the *price* field was empty is the kind of loss nobody
    forgives.
    """
    row = None
    if product is not None and product.pk:
        row = ProductThemeOverride.objects.filter(product=product).first()
    snapshot = theme2.settings_snapshot()

    resubmitted = post if post is not None and post.get(
        PRODUCT_FORM_PREFIX + 'present') == '1' else None

    def current(key):
        if resubmitted is not None:
            return resubmitted.get(PRODUCT_FORM_PREFIX + key, '')
        return getattr(row, key, '') if row else ''

    groups = []
    for group_key, group_label in FIELD_GROUPS:
        fields = []
        for spec in FIELD_SPECS:
            if spec['group'] != group_key:
                continue
            inherited = snapshot.get(spec['key'], '') or theme2.DEFAULTS.get(spec['key'], '')
            fields.append(dict(
                spec,
                name=PRODUCT_FORM_PREFIX + spec['key'],
                value=current(spec['key']),
                # Only the first line: a placeholder is a hint, and the whole
                # of a five-line highlights list is not a hint.
                inherited=str(inherited).split(chr(10))[0],
            ))
        if fields:
            groups.append({
                'key': group_key,
                'label': group_label,
                'fields': fields,
                # Drives whether the section opens folded. A group that already
                # says something opens; the rest stay one line each, so an
                # untouched panel is six headings rather than two screens of
                # empty boxes.
                'filled': any(f['value'] for f in fields),
            })

    return {
        'pt_groups': groups,
        'pt_layout': (resubmitted.get(PRODUCT_FORM_PREFIX + 'layout', '')
                      if resubmitted is not None else (row.layout if row else '')),
        'pt_layout_choices': PRODUCT_LAYOUT_CHOICES,
        'pt_global_layout': dict(LAYOUT_CHOICES).get(
            snapshot.get('layout'), snapshot.get('layout')),
        'pt_global_is_theme2': snapshot.get('layout') == LAYOUT_THEME2,
        'pt_has_row': row is not None,
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
        'meta_fields': list(FIELD_SPECS),
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


# Who may upload: an administrator, or anyone the dashboard already trusts to
# create or edit a product - because the panel on the product form is one of
# the two places these run from, and a shop assistant who may write a product
# page must be able to put a photo in it.
#
# The check is a function rather than a decorator on purpose: the decorators
# answer a refusal with a redirect and a queued Django message, which is right
# for a page and wrong for an endpoint the page calls over fetch. The caller
# would get HTML it cannot parse, and the message would resurface as a stray
# error toast on whatever page loaded next.
MEDIA_PERMISSIONS = ('can_edit_products', 'can_create_products')


def _media_denied(request):
    if has_any_permission(request.user, *MEDIA_PERMISSIONS):
        return None
    return JsonResponse(
        {'success': False,
         'message': 'You do not have permission to manage product media.'},
        status=403)


def _media_payload(row):
    return {
        'id': row.pk,
        'url': row.url,
        'kind': row.kind,
        'title': str(row),
        'size': row.size_label,
    }


@login_required
def product_theme_media_list(request):
    """The uploaded photos and clips, newest first, for the picker grid."""
    denied = _media_denied(request)
    if denied is not None:
        return denied

    kind = (request.GET.get('kind') or '').strip()
    rows = ThemeMedia.objects.all()
    if kind in (ThemeMedia.KIND_IMAGE, ThemeMedia.KIND_VIDEO):
        rows = rows.filter(kind=kind)
    return JsonResponse({'media': [_media_payload(r) for r in rows[:120]]})


@login_required
@require_POST
def product_theme_media_upload(request):
    """Take one uploaded photo or clip and hand back the URL to insert.

    Refuses by extension and by size rather than trusting the browser's
    content type, which is trivially wrong and trivially forged.
    """
    denied = _media_denied(request)
    if denied is not None:
        return denied

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
@require_POST
def product_theme_media_delete(request, media_id):
    """Remove one upload. The file goes too — an orphaned blob nobody can see
    in the picker is just disk that never comes back."""
    denied = _media_denied(request)
    if denied is not None:
        return denied

    row = get_object_or_404(ThemeMedia, pk=media_id)
    if row.file:
        row.file.delete(save=False)
    row.delete()
    return JsonResponse({'success': True})
