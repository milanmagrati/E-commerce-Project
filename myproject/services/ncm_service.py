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
    
    def _make_request(self, method: str, url: str, data: Dict = None, params: Dict = None, timeout: int = None):
        """Helper to make API requests"""
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=self.headers, params=params, timeout=timeout or 15)
            elif method.upper() == 'POST':
                response = requests.post(url, headers=self.headers, json=data, timeout=timeout or 30)
            
            response.raise_for_status()
            return {'success': True, 'data': response.json(), 'status_code': response.status_code}
        
        except requests.exceptions.Timeout:
            logger.error(f"NCM API timeout: {url}")
            return {'success': False, 'error': 'Request timeout'}
        
        except requests.exceptions.RequestException as e:
            logger.error(f"NCM API error: {str(e)}")
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_msg = e.response.json()
                except (ValueError, AttributeError):
                    error_msg = e.response.text if hasattr(e.response, 'text') else str(e)
            return {'success': False, 'error': error_msg}
    
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
            logger.info(f"✓ NCM Order created successfully: {result['data']}")
        else:
            logger.error(f"✗ NCM Order creation failed")
            logger.error(f"  Error: {result.get('error')}")
            logger.error(f"  URL: {url}")
            logger.error(f"  Data sent: {order_data}")
        
        return result
    
    def get_order_details(self, ncm_order_id: int):
        """Get order details from NCM"""
        url = f"{self.base_url}/order"
        params = {'id': ncm_order_id}
        return self._make_request('GET', url, params=params)
    
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
        """Fetch NCM order comments via GET /order/comment?id=<ncm_order_id>.

        NCM API docs:
          GET /api/v1/order/comment?id=ORDERID
          Returns a list: [{orderid, comments, addedBy, added_time}, ...]

        Returns only comments where addedBy == 'NCM Staff'.
        """
        url = f"{self.base_url}/order/comment"
        params = {'id': ncm_order_id}
        result = self._make_request('GET', url, params=params)
        if result['success']:
            raw = result['data']
            # API returns a flat JSON list
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict):
                # Fallback if wrapped in an object
                items = raw.get('data', raw.get('results', []))
                if isinstance(items, dict):
                    items = [items]
            else:
                items = []

            staff_comments = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get('addedBy') == 'NCM Staff':
                    staff_comments.append({
                        'comment': item.get('comments', ''),
                        'created_by': item.get('addedBy', 'NCM Staff'),
                        'created_at': item.get('added_time', ''),
                        'role': 'ncm',
                        'is_ncm_staff': True,
                    })
            return {'success': True, 'data': staff_comments}
        return result

    def get_order_comments(self, ncm_order_id: int):
        """Fetch all comments for an NCM order (all authors)"""
        url = f"{self.base_url}/order/comment"
        params = {'id': ncm_order_id}
        result = self._make_request('GET', url, params=params)
        if result['success']:
            raw = result['data']
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict):
                items = raw.get('data', raw.get('results', []))
                if isinstance(items, dict):
                    items = [items]
            else:
                items = []
            comments = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                comments.append({
                    'comment': item.get('comments', item.get('comment', '')),
                    'added_by': item.get('addedBy', item.get('added_by', 'Unknown')),
                    'added_time': item.get('added_time', item.get('created_at', '')),
                })
            return {'success': True, 'data': comments}
        return result

    def create_order_comment(self, ncm_order_id: int, comment: str):
        """Add comment to NCM order"""
        url = f"{self.base_url}/comment"
        data = {'orderid': ncm_order_id, 'comments': comment}
        return self._make_request('POST', url, data=data)

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
            logger.info(f"✓ NCM Exchange order created: cust_order={result['data'].get('cust_order')}, ven_order={result['data'].get('ven_order')}")
        else:
            logger.error(f"✗ NCM Exchange order creation failed for NCM ID {ncm_order_id}: {result.get('error')}")
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
    
    @staticmethod
    def map_ncm_status_to_system(ncm_status: str) -> str:
        """Map NCM status to system status.

        This mapping is aligned with NCMWebhookHandler.STATUS_MAPPING to ensure
        consistent behavior between webhook updates and manual sync operations.
        """
        mapping = {
            'Pickup Order Created': 'processing',
            'Drop off Order Created': 'processing',
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
        }
        return mapping.get(ncm_status, 'processing')

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
        """Resolve the actual order status and payment status when NCM reports 'Delivered'.

        The NCM API returns status='Delivered' for both successful deliveries and
        vendor returns. The vendor_return flag differentiates them.

        Returns:
            (system_status, payment_status) tuple
        """
        ncm_status = status_entry.get('status') or status_entry.get('Status', '')
        vendor_return_raw = status_entry.get('vendor_return', status_entry.get('vendorReturn', 'False'))

        if ncm_status == 'Delivered':
            vendor_return = NCMService.parse_vendor_return(vendor_return_raw)
            if vendor_return:
                return ('return', None)  # Returned to vendor, no payment update
            else:
                return ('delivered', 'paid')  # Successful delivery, mark as paid

        # For non-Delivered statuses, use standard mapping
        system_status = NCMService.map_ncm_status_to_system(ncm_status)
        return (system_status, None)

    @staticmethod
    def sync_order_status_fields(order, system_status, payment_status=None):
        """Update all status-related fields on an order to keep them in sync.

        Updates: status, order_status, status_setup (FK),
                 payment_status, payment_status_setup (FK).

        Returns list of field names that were modified (for use in update_fields).
        """
        from dashboard.models import Setup

        update_fields = []

        # Update status and order_status string fields
        order.status = system_status
        order.order_status = system_status
        update_fields.extend(['status', 'order_status'])

        # Try to find matching Setup FK for order status
        try:
            # Match by converting Setup name to the same format as system_status
            # e.g. Setup name "Delivered" -> "delivered", "In Transit" -> "in_transit"
            status_setup = None
            for s in Setup.objects.filter(setup_type='status', is_active=True):
                if s.name.lower().replace(' ', '_') == system_status:
                    status_setup = s
                    break
            if status_setup:
                order.status_setup = status_setup
                update_fields.append('status_setup')
        except Exception:
            pass

        # Update payment status
        if payment_status:
            order.payment_status = payment_status
            update_fields.append('payment_status')

            # Try to find matching Setup FK for payment status
            try:
                ps_setup = None
                for s in Setup.objects.filter(setup_type='payment_status', is_active=True):
                    if s.name.lower().replace(' ', '_') == payment_status:
                        ps_setup = s
                        break
                if ps_setup:
                    order.payment_status_setup = ps_setup
                    update_fields.append('payment_status_setup')
            except Exception:
                pass

        return update_fields
