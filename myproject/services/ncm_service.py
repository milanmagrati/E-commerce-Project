# services/ncm_service.py
import requests
import logging
from django.conf import settings
from typing import Dict, List, Optional

logger = logging.getLogger('ncm')

class NCMService:
    """Service for NCM (Nepal Can Move) API Integration"""
    
    def __init__(self):
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
        """Map NCM status to your system status"""
        mapping = {
            'Pickup Order Created': 'processing',
            'Drop off Order Created': 'processing',
            'Pickup Complete': 'processing',
            'Drop off Order Collected': 'processing',
            'Dispatched': 'shipped',
            'In Transit': 'shipped',
            'Arrived': 'shipped',
            'Sent for Delivery': 'shipped',
            'Out for Delivery': 'shipped',
            'Delivered': 'delivered',
            'Confirmed': 'delivered',
        }
        return mapping.get(ncm_status, 'processing')
