# services/woocommerce_service.py
import logging

import requests
from django.conf import settings

logger = logging.getLogger('integrations')


class WooCommerceService:
    """Thin client for WooCommerce's REST API - used for pull/poll sync until
    the push webhook is wired up. Needs a read-capable consumer key/secret
    from WooCommerce > Settings > Advanced > REST API in wp-admin.

    WooCommerce only accepts HTTP Basic Auth (consumer key/secret) over
    HTTPS - an http:// site URL would need OAuth1.0a signing instead, which
    this client doesn't implement.
    """

    def __init__(self):
        self.base_url = (settings.WOOCOMMERCE_SITE_URL or '').rstrip('/')
        self.auth = (settings.WOOCOMMERCE_CONSUMER_KEY, settings.WOOCOMMERCE_CONSUMER_SECRET)

    def fetch_orders_page(self, page=1, per_page=50, after=None, modified_after=None):
        if not self.base_url or not self.auth[0] or not self.auth[1]:
            raise ValueError(
                'WOOCOMMERCE_SITE_URL / WOOCOMMERCE_CONSUMER_KEY / WOOCOMMERCE_CONSUMER_SECRET '
                'must be set in .env before polling can run.'
            )

        params = {'page': page, 'per_page': per_page, 'orderby': 'date', 'order': 'asc'}
        if after:
            params['after'] = after
        if modified_after:
            params['modified_after'] = modified_after

        url = f'{self.base_url}/wp-json/wc/v3/orders'
        response = requests.get(url, params=params, auth=self.auth, timeout=30)
        response.raise_for_status()
        return response.json()

    def fetch_all_orders(self, after=None, modified_after=None, per_page=50, max_pages=20):
        """Yield orders across every page until WooCommerce returns a short page
        (or max_pages is hit, so a manual/on-demand sync can't run away)."""
        page = 1
        while page <= max_pages:
            batch = self.fetch_orders_page(
                page=page, per_page=per_page, after=after, modified_after=modified_after,
            )
            if not batch:
                break
            yield from batch
            if len(batch) < per_page:
                break
            page += 1
