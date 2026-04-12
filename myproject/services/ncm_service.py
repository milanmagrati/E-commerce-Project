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
    
    def _make_request(self, method: str, url: str, data: Dict = None, params: Dict = None):
        """Helper to make API requests"""
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
            elif method.upper() == 'POST':
                response = requests.post(url, headers=self.headers, json=data, timeout=30)
            
            response.raise_for_status()
            return {'success': True, 'data': response.json(), 'status_code': response.status_code}
        
        except requests.exceptions.Timeout:
            logger.error(f"NCM API timeout: {url}")
            return {'success': False, 'error': 'Request timeout'}
        
        except requests.exceptions.RequestException as e:
            logger.error(f"NCM API error: {str(e)}")
            error_msg = str(e)
            if hasattr(e, 'response') and hasattr(e.response, 'json'):
                try:
                    error_msg = e.response.json()
                except:
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
    
    def get_order_comments(self, ncm_order_id: int):
        """Get comments for an NCM order from order details"""
        result = self.get_order_details(ncm_order_id)
        if result['success']:
            data = result['data']
            # NCM API may return comments in various formats
            comments = []
            if isinstance(data, dict):
                comments = data.get('comments', data.get('comment', []))
            if isinstance(comments, str):
                comments = [{'comment': comments}] if comments else []
            return {'success': True, 'data': comments}
        return result

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

    def return_order(self, ncm_order_id: int, comment: str = None):
        """Mark order for return"""
        url = f"{self.base_url_v2}/vendor/order/return"
        data = {'pk': ncm_order_id}
        if comment:
            data['comment'] = comment
        return self._make_request('POST', url, data=data)
    
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
