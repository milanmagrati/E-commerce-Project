"""Storefront button wording — resolution, and nothing else.

The shop is Nepali and its buttons are read in two languages, so every word
printed on a storefront button is editable at Setup → Store Button Labels
rather than baked into a template. One singleton row (`store.StoreLabel`),
one rule::

    StoreLabel.<key>   when it has been filled in
    DEFAULTS[<key>]    otherwise

A blank column resolves to the shipped wording rather than to an empty
string: a button an admin cleared by accident must never render as an
unlabelled rectangle on the live shop.

Templates read the snapshot through the `store_labels` context variable, put
there by `store.context_processors.store_context`. Nothing reads the model
directly — add a key here and to `StoreLabel` together, never to one alone.
"""

import logging

from django.core.cache import cache

from .models import StoreLabel

logger = logging.getLogger(__name__)

# Read on every storefront page, so it is cached. LocMemCache is per process,
# so an edit reaches the other workers when this expires — short, because an
# admin renaming a button expects to see the new word straight away.
CACHE_KEY = 'store:labels:v1'
CACHE_TTL = 60

# The wording that ships. Every key here is a column on `StoreLabel`, and the
# setup screen draws exactly this list — so a label cannot be editable on the
# screen, missing from the model, and silently unsaved.
DEFAULTS = {
    'order_now': 'Order Now',
    'add_to_cart': 'Add to cart',
    'sold_out': 'Sold out',
    'save_for_later': 'Save for later',
    'saved': 'Saved',
    'form_title': 'Fill the order form',
    'form_subtitle': 'अर्डर / इन्क्वायरीको लागि फर्म भर्नुहोस्।',
    'confirm_title': 'Confirm Order',
    'confirm_sub': 'सामान लिने भएमा मात्र',
    'inquiry_title': 'Inquiry Only',
    'inquiry_sub': 'बुझ्नको लागि',
}

# What the setup screen draws, in the order it draws it: the key, the heading
# it sits under, its label and the line of help under the box.
FIELD_SPECS = (
    {'key': 'order_now', 'group': 'Product page',
     'label': 'Order Now button',
     'help': 'The big accent button that opens the order form.'},
    {'key': 'add_to_cart', 'group': 'Product page',
     'label': 'Add to cart button',
     'help': 'Also used on the sticky bar at the bottom of a phone screen.'},
    {'key': 'sold_out', 'group': 'Product page',
     'label': 'Sold out button',
     'help': 'Shown in place of the buy buttons when nothing is in stock.'},
    {'key': 'save_for_later', 'group': 'Product page',
     'label': 'Save for later link', 'help': 'The wishlist heart.'},
    {'key': 'saved', 'group': 'Product page',
     'label': 'Saved link', 'help': 'What the heart says once it is saved.'},

    {'key': 'form_title', 'group': 'Order form',
     'label': 'Form heading', 'help': 'The line at the top of the order panel.'},
    {'key': 'form_subtitle', 'group': 'Order form',
     'label': 'Form sub-heading', 'help': 'The smaller line under it.'},
    {'key': 'confirm_title', 'group': 'Order form',
     'label': 'Confirm button', 'help': 'Places a real order.'},
    {'key': 'confirm_sub', 'group': 'Order form',
     'label': 'Confirm button — small line',
     'help': 'The second, smaller line inside the same button.'},
    {'key': 'inquiry_title', 'group': 'Order form',
     'label': 'Inquiry button', 'help': 'Sends an enquiry rather than an order.'},
    {'key': 'inquiry_sub', 'group': 'Order form',
     'label': 'Inquiry button — small line',
     'help': 'The second, smaller line inside the same button.'},
)

GROUPS = ('Product page', 'Order form')


def invalidate_cache():
    """Called by Setup → Store Button Labels after every write."""
    cache.delete(CACHE_KEY)


def snapshot():
    """The singleton flattened to plain strings, cached.

    Never raises: an unmigrated database or a DB blip falls back to the
    shipped wording, which is the wording the shop had before this screen
    existed.
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    labels = dict(DEFAULTS)
    try:
        row = StoreLabel.objects.first()
        if row is not None:
            for key, fallback in DEFAULTS.items():
                value = (getattr(row, key, '') or '').strip()
                labels[key] = value or fallback
    except Exception as exc:  # unmigrated DB, DB blip — never break a page
        logger.warning('Store: button labels unreadable, using defaults: %s', exc)
        return dict(DEFAULTS)

    cache.set(CACHE_KEY, labels, CACHE_TTL)
    return labels
