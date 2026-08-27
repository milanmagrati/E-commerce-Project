# services/ncm_service.py
import re
import threading
import time
import requests
import logging
from collections import defaultdict, deque
from django.conf import settings
from typing import Dict, List, Optional

logger = logging.getLogger('ncm')

#: NCM rate-limits per API account, not per IP, and the window is one second.
#: Measured against the live API: 3 requests landing inside the same second
#: succeed, the 4th comes back 429 "Request was throttled. Expected available
#: in 1 second." Rejected requests are not counted against the window, so a
#: retry a second later always gets through.
#:
#: The limit is scoped to the v1 order endpoints (/order, /order/status,
#: /order/comment). The v2 vendor endpoints are not throttled at all - 102
#: concurrent /vendor/orders pages came back 200 across the board, and running
#: that scan alongside order-status polls did not throttle either one. So the
#: gate below deliberately covers v1 only; pacing the RTV screen's v2 calls
#: would cost it seconds for nothing.
#:
#: This matters because several code paths fan out deliberately - the order
#: detail endpoint alone fires details+status+comments in parallel - and a
#: single page load used to overshoot the window and report the throttle to
#: the user as "could not load status history".
NCM_MAX_CALLS_PER_SECOND = 3
NCM_RATE_WINDOW_SECONDS = 1.0
#: Upper bound on a single backoff sleep, so a misbehaving Retry-After cannot
#: pin a request thread for minutes.
NCM_MAX_BACKOFF_SECONDS = 5.0
#: How many times a throttled GET is re-sent before giving up. Two is enough
#: for the observed one-second window; more would just queue behind the gate.
NCM_THROTTLE_RETRIES = 2

_rate_lock = threading.Lock()
#: api_key -> timestamps (monotonic) of requests admitted in the last window.
_rate_history = defaultdict(deque)


def _await_rate_slot(api_key):
    """Block until this NCM account has room in its one-second window.

    Keeps our own concurrent fan-out under NCM's limit instead of spending a
    request to discover it is over. In-process only: it cannot see requests
    from another worker process, which is why the 429 retry below stays as the
    backstop rather than being replaced by this.
    """
    key = api_key or ''
    while True:
        with _rate_lock:
            now = time.monotonic()
            recent = _rate_history[key]
            while recent and now - recent[0] >= NCM_RATE_WINDOW_SECONDS:
                recent.popleft()
            if len(recent) < NCM_MAX_CALLS_PER_SECOND:
                recent.append(now)
                return
            wait = NCM_RATE_WINDOW_SECONDS - (now - recent[0])
        # Sleep outside the lock so other threads can still drain the window.
        time.sleep(min(max(wait, 0.01), NCM_RATE_WINDOW_SECONDS))


def _throttle_wait_seconds(response):
    """How long NCM says to wait before retrying a 429, in seconds.

    Prefers the Retry-After header; falls back to parsing DRF's own wording
    ("Expected available in 1 second."), which is what NCM actually sends.
    """
    retry_after = None
    try:
        retry_after = response.headers.get('Retry-After')
    except Exception:
        retry_after = None
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except (TypeError, ValueError):
            pass

    body = ''
    try:
        body = response.text or ''
    except Exception:
        body = ''
    match = re.search(r'available in (\d+(?:\.\d+)?) second', body)
    if match:
        try:
            return max(float(match.group(1)), 0.0)
        except (TypeError, ValueError):
            pass
    return 1.0


def _is_throttle_error(error):
    """True if a failed NCM result was a rate-limit rejection, not a real error.

    A throttle says nothing about whether this account owns the order, so
    callers that sweep accounts must not read it as "wrong account".
    """
    if isinstance(error, dict):
        error = error.get('detail') or error.get('error') or ''
    return 'throttled' in str(error or '').lower()


def _same_instant(a, b):
    """True if two datetimes represent the same point in time, ignoring
    sub-second precision (repeat NCM comment polls can re-parse the same
    comment to microsecond-jittered values)."""
    if a is None or b is None:
        return a is b
    return a.replace(microsecond=0) == b.replace(microsecond=0)


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
        #: Resolved lazily by _resolve_timeout() and then reused for the life of
        #: this instance. The configured timeout lives in a DB row, and a bulk
        #: sync can issue hundreds of requests through one service object - so
        #: re-reading it per request would mean hundreds of pointless queries.
        self._timeout = None

    def _resolve_timeout(self) -> int:
        """The configured NCM per-request timeout, read at most once per instance."""
        if self._timeout is None:
            try:
                from dashboard.models import APISettings
                self._timeout = APISettings.get_settings().ncm_api_timeout
            except Exception:
                self._timeout = 30
        return self._timeout

    def _rate_scope(self, url: str):
        """The rate-limit bucket this URL belongs to, or None if unlimited.

        Only the v1 order endpoints are throttled (see NCM_MAX_CALLS_PER_SECOND).
        When no separate v2 base URL is configured the two are the same string,
        and everything is treated as v1 - the safe way round.
        """
        v2 = self.base_url_v2
        if v2 and v2 != self.base_url and url.startswith(v2):
            return None
        return self.api_key

    def _make_request(self, method: str, url: str, data: Dict = None, params: Dict = None, timeout: int = None, _retry: bool = True,
                      _throttle_retries: int = NCM_THROTTLE_RETRIES):
        """Helper to make API requests.

        Transient failures (timeouts, connection resets, 5xx responses, or a
        non-JSON 200 body from a flaky upstream/proxy) are retried exactly
        once before giving up - but ONLY for GET requests. POST requests
        (create_order, return_order, create_exchange_order, comments, ...)
        are never auto-retried: if a POST times out or drops its response
        after NCM already processed it, retrying would risk creating a
        duplicate order/comment/return. Callers that need a POST retried
        must do so explicitly and idempotently at the call site.

        Rate limiting gets its own handling on top of that. NCM allows about
        three requests per second per account and answers 429 beyond it; that
        is a queueing problem, not a failure, so a throttled GET waits the
        interval NCM names and goes again (`_throttle_retries` attempts).
        A throttled POST is still never replayed automatically, for the same
        duplicate-write reason as above.
        """
        if timeout is None:
            timeout = self._resolve_timeout()

        method = method.upper()
        can_retry = _retry and method == 'GET'

        try:
            # Stay inside NCM's per-account window rather than spending a
            # request to be told we are outside it. Unthrottled endpoints are
            # not paced - they answer a hundred at a time quite happily.
            scope = self._rate_scope(url)
            if scope is not None:
                _await_rate_slot(scope)

            if method == 'GET':
                response = requests.get(url, headers=self.headers, params=params, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, headers=self.headers, json=data, timeout=timeout)

            if (response.status_code == 429 and method == 'GET'
                    and _throttle_retries > 0):
                wait = min(_throttle_wait_seconds(response), NCM_MAX_BACKOFF_SECONDS)
                logger.warning(
                    f"NCM API throttled, waiting {wait}s and retrying "
                    f"({_throttle_retries} attempt(s) left): {url}"
                )
                time.sleep(wait)
                return self._make_request(
                    method, url, data, params, timeout, _retry,
                    _throttle_retries=_throttle_retries - 1,
                )

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
                timeout = self._resolve_timeout()

                # This path deliberately does not go through _make_request:
                # a 404 here is the ordinary "no comments yet" answer and must
                # stay at debug level instead of logging an error per order.
                # It still owes NCM the same rate discipline, though - and a
                # swallowed 429 would drop an order's comments on the floor
                # and report success, so throttling is retried here too.
                attempts_left = NCM_THROTTLE_RETRIES
                comment_scope = self._rate_scope(url)
                while True:
                    if comment_scope is not None:
                        _await_rate_slot(comment_scope)
                    resp = _req.get(url, headers=self.headers, params=params, timeout=timeout)
                    if resp.status_code != 429 or attempts_left <= 0:
                        break
                    wait = min(_throttle_wait_seconds(resp), NCM_MAX_BACKOFF_SECONDS)
                    logger.warning(f"NCM comment fetch throttled, waiting {wait}s: {url}")
                    time.sleep(wait)
                    attempts_left -= 1

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

        A failed page is reported, never quietly skipped: this feeds an
        additive sync, so a dropped page does not delete anything, but the RTVs
        it held simply never reach the screen and the caller used to be told
        the fetch succeeded. `partial` is True when any request failed, with
        `error` naming the first one.

        Returns:
            {'success': True, 'data': [<order dict>, ...],
             'partial': bool, 'error': str|None}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        rtv_statuses = ['Arrived', 'Dispatched', 'Sent to Vendor', 'Returned to Warehouse']
        rtvs = []
        #: First error from any page, and whether the result is incomplete.
        fetch_errors = []

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
                    # Distinct from "no more pages": this status is now short
                    # by however many orders the failed page held.
                    fetch_errors.append(
                        f"{status} page {page}: {result.get('error') or 'request failed'}"
                    )
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
                    fetch_errors.append(
                        f"recent page {page}: {result.get('error') or 'request failed'}"
                    )
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
                task = futures[future]
                try:
                    rtvs.extend(future.result())
                except Exception as e:
                    # Swallowing this silently dropped a whole status' worth of
                    # RTVs and still reported success.
                    logger.warning(f"NCM RTV fetch failed for '{task}': {e}")
                    fetch_errors.append(f"{task}: {e}")

        # Deduplicate by orderid
        seen = set()
        unique_rtvs = []
        for o in rtvs:
            oid = o.get('orderid') or o.get('id')
            if oid and oid not in seen:
                seen.add(oid)
                unique_rtvs.append(o)

        if fetch_errors:
            logger.warning(
                f"NCM RTV fetch incomplete: {len(fetch_errors)} request(s) failed "
                f"- {fetch_errors[0]}"
            )

        return {
            'success': True,
            'data': unique_rtvs,
            'partial': bool(fetch_errors),
            'error': fetch_errors[0] if fetch_errors else None,
        }

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

    #: NCM status texts that mean the return leg is FINISHED - the parcel has
    #: physically arrived back with the vendor/warehouse. Everything else in the
    #: RTV pipeline ("Order Marked Return", "Sent to Vendor", an "Arrived at
    #: RETURN (BRANCH)" hop) is still in transit and must stay at the
    #: intermediate 'return_processing' stage.
    #:
    #: 'Delivered'/'Confirmed' only count as a completed return when they carry
    #: the vendor_return flag - NCM reuses the same words for a delivery to the
    #: customer, and resolve_delivered_status is the only place that knows the
    #: difference.
    RETURN_COMPLETED_STATUSES = frozenset((
        'delivered',
        'confirmed',
        'returned',
        'returned to warehouse',
        'return completed',
    ))

    #: System-side counterparts: the order statuses that mean the return is over.
    #: 'return' is what NCM's own confirmation resolves to; 'returned' is set by
    #: Return Management when staff physically scan the parcel back in.
    COMPLETED_RETURN_SYSTEM_STATUSES = frozenset(('return', 'returned'))

    @staticmethod
    def is_return_completed(ncm_status) -> bool:
        """True when an NCM status text means the parcel is back with the vendor.

        Matched on the leading words so branch-qualified variants NCM appends
        ("Returned to Warehouse (TINKUNE)") still count, while an in-pipeline
        hop that merely mentions the return branch ("Arrived at RETURN
        (TINKUNE)", "Dispatched to RETURN (TINKUNE)") does not - those start
        with a transit verb and are still on the way back.
        """
        text = ' '.join((ncm_status or '').strip().lower().split())
        if not text:
            return False
        if text in NCMService.RETURN_COMPLETED_STATUSES:
            return True
        return any(text.startswith(done + ' ') for done in NCMService.RETURN_COMPLETED_STATUSES)

    @staticmethod
    def map_ncm_status_to_system(ncm_status: str) -> str:
        """Map NCM status to system status.

        This mapping is aligned with NCMWebhookHandler.STATUS_MAPPING to ensure
        consistent behavior between webhook updates and manual sync operations.

        Every RTV status other than a confirmed arrival back at the warehouse
        resolves to 'return_processing'; only 'Returned to Warehouse' (and, via
        resolve_delivered_status, a vendor_return 'Delivered') ends the pipeline
        at 'return'. Each value produced here has a matching Setup row so the
        order's status_setup FK and its status strings never disagree - which is
        what made the order detail header badge show "RETURN" while the status
        dropdown still read "Return Processing".
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
            'Return Initiated': 'return_processing',
            'Return Approved': 'return_processing',
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
        vendor returns (RTV) - the vendor_return flag differentiates them. But
        that flag says only "this parcel is in the RTV pipeline", not "the RTV
        pipeline has finished": NCM sets it the moment an order is marked
        return and keeps reporting it on every hop of the journey back
        ("Order Marked Return", "Sent to Vendor", "Arrived at RETURN (...)").

        So the flag decides *which* pipeline the status belongs to, and the raw
        status text decides *how far along* it is:

        * vendor_return + a completed-return text ("Delivered" back to the
          vendor, "Returned to Warehouse") -> 'return', the parcel is back;
        * vendor_return + anything else -> 'return_processing', still moving;
        * no vendor_return -> the ordinary mapping, where "Delivered" is a real
          delivery to the customer.

        Treating the bare flag as terminal is what marked orders 'return' while
        they were still in transit back, and - because 'return' is in bulk
        sync's terminal set - froze them there: the order detail page's
        load-time sync would flip the header badge to RETURN seconds after the
        page rendered "Return Processing", and nothing synced the order again
        to correct it.

        Returns:
            (system_status, payment_status) tuple
        """
        # 'last_status' is the shape NCM uses when it answers with a single
        # summary object rather than a timeline list (see the branch in
        # ncm.realtime_api.api_sync_order_status that passes that object
        # straight through). Missing it left ncm_status empty, and an empty
        # status is never a completed return - so a finished RTV would have
        # been read as still in transit.
        ncm_status = (status_entry.get('status')
                      or status_entry.get('Status')
                      or status_entry.get('last_status')
                      or '')
        vendor_return_raw = status_entry.get('vendor_return', status_entry.get('vendorReturn', 'False'))
        vendor_return = NCMService.parse_vendor_return(vendor_return_raw)

        if vendor_return:
            # In the RTV pipeline - completed only once NCM says it arrived back.
            if NCMService.is_return_completed(ncm_status):
                return ('return', None)
            return ('return_processing', None)

        if ncm_status == 'Delivered':
            return ('delivered', 'paid')  # Successful delivery, mark as paid

        # For non-Delivered statuses, use standard mapping
        system_status = NCMService.map_ncm_status_to_system(ncm_status)
        return (system_status, None)

    # ==================== RTV DATE EXTRACTION ====================
    # NCM never exposes "when was this order marked RTV" as a field. The
    # authoritative answer is the added_time of the staff comment whose text
    # starts with "RTV marked"; everything else below is a ranked approximation.
    # These live here (rather than inline in the views) because the RTV list
    # sync, the comment-only sync and the repair command all need identical
    # semantics — three divergent copies is how the wrong dates crept in.

    RTV_MARKED_PREFIX = 'RTV marked'
    RTV_REMOVED_PREFIX = 'RTV removed'

    # Status texts that mean the package is already moving back to the vendor.
    RTV_TIMELINE_STATUSES = (
        'order marked return',
        'sent to vendor',
        'returned to warehouse',
    )

    @staticmethod
    def extract_rtv_marked_at(comments) -> dict:
        """Find when NCM marked an order as RTV, from its comment list.

        Picks the RTV comment with the LATEST added_time rather than the first
        one in the list. NCM's comment endpoint has no documented ordering and
        get_order_comments() doesn't sort, so an unmark -> re-mark sequence
        leaves several "RTV marked" comments whose list position says nothing
        about which is current.

        Args:
            comments: list of dicts as returned by NCMService.get_order_comments()
        Returns:
            {
              'marked_at':     aware datetime or None,
              'comment':       reason text ('' if none found),
              'vendor_return': True / False / None (None = comments say nothing),
              'source':        RTVOrder.SOURCE_* value for the winning entry,
            }
        """
        from dashboard.timezone_utils import parse_ncm_datetime

        result = {'marked_at': None, 'comment': '', 'vendor_return': None, 'source': ''}
        if not comments:
            return result

        # Parse every timestamp once, up front.
        parsed = []
        for c in comments:
            if not isinstance(c, dict):
                continue
            parsed.append((
                (c.get('comment') or '').strip(),
                parse_ncm_datetime(c.get('added_time')),
                (c.get('added_by') or '').strip(),
            ))

        rtv_entries = [
            (text, dt) for text, dt, _ in parsed
            if text.startswith(NCMService.RTV_MARKED_PREFIX)
            or text.startswith(NCMService.RTV_REMOVED_PREFIX)
        ]

        if rtv_entries:
            # Newest wins. Entries whose added_time didn't parse sort last, so
            # they're only chosen when nothing else parsed at all — better than
            # discarding the only evidence we have.
            #
            # The comment text is a deliberate secondary key: NCM's endpoint has
            # no documented ordering, so two RTV comments sharing an added_time
            # would otherwise be resolved by list position and flip between
            # polls, rewriting rtv.comment and making the page reload forever.
            from datetime import datetime, timezone as _dt_timezone
            _floor = datetime.min.replace(tzinfo=_dt_timezone.utc)
            text, dt = max(rtv_entries, key=lambda pair: (pair[1] or _floor, pair[0]))

            if text.startswith(NCMService.RTV_REMOVED_PREFIX):
                result['vendor_return'] = False
                return result

            result['vendor_return'] = True
            result['comment'] = text.replace('RTV marked - ', '').replace('RTV marked', '', 1).strip()
            if dt is not None:
                result['marked_at'] = dt
                result['source'] = 'comment'
            return result

        # No RTV comment at all — fall back to the newest NCM Staff comment,
        # which at least brackets when staff last touched the order.
        staff = [
            (text, dt) for text, dt, added_by in parsed
            if added_by == 'NCM Staff' and (text or dt)
        ]
        if staff:
            # Same tie-break reasoning as above — never let list position decide.
            dated = [(text, dt) for text, dt in staff if dt is not None]
            if dated:
                text, dt = max(dated, key=lambda pair: (pair[1], pair[0]))
            else:
                text, dt = min(staff, key=lambda pair: pair[0])[0], None
            result['comment'] = text
            if dt is not None:
                result['marked_at'] = dt
                result['source'] = 'ncm_staff_comment'

        return result

    @staticmethod
    def extract_return_step_time(status_entries):
        """Approximate the RTV-marked time from an order's NCM status timeline.

        UPPER BOUND ONLY: this returns when the package was first seen moving
        back to the vendor ("Order Marked Return", "Sent to Vendor",
        "Dispatched to RETURN ( TINKUNE)", ...), and marking always precedes
        dispatch. Never let this overwrite a comment-sourced date.

        Note the vendor_return flag on these entries is useless for dating —
        NCM stamps the order-level flag onto every historical row, including
        "Pickup Order Created" from before the return existed.

        Args:
            status_entries: list of dicts from NCMService.get_order_status()
        Returns:
            (aware datetime or None, 'status_timeline')
        """
        import re

        from dashboard.timezone_utils import parse_ncm_datetime

        if not status_entries:
            return (None, 'status_timeline')

        candidates = []
        for entry in status_entries:
            if not isinstance(entry, dict):
                continue
            status = (entry.get('status') or entry.get('Status') or '').strip()
            if not status:
                continue
            lowered = status.lower()
            is_return_step = (
                any(marker in lowered for marker in NCMService.RTV_TIMELINE_STATUSES)
                # "Dispatched to RETURN ( TINKUNE)" / "Arrived at RETURN (...)"
                or re.search(r'\bRETURN\b', status) is not None
            )
            if not is_return_step:
                continue
            dt = parse_ncm_datetime(entry.get('added_time') or entry.get('date'))
            if dt is not None:
                candidates.append(dt)

        # Earliest return-step entry is the closest to the actual marking.
        return (min(candidates) if candidates else None, 'status_timeline')

    @staticmethod
    def apply_rtv_marked_at(rtv, dt, source, save=True) -> bool:
        """Write rtv_marked_at only when `source` is at least as trustworthy.

        Everything that sets an RTV date goes through here. Without the rank
        check, a cheap approximation (an order's created_date, or a status
        timeline entry) could overwrite the real "RTV marked" comment time on
        the next sync — which is the bug this whole change exists to kill.

        Always stamps rtv_marked_at_checked_at, even when nothing else changes,
        so repair passes that take the least-recently-checked rows make
        progress instead of re-picking the same handful forever.

        Args:
            rtv: RTVOrder instance
            dt: datetime / NCM timestamp string / None
            source: one of RTVOrder.SOURCE_* values
            save: persist immediately (False = caller saves the listed fields)
        Returns:
            True if rtv_marked_at was updated.
        """
        from django.utils import timezone as dj_timezone

        from dashboard.models import RTVOrder
        from dashboard.timezone_utils import parse_ncm_datetime

        fields = ['rtv_marked_at_checked_at']
        rtv.rtv_marked_at_checked_at = dj_timezone.now()

        parsed = parse_ncm_datetime(dt)
        incoming_rank = RTVOrder.SOURCE_RANK.get(source, 0)
        current_rank = RTVOrder.SOURCE_RANK.get(rtv.rtv_marked_at_source, 0)

        # Equal rank overwrites on purpose: a fresh "RTV marked" comment after
        # an unmark/re-mark is newer information than the old one. But an
        # equal-rank re-derivation that lands on the *same* instant (NCM's
        # comment endpoint has no stable ordering, so repeat polls can just
        # re-pick the same comment) must not count as a write — otherwise the
        # repair queue in ncm_rtvs_sync flags it as a change every poll and
        # the frontend reloads the page for nothing. Compared at second
        # resolution to tolerate any legacy rows without DATETIME(6).
        should_write = (
            parsed is not None
            and incoming_rank >= current_rank
            and (incoming_rank > current_rank or not _same_instant(parsed, rtv.rtv_marked_at))
        )

        if should_write:
            rtv.rtv_marked_at = parsed
            rtv.rtv_marked_at_source = source
            fields.extend(['rtv_marked_at', 'rtv_marked_at_source'])

        if save:
            rtv.save(update_fields=fields)

        return should_write

    @staticmethod
    def sync_order_status_fields(order, system_status, payment_status=None,
                                 allow_return_reopen=False):
        """Update all status-related fields on an order to keep them in sync.

        Updates: status, order_status, status_setup (FK),
                 payment_status, payment_status_setup (FK).

        `allow_return_reopen` lifts the "a finished return never reopens" guard
        below. Only the repair command uses it, to move orders that were marked
        'return' while still in transit back onto the correct stage.

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

        # A finished return never goes back to "still on its way". NCM keeps
        # reporting vendor_return (and can replay in-pipeline hops late or out
        # of order) after the parcel is already back, and staff scanning it in
        # on the Return Management page sets 'returned' - a stronger signal
        # than anything NCM reports. Either way, resolving 'return_processing'
        # afterwards would be a downgrade, so keep what the order already has.
        if (not allow_return_reopen
                and system_status == 'return_processing'
                and (order.status or '').strip().lower() in NCMService.COMPLETED_RETURN_SYSTEM_STATUSES):
            system_status = order.status

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
        """Find (or create) the Setup row whose name matches a system status value.

        Setup names are human-readable ("Return Processing") while system
        status values are normalized ("return_processing"), so matching
        compares both sides normalized. The indexed name__iexact lookup
        resolves the usual case in a single query; the full scan below is
        only reached for names that normalize equal without matching
        literally (e.g. one that already contains underscores), which keeps
        this correct while avoiding a table scan per order during bulk sync.

        When nothing matches, the row is created rather than returning None.
        Returning None left the order's status_setup FK pointing at the
        PREVIOUS status while the status strings moved on, and the order detail
        header badge renders that FK - so the badge showed the stale status
        (e.g. "RETURN") while the status dropdown, which reads the same FK, and
        every list page, which reads the strings, disagreed with it. Creating
        the row is also what dashboard.views.sync_order_status_setup already
        does for statuses set through the UI, so both paths now behave alike.
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

        # An admin may have deactivated the row rather than deleted it. Reuse it:
        # unique_together(setup_type, name) would reject a duplicate anyway, and a
        # hidden status is still a truthful place for the FK to point.
        existing = Setup.objects.filter(
            setup_type=setup_type, name__iexact=normalized.replace('_', ' ')
        ).first()
        if existing:
            return existing

        # Savepointed: this runs inside the webhook's transaction.atomic(), so a
        # race with a concurrent sync creating the same row must not leave that
        # transaction unusable when the caller swallows the error.
        from django.db import transaction
        with transaction.atomic():
            setup, _ = Setup.objects.get_or_create(
                setup_type=setup_type,
                name=normalized.replace('_', ' ').title(),
                defaults={'is_active': True},
            )
        return setup


# ---------------------------------------------------------------------------
# Order status history
#
# NCM scopes every order to the account that created it: query order 25098287
# with the wrong API key and NCM answers 404 {"detail": "Not found."} rather
# than an empty list. The order detail page's "Status History" panel therefore
# came up saying "No status history found" for any order whose stored
# api_config_id was missing or pointed at the wrong account — even though NCM's
# own portal listed the full timeline for it.
#
# The helpers below fetch the timeline against the account the order most
# likely belongs to and, only if that comes back empty, sweep the remaining
# active NCM accounts before concluding there is genuinely no history.
# ---------------------------------------------------------------------------

def normalize_status_entries(raw):
    """Flatten an /order/status payload into display-ready timeline rows.

    NCM answers with a bare list, but callers have historically also seen the
    rows wrapped in {"data": [...]} / {"results": [...]} — and a single-entry
    response wrapped as a lone dict. All four shapes are accepted.

    Rows come back newest-first (NCM's own ordering, re-applied here rather
    than trusted, since the UI paints row 0 as the current status).
    """
    from dashboard.timezone_utils import format_ncm_datetime, parse_ncm_datetime

    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = raw.get('data', raw.get('results', []))
        if isinstance(rows, dict):
            rows = [rows]
    else:
        rows = []

    entries = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = (row.get('status') or row.get('Status') or '').strip()
        raw_timestamp = (
            row.get('added_time')
            or row.get('date')
            or row.get('timestamp')
            or row.get('created_at')
            or ''
        )
        if not status and not raw_timestamp:
            continue
        entries.append({
            'status': status,
            # Raw kept for back-compat; *_display is the formatted Nepal-time
            # string the UI shows instead of printing NCM's ISO value verbatim.
            'timestamp': raw_timestamp,
            'timestamp_display': format_ncm_datetime(raw_timestamp),
            'remarks': row.get('remarks', row.get('comment', '')) or '',
            '_sort_key': parse_ncm_datetime(raw_timestamp),
        })

    # Undated rows keep their original relative position at the bottom rather
    # than crashing the comparison against aware datetimes.
    dated = [e for e in entries if e['_sort_key'] is not None]
    undated = [e for e in entries if e['_sort_key'] is None]
    dated.sort(key=lambda e: e['_sort_key'], reverse=True)

    ordered = dated + undated
    for entry in ordered:
        entry.pop('_sort_key', None)
    return ordered


def candidate_ncm_config_ids(preferred_config_id=None):
    """Ordered NCM accounts to try for one order, de-duplicated by API key.

    `None` means "the key in settings". Several LogisticsAPIConfig rows can
    share one key (the same account registered twice); those collapse to a
    single attempt so a sweep costs one request per distinct account, not one
    per row.
    """
    from django.conf import settings as dj_settings
    from dashboard.models import LogisticsAPIConfig

    default_key = getattr(dj_settings, 'NCM_API_KEY', '') or ''
    configs = {
        c.id: c
        for c in LogisticsAPIConfig.objects.filter(
            logistics_provider='ncm', is_active=True
        )
    }

    ordered = []
    seen_keys = set()

    def _add(config_id, api_key):
        key = (api_key or '').strip()
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        ordered.append(config_id)

    if preferred_config_id and preferred_config_id in configs:
        _add(preferred_config_id, configs[preferred_config_id].api_key)
    _add(None, default_key)
    for config_id, config in configs.items():
        _add(config_id, config.api_key)

    return ordered


#: Opening one order fetches /order/status twice within a second or so - once
#: by the page-load sync, once by the detail endpoint that draws the timeline.
#: Against a three-per-second budget that duplicate is expensive, so a resolved
#: answer is briefly reusable. Short enough that a status change still shows up
#: on the next refresh; only populated answers are ever stored.
NCM_STATUS_CACHE_SECONDS = 20


def _status_cache_key(ncm_order_id):
    return f'ncm_order_status_raw_{ncm_order_id}'


def invalidate_order_status_cache(ncm_order_id):
    """Drop any cached /order/status answer for this order.

    Called after we write a status change, so the next read is not served a
    snapshot from just before it.
    """
    try:
        from django.core.cache import cache
        cache.delete(_status_cache_key(ncm_order_id))
    except Exception:
        pass


def peek_order_status_cache(ncm_order_id):
    """A populated /order/status answer cached moments ago, or None.

    Lets a caller skip an NCM request it is about to duplicate - notably the
    order detail endpoint, which runs immediately after the page-load sync has
    already asked NCM this exact question.

    Returns (result, resolved_config_id) or None.
    """
    try:
        from django.core.cache import cache
        cached = cache.get(_status_cache_key(ncm_order_id))
    except Exception:
        return None
    if not cached:
        return None
    return cached[0], cached[1]


def fetch_order_status_raw(ncm_order_id, api_config_id=None,
                           sweep_accounts=True, skip_config_ids=(),
                           use_cache=False):
    """Call /order/status against whichever NCM account actually owns the order.

    Returns (result, resolved_config_id) where `result` is the raw
    NCMService._make_request envelope, so callers that need NCM's own row shape
    (added_time, vendor_return, ...) get it untouched.

    `resolved_config_id` is the account that answered with entries, or None
    when nobody did - callers should persist it so the next call is a single
    request instead of a sweep.

    Args:
        ncm_order_id: NCM's order id.
        api_config_id: LogisticsAPIConfig id believed to own the order, or
            None for the default account.
        sweep_accounts: when the preferred account returns nothing, also try
            every other active NCM account before giving up.
        skip_config_ids: accounts the caller has already queried itself, so a
            fallback sweep doesn't repeat a request that just came back empty.
        use_cache: reuse a populated answer fetched for this order in the last
            NCM_STATUS_CACHE_SECONDS. For the automatic page-load reads, which
            otherwise ask NCM the same question twice. A deliberate "Sync
            Status" click must leave this off and go to NCM.
    """
    if use_cache:
        try:
            from django.core.cache import cache
            cached = cache.get(_status_cache_key(ncm_order_id))
        except Exception:
            cached = None
        if cached is not None:
            return cached[0], cached[1]

    candidates = candidate_ncm_config_ids(api_config_id)
    if not sweep_accounts:
        candidates = candidates[:1]
    if skip_config_ids:
        skipped = set(skip_config_ids)
        candidates = [c for c in candidates if c not in skipped]

    first_success = None
    last_error = None
    throttle_error = None

    for config_id in candidates:
        result = NCMService(api_config_id=config_id).get_order_status(ncm_order_id)
        if not result.get('success'):
            error = result.get('error') or 'NCM request failed'
            # A 429 is not an answer about ownership. NCMService already waited
            # and retried, so reaching here means the account is genuinely
            # congested - remember that separately, because reporting some
            # other account's "Not found" (or its empty answer) instead would
            # tell the user this order has no history when nobody ever asked
            # the account that has it.
            if _is_throttle_error(error):
                throttle_error = error
            else:
                last_error = error
            continue

        if normalize_status_entries(result.get('data')):
            if config_id != api_config_id:
                logger.info(
                    f"NCM order {ncm_order_id}: status history resolved via "
                    f"api_config {config_id} (asked for {api_config_id})"
                )
            try:
                from django.core.cache import cache
                cache.set(
                    _status_cache_key(ncm_order_id),
                    (result, config_id),
                    NCM_STATUS_CACHE_SECONDS,
                )
            except Exception:
                pass
            return result, config_id

        # A successful-but-empty answer is authoritative enough to report, but
        # not authoritative enough to stop looking: an account that does not
        # own the order can only ever answer empty.
        if first_success is None:
            first_success = result

    # An empty answer only means "this order has no history" if every account
    # actually got to answer. If one was throttled, say so instead.
    if throttle_error is not None:
        if isinstance(throttle_error, dict):
            throttle_error = throttle_error.get('detail') or str(throttle_error)
        return ({'success': False, 'error': str(throttle_error), 'throttled': True}, None)

    if first_success is not None:
        return first_success, None

    if isinstance(last_error, dict):
        last_error = last_error.get('detail') or str(last_error)
    return (
        {'success': False, 'error': str(last_error or 'Could not reach NCM')},
        None,
    )


def fetch_order_status_history(ncm_order_id, api_config_id=None,
                               sweep_accounts=True, skip_config_ids=(),
                               use_cache=False):
    """Fetch one NCM order's status timeline, resolving the owning account.

    Returns:
        (entries, resolved_config_id, error)

        `error` is None whenever at least one account answered successfully -
        including when the answer was legitimately empty - so callers can tell
        "NCM has no history for this order" apart from "we could not ask NCM".

    See fetch_order_status_raw() for the arguments.
    """
    result, resolved_config_id = fetch_order_status_raw(
        ncm_order_id,
        api_config_id=api_config_id,
        sweep_accounts=sweep_accounts,
        skip_config_ids=skip_config_ids,
        use_cache=use_cache,
    )
    if not result.get('success'):
        return [], None, str(result.get('error') or 'Could not reach NCM')
    return normalize_status_entries(result.get('data')), resolved_config_id, None
