import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


class PickAndDropService:
    """Service class for Pick and Drop logistics API integration"""

    def __init__(self):
        self.api_key = getattr(settings, 'PND_API_KEY', '')
        self.api_secret = getattr(settings, 'PND_API_SECRET', '')
        self.base_url = getattr(settings, 'PND_API_BASE_URL', 'https://pickndropnepal.com').rstrip('/')

    def _get_headers(self):
        return {
            'Authorization': f'token {self.api_key}:{self.api_secret}',
            'Content-Type': 'application/json',
        }

    def create_order(self, order_data):
        """
        Create an order in Pick and Drop system.

        order_data should contain:
            - customerName (required)
            - primaryMobileNo (required)
            - destinationBranch (required)
            - codAmount (required)
            - orderDescription (required)
            - vendorTrackingNumber (optional)
            - landmark (optional)
            - secondaryMobileNo (optional)
            - destinationCityArea (optional)
            - weight (optional)
            - orderType (optional, default "Regular")
            - instruction (optional)
            - businessAddress (optional)
            - ref (optional)
        """
        url = f"{self.base_url}/api/method/logi360.api.create_order"

        try:
            response = requests.post(
                url,
                json=order_data,
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                # Success: {"message": {"status": "success", "data": {...}}}
                msg = data.get('message', {})
                if isinstance(msg, dict) and msg.get('status') == 'success':
                    return {
                        'success': True,
                        'data': msg.get('data', {}),
                        'raw_response': data,
                    }
                else:
                    return {
                        'success': False,
                        'error': msg if isinstance(msg, str) else str(msg),
                        'raw_response': data,
                    }
            else:
                try:
                    error_data = response.json()
                except Exception:
                    error_data = {'raw_text': response.text[:500]}

                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}',
                    'raw_response': error_data,
                }

        except requests.exceptions.Timeout:
            logger.error('Pick and Drop API timeout')
            return {'success': False, 'error': 'API request timed out'}
        except requests.exceptions.ConnectionError:
            logger.error('Pick and Drop API connection error')
            return {'success': False, 'error': 'Could not connect to Pick and Drop API'}
        except Exception as e:
            logger.error(f'Pick and Drop API error: {str(e)}')
            return {'success': False, 'error': str(e)}

    def cancel_order(self, order_id):
        """
        Cancel an order in Pick and Drop system.

        Args:
            order_id: The PND order ID to cancel (e.g. "XGAD-8")

        Returns:
            dict with 'success', 'data'/'error', and 'raw_response'
        """
        url = f"{self.base_url}/api/method/logi360.api.cancel_order"

        try:
            response = requests.put(
                url,
                json={'orderID': str(order_id)},
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                msg = data.get('message', {})
                if isinstance(msg, dict) and msg.get('status') == 'success':
                    return {
                        'success': True,
                        'data': msg.get('data', ''),
                        'message': msg.get('message', 'Order canceled successfully.'),
                        'raw_response': data,
                    }
                else:
                    error_msg = msg.get('message', str(msg)) if isinstance(msg, dict) else str(msg)
                    return {
                        'success': False,
                        'error': error_msg,
                        'raw_response': data,
                    }
            else:
                try:
                    error_data = response.json()
                    msg = error_data.get('message', {})
                    error_msg = msg.get('message', str(msg)) if isinstance(msg, dict) else str(error_data)
                except Exception:
                    error_msg = f'HTTP {response.status_code}'
                    error_data = {'raw_text': response.text[:500]}

                return {
                    'success': False,
                    'error': error_msg,
                    'raw_response': error_data,
                }

        except requests.exceptions.Timeout:
            logger.error('Pick and Drop cancel API timeout')
            return {'success': False, 'error': 'API request timed out'}
        except requests.exceptions.ConnectionError:
            logger.error('Pick and Drop cancel API connection error')
            return {'success': False, 'error': 'Could not connect to Pick and Drop API'}
        except Exception as e:
            logger.error(f'Pick and Drop cancel API error: {str(e)}')
            return {'success': False, 'error': str(e)}
