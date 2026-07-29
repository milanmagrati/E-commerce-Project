# services/ncm_service.py
import requests
import logging
from django.conf import settings
from typing import Dict, List, Optional

logger = logging.getLogger('ncm')

class NCMService:
    """Service for NCM (Nepal Can Move) API Integration"""
    
    def __init__(self, api_config_id=None):
        if api_config_id:
            from dashboard.models import LogisticsAPIConfig
            try:
                config = LogisticsAPIConfig.objects.get(id=api_config_id, is_active=True, logistics_provider='ncm')
                self.api_key = config.api_key
                self.base_url = config.get_primary_base_url()
                self.base_url_v2 = config.get_base_url_v2() or self.base_url
            except LogisticsAPIConfig.DoesNotExist:
                self.api_key = settings.NCM_API_KEY
                self.base_url = settings.NCM_API_BASE_URL
                self.base_url_v2 = settings.NCM_API_BASE_URL_V2
        else:
            self.api_key = settings.NCM_API_KEY
            self.base_url = settings.NCM_API_BASE_URL
            self.base_url_v2 = settings.NCM_API_BASE_URL_V2
        self.headers = {
            'Authorization': f'Token {self.api_key}',
            'Content-Type': 'application/json'
        }
    
    def _make_request(self, method: str, url: str, data: Dict = None, params: Dict = None, timeout: int = None, _retry: bool = True):
        """Helper to make API requests.

        Transient failures (timeouts, connection resets, 5xx responses, or a
        non-JSON 200 body from a flaky upstream/proxy) are retried exactly
        once before giving up - but ONLY for GET requests. POST requests
        (create_order, return_order, create_exchange_order, comments, ...)
        are never auto-retried: if a POST times out or drops its response
        after NCM already processed it, retrying would risk creating a
        duplicate order/comment/return. Callers that need a POST retried
        must do so explicitly and idempotently at the call site.
        """
        if timeout is None:
            try:
                from dashboard.models import APISettings
                timeout = APISettings.get_settings().ncm_api_timeout
            except Exception:
                timeout = 30

        method = method.upper()
        can_retry = _retry and method == 'GET'

        try:
            if method == 'GET':
                response = requests.get(url, headers=self.headers, params=params, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, headers=self.headers, json=data, timeout=timeout)

            response.raise_for_status()
            try:
                return {'success': True, 'data': response.json(), 'status_code': response.status_code}
            except ValueError:
                # 200 OK but body isn't valid JSON (e.g. a proxy/gateway hiccup)
                if can_retry:
                    logger.warning(f"NCM API returned non-JSON body, retrying once: {url}")
                    return self._make_request(method, url, data, params, timeout, _retry=False)
                logger.error(f"NCM API returned non-JSON body: {url}")
                return {'success': False, 'error': 'Invalid response from NCM API', 'status_code': response.status_code}

        except requests.exceptions.Timeout:
            if can_retry:
                logger.warning(f"NCM API timeout, retrying once: {url}")
                return self._make_request(method, url, data, params, timeout, _retry=False)
            logger.error(f"NCM API timeout: {url}")
            return {'success': False, 'error': 'Request timeout'}

        except requests.exceptions.ConnectionError:
            if can_retry:
                logger.warning(f"NCM API connection error, retrying once: {url}")
                return self._make_request(method, url, data, params, timeout, _retry=False)
            logger.error(f"NCM API connection error: {url}")
            return {'success': False, 'error': 'Connection error'}

        except requests.exceptions.RequestException as e:
            status_code = getattr(getattr(e, 'response', None), 'status_code', None)

            # Log 404s as a debug/warning instead of a full error, since they are expected for missing orders
            if status_code == 404:
                logger.warning(f"NCM API 404 Not Found: {url}")
            else:
                logger.error(f"NCM API error: {str(e)}")

            # Retry once on server-side errors (5xx) - these are typically transient
            if can_retry and status_code and status_code >= 500:
                logger.warning(f"NCM API {status_code} error, retrying once: {url}")
                return self._make_request(method, url, data, params, timeout, _retry=False)

            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_msg = e.response.json()
                except (ValueError, AttributeError):
                    error_msg = e.response.text if hasattr(e.response, 'text') else str(e)
            return {'success': False, 'error': error_msg, 'status_code': status_code}

    def _fetch_comments(self, ncm_order_id: int):
        """Fetch comments for an NCM order, trying v2 then v1.

        The NCM API returns HTTP 404 when an order has NO comments yet.
        This is normal behaviour (not an error) — we treat 404 as an
        empty list so it never pollutes the terminal logs.
        """
        import requests as _req
        params = {'id': ncm_order_id}
        urls_to_try = [
            f"{self.base_url_v2}/order/comment",
            f"{self.base_url}/order/comment",
        ]
        for url in urls_to_try:
            try:
                if self.base_url_v2 == self.base_url and url == urls_to_try[1]:
                    # Skip duplicate when v2 == v1 (no second base URL configured)
                    break
                timeout = 30
                try:
                    from dashboard.models import APISettings
                    timeout = APISettings.get_settings().ncm_api_timeout
                except Exception:
                    pass
                resp = _req.get(url, headers=self.headers, params=params, timeout=timeout)
                if resp.status_code == 404:
                    # Might be missing endpoint (v2) or no comments. Let's try fallback.
                    logger.debug(f"NCM: 404 returned for order {ncm_order_id} at {url}, trying next...")
                    continue
                resp.raise_for_status()
                return {'success': True, 'data': resp.json()}
            except _req.exceptions.Timeout:
                logger.warning(f"NCM comment fetch timeout: {url}")
                continue
            except _req.exceptions.RequestException as e:
                status = getattr(getattr(e, 'response', None), 'status_code', None)
                if status == 404:
                    logger.debug(f"NCM: 404 returned for order {ncm_order_id} at {url}, trying next...")
                    continue
                logger.warning(f"NCM comment fetch error ({url}): {e}")
                continue
        # All URLs failed — return empty rather than error to keep UI clean
        return {'success': True, 'data': []}

    def _post_comment(self, ncm_order_id: int, comment: str):
        """Post a comment to NCM, trying v2 then v1."""
        data = {'orderid': ncm_order_id, 'comments': comment}
        result = self._make_request('POST', f"{self.base_url_v2}/order/comment", data=data)
        if not result['success']:
            result = self._make_request('POST', f"{self.base_url}/comment", data=data)
        return result
    
    def get_branches(self):
        """Get list of NCM branches"""
        url = f"{self.base_url_v2}/branches"
        return self._make_request('GET', url)
    
    def get_shipping_rate(self, from_branch: str, to_branch: str, delivery_type: str = 'Door2Door'):
        """Calculate shipping rate between branches"""
        url = f"{self.base_url}/shipping-rate"
        params = {
            'creation': from_branch,
            'destination': to_branch,
            'type': delivery_type
        }
        return self._make_request('GET', url, params=params)
    
    def create_order(self, order_data: Dict):
        """Create order in NCM system"""
        url = f"{self.base_url}/order/create"
        
        # Validate required fields
        required = ['name', 'phone', 'cod_charge', 'address', 'fbranch', 'branch']
        for field in required:
            if not order_data.get(field):
                logger.error(f"Required field missing: {field}")
                return {'success': False, 'error': f'Missing required field: {field}'}
        
        # Clean phone number
        original_phone = order_data.get('phone')
        order_data['phone'] = self._clean_phone(order_data['phone'])
        logger.info(f"Phone cleaning: {original_phone} -> {order_data['phone']}")
        
        if not order_data['phone']:
            logger.error(f"Phone number became empty after cleaning: {original_phone}")
            return {'success': False, 'error': 'Invalid phone number format'}
        
        if order_data.get('phone2'):
            order_data['phone2'] = self._clean_phone(order_data['phone2'])
        
        logger.info(f"Sending to NCM API: POST {url}")
        logger.info(f"Order Data: {order_data}")
        
        result = self._make_request('POST', url, data=order_data)
        
        if result['success']:
            logger.info(f"[SUCCESS] NCM Order created successfully: {result['data']}")
        else:
            logger.error(f"[FAILED] NCM Order creation failed")
            logger.error(f"  Error: {result.get('error')}")
            logger.error(f"  URL: {url}")
            logger.error(f"  Data sent: {order_data}")
        
        return result
    
    def get_order_details(self, ncm_order_id: int, timeout: int = None):
        """Get order details from NCM"""
        url = f"{self.base_url}/order"
        params = {'id': ncm_order_id}
        return self._make_request('GET', url, params=params, timeout=timeout)
    
    def get_order_status(self, ncm_order_id: int):
        """Get order status history"""
        url = f"{self.base_url}/order/status"
        params = {'id': ncm_order_id}
        return self._make_request('GET', url, params=params)
    
    def get_bulk_order_statuses(self, order_ids: List[int]):
        """Get statuses for multiple orders"""
        url = f"{self.base_url}/orders/statuses"
        data = {'orders': order_ids}
        return self._make_request('POST', url, data=data)

    def get_staff_comments(self, ncm_order_id: int):
        """Fetch NCM order comments, returning only NCM Staff entries.

        404 from NCM = no comments yet (treated as empty list, not an error).
        """
        result = self._fetch_comments(ncm_order_id)
        if result['success']:
            raw = result['data']
            items = raw if isinstance(raw, list) else raw.get('data', raw.get('results', [])) if isinstance(raw, dict) else []
            if isinstance(items, dict):
                items = [items]
            staff_comments = [
                {
                    'comment': item.get('comments', ''),
                    'created_by': item.get('addedBy', 'NCM Staff'),
                    'created_at': item.get('added_time', ''),
                    'role': 'ncm',
                    'is_ncm_staff': True,
                }
                for item in items
                if isinstance(item, dict) and item.get('addedBy') == 'NCM Staff'
            ]
            return {'success': True, 'data': staff_comments}
        return result

    def get_order_comments(self, ncm_order_id: int):
        """Fetch all comments for an NCM order (all authors).

        404 from NCM = no comments yet (treated as empty list, not an error).
        """
        result = self._fetch_comments(ncm_order_id)
        if result['success']:
            raw = result['data']
            items = raw if isinstance(raw, list) else raw.get('data', raw.get('results', [])) if isinstance(raw, dict) else []
            if isinstance(items, dict):
                items = [items]
            comments = [
                {
                    'comment': item.get('comments', item.get('comment', '')),
                    'added_by': item.get('addedBy', item.get('added_by', 'Unknown')),
                    'added_time': item.get('added_time', item.get('created_at', '')),
                }
                for item in items
                if isinstance(item, dict)
            ]
            return {'success': True, 'data': comments}
        return result

    def create_order_comment(self, ncm_order_id: int, comment: str):
        """Add comment to NCM order (tries v2 then v1)."""
        return self._post_comment(ncm_order_id, comment)

    def get_vendor_rtvs_by_status(self, page_size: int = 500, include_recent: bool = True):
        """Fetch active RTVs using the status filter — fast path.

        Instead of scanning all 336+ pages of /vendor/orders, this queries
        only the RTV-relevant statuses (Arrived, Dispatched, Sent to Vendor,
        Returned to Warehouse) which typically total ~200 orders, fetchable
        in 4 API calls.

        When include_recent=True (default), also scans the first 3 pages of
        ALL orders (most recent 1500) to catch newly-marked RTVs that may
        still be in "Delivered" status.

        Returns:
            {'success': True, 'data': [<order dict>, ...]}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        rtv_statuses = ['Arrived', 'Dispatched', 'Sent to Vendor', 'Returned to Warehouse']
        rtvs = []

        def _fetch_status(status):
            """Fetch all pages for a given status."""
            status_rtvs = []
            page = 1
            while True:
                result = self._make_request(
                    'GET',
                    f"{self.base_url_v2}/vendor/orders",
                    params={'page': page, 'page_size': page_size, 'status': status},
                )
                if not result['success']:
                    break
                raw = result['data']
                results = raw.get('results', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                if not results:
                    break
                for order in results:
                    if NCMService.parse_vendor_return(order.get('vendor_return')):
                        status_rtvs.append(order)
                # Check for next page
                if isinstance(raw, dict) and raw.get('next'):
                    page += 1
                else:
                    break
            return status_rtvs

        def _fetch_recent_pages():
            """Fetch first 3 pages of all orders to catch newly-marked RTVs."""
            recent_rtvs = []
            for page in range(1, 4):
                result = self._make_request(
                    'GET',
                    f"{self.base_url_v2}/vendor/orders",
                    params={'page': page, 'page_size': page_size},
                )
                if not result['success']:
                    break
                raw = result['data']
                results = raw.get('results', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                if not results:
                    break
                for order in results:
                    if NCMService.parse_vendor_return(order.get('vendor_return')):
                        recent_rtvs.append(order)
            return recent_rtvs

        tasks = list(rtv_statuses)
        if include_recent:
            tasks.append('__recent__')

        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {}
            for task in tasks:
                if task == '__recent__':
                    futures[executor.submit(_fetch_recent_pages)] = task
                else:
                    futures[executor.submit(_fetch_status, task)] = task
            for future in as_completed(futures):
                try:
                    rtvs.extend(future.result())
                except Exception:
                    pass

        # Deduplicate by orderid
        seen = set()
        unique_rtvs = []
        for o in rtvs:
            oid = o.get('orderid') or o.get('id')
            if oid and oid not in seen:
                seen.add(oid)
                unique_rtvs.append(o)

        return {'success': True, 'data': unique_rtvs}

    def get_vendor_rtvs(self, max_pages: int = 50, page_size: int = 200,
                         known_ids: set = None, scan_all: bool = False):
        """Fetch vendor orders with vendor_return=True from NCM.

        Paginates through ``/vendor/orders`` and collects entries where
        ``vendor_return`` is truthy (handles both bool and string).

        Args:
            max_pages: Maximum pages to fetch (ignored when scan_all=True).
            page_size: Orders per page.
            known_ids: Set of already-known RTV order IDs.  When provided
                       and scan_all is False, pagination stops early after
                       3 consecutive pages with zero new RTVs.
            scan_all:  When True, dynamically compute max_pages from the
                       API ``count`` field and scan every page.

        Returns:
            {'success': True, 'data': [<order dict>, ...], 'pages_scanned': int}
        """
        rtvs = []
        consecutive_empty = 0
        pages_scanned = 0
        dynamic_max = max_pages

        for page in range(1, 9999):
            if not scan_all and page > max_pages:
                break
            if scan_all and page > dynamic_max:
                break

            result = self._make_request(
                'GET',
                f"{self.base_url_v2}/vendor/orders",
                params={'page': page, 'page_size': page_size},
            )
            if not result['success']:
                break
            raw_data = result['data']
            # Handle both paginated (dict with 'results') and flat list responses
            if isinstance(raw_data, list):
                results = raw_data
            elif isinstance(raw_data, dict):
                results = raw_data.get('results', [])
                # Dynamically set max pages from API count on first page
                if scan_all and page == 1:
                    total_count = raw_data.get('count', 0)
                    if total_count > 0:
                        import math
                        dynamic_max = math.ceil(total_count / page_size)
            else:
                break
            if not results:
                break

            pages_scanned = page
            new_on_page = 0
            for order in results:
                if NCMService.parse_vendor_return(order.get('vendor_return')):
                    rtvs.append(order)
                    if known_ids is not None:
                        oid = order.get('orderid') or order.get('id') or order.get('pk') or order.get('order_id')
                        if oid and oid not in known_ids:
                            new_on_page += 1

            # Early exit for incremental sync: stop after 3 consecutive
            # pages with no new RTVs (not just 1 — RTVs can be sparse).
            if known_ids is not None and not scan_all:
                if new_on_page == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= 3 and page > 3:
                        break
                else:
                    consecutive_empty = 0

            # Stop if we've exhausted all pages
            if isinstance(raw_data, list) or not raw_data.get('next'):
                break
        return {'success': True, 'data': rtvs, 'pages_scanned': pages_scanned}

    def get_vendor_rtvs_parallel(self, max_workers: int = 300):
        """Fetch ALL vendor RTVs using parallel HTTP requests (fastest path).

        Uses a shared requests.Session with a large connection pool to fire
        all API pages concurrently.  The NCM API caps page size at 100, so
        total pages ≈ ceil(total_orders / 100) ≈ 336 for ~33k orders.
        With 300 workers the full scan completes in ~15s vs ~370s sequential.

        Returns:
            {'success': True, 'data': [<order dict>, ...], 'total_pages': int,
             'error': str|None}
        """
        import math
        import requests as req_lib
        from concurrent.futures import ThreadPoolExecutor, as_completed

        page_size = 100  # NCM API hard cap

        # Shared session with a connection pool large enough for all workers
        session = req_lib.Session()
        adapter = req_lib.adapters.HTTPAdapter(
            pool_connections=max_workers + 10,
            pool_maxsize=max_workers + 10,
        )
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        session.headers.update(self.headers)

        def _fetch(page_num):
            try:
                r = session.get(
                    f'{self.base_url_v2}/vendor/orders',
                    params={'page': page_num, 'page_size': page_size},
                    timeout=30,
                )
                r.raise_for_status()
                d = r.json()
                results = d.get('results', []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
                return [o for o in results if NCMService.parse_vendor_return(o.get('vendor_return'))]
            except Exception:
                return []

        # Page 1 — determines total page count
        try:
            r1 = session.get(
                f'{self.base_url_v2}/vendor/orders',
                params={'page': 1, 'page_size': page_size},
                timeout=20,
            )
            r1.raise_for_status()
            data1 = r1.json()
        except Exception as exc:
            session.close()
            return {'success': False, 'error': str(exc), 'data': [], 'total_pages': 0}

        results1 = data1.get('results', []) if isinstance(data1, dict) else (data1 if isinstance(data1, list) else [])
        total_count = data1.get('count', 0) if isinstance(data1, dict) else len(results1)
        total_pages = max(1, math.ceil(total_count / page_size)) if total_count else 1

        rtvs = [o for o in results1 if NCMService.parse_vendor_return(o.get('vendor_return'))]

        if total_pages > 1:
            workers = min(max_workers, total_pages - 1)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_fetch, p): p for p in range(2, total_pages + 1)}
                for future in as_completed(futures):
                    try:
                        rtvs.extend(future.result())
                    except Exception:
                        pass

        session.close()
        return {'success': True, 'data': rtvs, 'total_pages': total_pages, 'error': None}

    def return_order(self, ncm_order_id: int, comment: str = None):
        """Mark order for return"""
        url = f"{self.base_url_v2}/vendor/order/return"
        data = {'pk': ncm_order_id}
        if comment:
            data['comment'] = comment
        return self._make_request('POST', url, data=data)

    def create_exchange_order(self, ncm_order_id: int):
        """Create an exchange order in NCM system.
        
        Returns cust_order and ven_order IDs on success.
        """
        url = f"{self.base_url_v2}/vendor/order/exchange-create"
        data = {'pk': ncm_order_id}
        logger.info(f"Creating NCM exchange order for NCM ID: {ncm_order_id}")
        result = self._make_request('POST', url, data=data)
        if result['success']:
            logger.info(f"[SUCCESS] NCM Exchange order created: cust_order={result['data'].get('cust_order')}, ven_order={result['data'].get('ven_order')}")
        else:
            logger.error(f"[FAILED] NCM Exchange order creation failed for NCM ID {ncm_order_id}: {result.get('error')}")
        return result
    
    def set_webhook_url(self, webhook_url: str):
        """Register webhook URL"""
        url = f"{self.base_url_v2}/vendor/webhook"
        data = {'webhook_url': webhook_url}
        return self._make_request('POST', url, data=data)
    
    def test_webhook(self, webhook_url: str):
        """Test webhook URL"""
        url = f"{self.base_url_v2}/vendor/webhook/test"
        data = {'webhook_url': webhook_url}
        return self._make_request('POST', url, data=data)
    
    @staticmethod
    def _clean_phone(phone: str) -> str:
        """Clean phone number"""
        if not phone:
            return ""
        return ''.join(filter(str.isdigit, str(phone)))
    
    #: NCM statuses that mean an order is somewhere in the return-to-vendor
    #: pipeline but NOT yet confirmed as physically received at the vendor.
    #: Kept as a set (not just dict values) so the substring fallback below
    #: can recognize branch-qualified variants NCM sends from its
    #: tracking/history endpoints, e.g. "Arrived at RETURN (TINKUNE)" or
    #: "Dispatched to RETURN (TINKUNE)", which don't exactly match any fixed
    #: key. Only the literal "Returned to Warehouse" string (handled as an
    #: exact dict entry below) represents confirmed arrival.
    RETURN_STATUS_KEYWORDS = ('return', 'rtv', 'sent to vendor')

    @staticmethod
    def map_ncm_status_to_system(ncm_status: str) -> str:
        """Map NCM status to system status.

        This mapping is aligned with NCMWebhookHandler.STATUS_MAPPING to ensure
        consistent behavior between webhook updates and manual sync operations.
        """
        mapping = {
            'Pickup Order Created': 'Pickup Created',
            'Drop off Order Created': 'Pickup Created',
            'Pickup Complete': 'in_transit',
            'Drop off Order Collected': 'in_transit',
            'Dispatched': 'in_transit',
            'In Transit': 'in_transit',
            'Arrived': 'in_transit',
            'Sent for Delivery': 'in_transit',
            'Out for Delivery': 'in_transit',
            'Delivered': 'delivered',
            'Confirmed': 'delivered',
            'Returned': 'returned',
            'Return Initiated': 'return_initiated',
            'Return Approved': 'return_approved',
            'Order Marked Return': 'return_processing',
            'Sent to Vendor': 'return_processing',
            'Returned to Warehouse': 'return',
        }
        if ncm_status in mapping:
            return mapping[ncm_status]

        # Fallback: any status mentioning a return/RTV keyword (including
        # branch-qualified variants NCM doesn't send a fixed key for) means
        # the order is somewhere in the return-to-vendor pipeline but not yet
        # confirmed as physically received - treat as the intermediate stage.
        if ncm_status and any(kw in ncm_status.lower() for kw in NCMService.RETURN_STATUS_KEYWORDS):
            return 'return_processing'

        return 'processing'

    @staticmethod
    def parse_vendor_return(value) -> bool:
        """Parse the vendor_return flag from NCM API response.
        The API returns this as a string ('True'/'False')."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() == 'true'
        return False

    @staticmethod
    def resolve_delivered_status(status_entry: dict) -> tuple:
        """Resolve the actual order status and payment status from an NCM status entry.

        The NCM API returns status='Delivered' for both successful deliveries and
        vendor returns (RTV) - the vendor_return flag differentiates them. That
        flag can also accompany other statuses throughout the RTV pipeline
        (e.g. "Sent to Vendor", "Order Marked Return", "Returned to
        Warehouse"), so whenever it's confirmed true the order is treated as
        returned ('return') regardless of the raw NCM status text - NCM only
        sets/reports this flag once it has actually confirmed the order is in
        the RTV pipeline, so the flag itself is treated as sufficient.
        'return_processing' (the earlier "marked for return, not yet
        confirmed" stage) is only produced via map_ncm_status_to_system below,
        for raw statuses like "Order Marked Return"/"Sent to Vendor" seen
        WITHOUT the vendor_return flag - e.g. the initial `order_marked_rtv`
        webhook event, whose payload typically doesn't include vendor_return
        yet.

        Returns:
            (system_status, payment_status) tuple
        """
        ncm_status = status_entry.get('status') or status_entry.get('Status', '')
        vendor_return_raw = status_entry.get('vendor_return', status_entry.get('vendorReturn', 'False'))
        vendor_return = NCMService.parse_vendor_return(vendor_return_raw)

        if vendor_return:
            return ('return', None)  # Confirmed in the RTV pipeline (vendor_return flag)

        if ncm_status == 'Delivered':
            return ('delivered', 'paid')  # Successful delivery, mark as paid

        # For non-Delivered statuses, use standard mapping
        system_status = NCMService.map_ncm_status_to_system(ncm_status)
        return (system_status, None)

    @staticmethod
    def sync_order_status_fields(order, system_status, payment_status=None):
        """Update all status-related fields on an order to keep them in sync.

        Updates: status, order_status, status_setup (FK),
                 payment_status, payment_status_setup (FK).

        Only assigns/reports a field when its value actually differs from the
        current one, so callers (e.g. the NCM real-time sync API) can trust
        an empty return value to mean "nothing changed" - important because
        that signal drives whether the order detail page reloads itself.

        Returns list of field names that were modified (for use in update_fields).
        """
        update_fields = []

        # Guard against a caller passing an empty/None status - writing that
        # through would blank the order's status rather than leave it alone.
        if not system_status:
            return update_fields

        # Update status and order_status string fields
        if order.status != system_status:
            order.status = system_status
            update_fields.append('status')
        if order.order_status != system_status:
            order.order_status = system_status
            update_fields.append('order_status')

        # Link the matching Setup FK for order status. Wrapped defensively:
        # a lookup problem must not prevent the status strings above from
        # being saved.
        try:
            status_setup = NCMService._resolve_setup('status', system_status)
            if status_setup and order.status_setup_id != status_setup.id:
                order.status_setup = status_setup
                update_fields.append('status_setup')
        except Exception:
            logger.exception(f"Could not resolve status Setup for '{system_status}'")

        # Update payment status
        if payment_status:
            if order.payment_status != payment_status:
                order.payment_status = payment_status
                update_fields.append('payment_status')

            try:
                ps_setup = NCMService._resolve_setup('payment_status', payment_status)
                if ps_setup and order.payment_status_setup_id != ps_setup.id:
                    order.payment_status_setup = ps_setup
                    update_fields.append('payment_status_setup')
            except Exception:
                logger.exception(f"Could not resolve payment_status Setup for '{payment_status}'")

        return update_fields

    @staticmethod
    def _resolve_setup(setup_type, value):
        """Find the active Setup row whose name matches a system status value.

        Setup names are human-readable ("Return Processing") while system
        status values are normalized ("return_processing"), so matching
        compares both sides normalized. The indexed name__iexact lookup
        resolves the usual case in a single query; the full scan below is
        only reached for names that normalize equal without matching
        literally (e.g. one that already contains underscores), which keeps
        this correct while avoiding a table scan per order during bulk sync.
        """
        from dashboard.models import Setup

        if not value:
            return None

        normalized = str(value).lower().replace(' ', '_')
        candidates = Setup.objects.filter(setup_type=setup_type, is_active=True)

        match = candidates.filter(name__iexact=normalized.replace('_', ' ')).first()
        if match:
            return match

        for s in candidates:
            if s.name.lower().replace(' ', '_') == normalized:
                return s
        return None
