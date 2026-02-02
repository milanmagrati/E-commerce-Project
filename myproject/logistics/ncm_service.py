import requests
from django.utils import timezone
from .models import LogisticsProvider, LogisticsOrder, StatusLog


class NCMService:
    """Handles all NCM API operations"""
    
    def __init__(self, provider_code='NCM'):
        try:
            self.provider = LogisticsProvider.objects.get(code=provider_code, is_active=True)
            self.base_url = self.provider.api_url
            self.token = self.provider.api_token
        except LogisticsProvider.DoesNotExist:
            raise Exception(f"Provider {provider_code} not found in database. Add it via Django admin first.")
    
    def _get_headers(self):
        return {
            'Authorization': f'Token {self.token}',
            'Content-Type': 'application/json',
        }
    
    def _make_request(self, method, endpoint, **kwargs):
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._get_headers(),
                timeout=30,
                **kwargs
            )
            return response
        except requests.exceptions.RequestException as e:
            raise Exception(f"NCM API Error: {str(e)}")
    
    def create_order(self, order_data):
        """
        Create order in NCM system
        
        Args:
            order_data: dict with keys:
                - order_reference: Your internal order ID/reference
                - customer_name: Customer name
                - customer_phone: Customer phone
                - customer_address: Delivery address
                - cod_amount: Cash on delivery amount
                - fbranch: From branch (pickup)
                - branch: To branch (destination)
                - package: Package description
        """
        
        # Build NCM payload
        ncm_data = {
            'name': order_data.get('customer_name'),
            'phone': order_data.get('customer_phone'),
            'address': order_data.get('customer_address'),
            'fbranch': order_data.get('fbranch', 'TINKUNE'),
            'branch': order_data.get('branch', 'KATHMANDU'),
            'cod_charge': str(order_data.get('cod_amount', 0)),
            'package': order_data.get('package', 'Order'),
            'vref_id': f"ORD{order_data.get('order_reference')}",
            'delivery_type': order_data.get('delivery_type', 'Door2Door'),
        }
        
        response = self._make_request('POST', '/api/v1/order/create', json=ncm_data)
        
        if response.status_code == 200:
            data = response.json()
            ncm_order_id = data.get('orderid')
            
            logistics_order = LogisticsOrder.objects.create(
                order_reference=order_data.get('order_reference'),
                provider=self.provider,
                ncm_order_id=str(ncm_order_id),
                status='CREATED',
                customer_name=order_data.get('customer_name'),
                customer_phone=order_data.get('customer_phone'),
                customer_address=order_data.get('customer_address'),
                cod_amount=order_data.get('cod_amount', 0)
            )
            
            StatusLog.objects.create(
                logistics_order=logistics_order,
                status='CREATED',
                message=f'Order created in NCM with ID {ncm_order_id}'
            )
            
            return logistics_order
        else:
            error_data = response.json() if response.text else {}
            raise Exception(f"NCM API Error: {error_data}")
    
    def sync_status(self, logistics_order):
        """Get latest status from NCM"""
        ncm_order_id = logistics_order.ncm_order_id
        
        response = self._make_request('GET', f'/api/v1/order/status?id={ncm_order_id}')
        
        if response.status_code == 200:
            statuses = response.json()
            if statuses and len(statuses) > 0:
                latest = statuses[0]
                ncm_status = latest.get('status')
                
                new_status = self._map_status(ncm_status)
                
                logistics_order.status = new_status
                logistics_order.last_synced = timezone.now()
                logistics_order.save()
                
                StatusLog.objects.create(
                    logistics_order=logistics_order,
                    status=ncm_status,
                    message='Status synced from NCM'
                )
                
                return new_status
        
        return None
    
    def get_order_details(self, logistics_order):
        """Get full order details from NCM"""
        ncm_order_id = logistics_order.ncm_order_id
        response = self._make_request('GET', f'/api/v1/order?id={ncm_order_id}')
        
        if response.status_code == 200:
            return response.json()
        return None
    
    def add_comment(self, logistics_order, comment_text):
        """Add comment to NCM order"""
        payload = {
            'orderid': logistics_order.ncm_order_id,
            'comments': comment_text
        }
        response = self._make_request('POST', '/api/v1/comment', json=payload)
        
        if response.status_code == 200:
            StatusLog.objects.create(
                logistics_order=logistics_order,
                status='COMMENT_ADDED',
                message=comment_text
            )
            return True
        return False
    
    def get_branches(self):
        """Get list of NCM branches"""
        response = self._make_request('GET', '/api/v2/branches')
        
        if response.status_code == 200:
            return response.json()
        return []
    
    def calculate_shipping(self, from_branch, to_branch, delivery_type='Door2Door'):
        """Calculate shipping rate"""
        params = {
            'creation': from_branch,
            'destination': to_branch,
            'type': delivery_type
        }
        response = self._make_request('GET', '/api/v1/shipping-rate', params=params)
        
        if response.status_code == 200:
            return response.json()
        return None
    
    def _map_status(self, ncm_status):
        """Map NCM status to our status"""
        mapping = {
            'Pickup Order Created': 'CREATED',
            'Sent for Pickup': 'CREATED',
            'Pickup Complete': 'IN_TRANSIT',
            'Sent for Delivery': 'IN_TRANSIT',
            'Delivered': 'DELIVERED',
            'Returned': 'RETURNED',
            'Arrived': 'IN_TRANSIT',
            'Drop off Order Created': 'CREATED',
        }
        return mapping.get(ncm_status, 'PENDING')


# Simple helper functions
def create_ncm_order(order_data):
    """
    Create order in NCM
    
    Usage:
        create_ncm_order({
            'order_reference': '12345',
            'customer_name': 'John Doe',
            'customer_phone': '9841234567',
            'customer_address': 'Kathmandu',
            'cod_amount': 2500,
            'fbranch': 'TINKUNE',
            'branch': 'KATHMANDU',
            'package': 'Mobile Phone'
        })
    """
    service = NCMService()
    return service.create_order(order_data)


def sync_ncm_status(order_reference):
    """Sync order status from NCM by order reference"""
    try:
        logistics_order = LogisticsOrder.objects.get(order_reference=order_reference)
        service = NCMService()
        return service.sync_status(logistics_order)
    except LogisticsOrder.DoesNotExist:
        return None


def update_all_pending_orders():
    """Sync all pending orders"""
    service = NCMService()
    pending_orders = LogisticsOrder.objects.filter(
        status__in=['CREATED', 'IN_TRANSIT']
    )
    
    for logistics_order in pending_orders:
        try:
            service.sync_status(logistics_order)
            print(f"✓ Synced Order {logistics_order.order_reference}")
        except Exception as e:
            print(f"✗ Failed Order {logistics_order.order_reference}: {str(e)}")
