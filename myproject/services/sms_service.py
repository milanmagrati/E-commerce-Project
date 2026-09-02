# services/sms_service.py
"""
SMS Notification Service for Order Updates
Handles sending SMS to customers for delivery status changes
"""

import logging
from django.conf import settings
from typing import Dict, Optional, Tuple

logger = logging.getLogger('ncm')

class SMSService:
    """Service for sending SMS notifications"""
    
    def __init__(self):
        # Configure your SMS provider (Twilio, Sparrow, Atuha, etc.)
        self.provider = getattr(settings, 'SMS_PROVIDER', 'console')
        self.api_key = getattr(settings, 'SMS_API_KEY', None)
        self.sender_id = getattr(settings, 'SMS_SENDER_ID', 'EcommerceAdmin')
        self.enabled = getattr(settings, 'SMS_ENABLED', False)
    
    def send_order_status_sms(self, phone_number: str, order_number: str, 
                             status: str, additional_info: str = None) -> Dict:
        """
        Send SMS notification about order status change
        
        Args:
            phone_number: Customer phone number
            order_number: Order number
            status: Delivery status (delivered, in_transit, returned, cod_collected)
            additional_info: Extra information like reference number
        
        Returns:
            Dict with success status and message
        """
        if not self.enabled:
            logger.info(f"SMS disabled. Skipping notification to {phone_number}")
            return {'success': True, 'message': 'SMS disabled', 'sent': False}
        
        # Clean phone number
        phone_number = self._clean_phone(phone_number)
        if not phone_number:
            logger.error("Invalid phone number after cleaning")
            return {'success': False, 'message': 'Invalid phone number', 'sent': False}
        
        # Prepare message
        message = self._prepare_message(order_number, status, additional_info)
        
        if not message:
            logger.error(f"Failed to prepare message for status: {status}")
            return {'success': False, 'message': 'Invalid status type', 'sent': False}
        
        # Send SMS based on provider
        if self.provider == 'twilio':
            return self._send_twilio_sms(phone_number, message, order_number)
        elif self.provider == 'sparrow':
            return self._send_sparrow_sms(phone_number, message, order_number)
        elif self.provider == 'atuha':
            return self._send_atuha_sms(phone_number, message, order_number)
        elif self.provider == 'console':
            return self._send_console_sms(phone_number, message, order_number)
        else:
            logger.warning(f"Unknown SMS provider: {self.provider}")
            return {'success': False, 'message': 'Unknown SMS provider', 'sent': False}
    
    def send_text(self, phone_number: str, message: str, ref: str = 'notify') -> Dict:
        """Send an arbitrary short message through the configured provider.

        For non-order notifications (e.g. back-in-stock alerts) that don't fit
        the fixed order-status templates.
        """
        if not self.enabled:
            logger.info(f"SMS disabled. Skipping message to {phone_number}")
            return {'success': True, 'message': 'SMS disabled', 'sent': False}

        phone_number = self._clean_phone(phone_number)
        if not phone_number:
            return {'success': False, 'message': 'Invalid phone number', 'sent': False}

        if not (message or '').strip():
            return {'success': False, 'message': 'Empty message', 'sent': False}

        if self.provider == 'twilio':
            return self._send_twilio_sms(phone_number, message, ref)
        elif self.provider == 'sparrow':
            return self._send_sparrow_sms(phone_number, message, ref)
        elif self.provider == 'atuha':
            return self._send_atuha_sms(phone_number, message, ref)
        elif self.provider == 'console':
            return self._send_console_sms(phone_number, message, ref)
        logger.warning(f"Unknown SMS provider: {self.provider}")
        return {'success': False, 'message': 'Unknown SMS provider', 'sent': False}

    def _prepare_message(self, order_number: str, status: str, additional_info: str = None) -> Optional[str]:
        """Prepare SMS message for status"""
        status_lower = status.lower()
        
        if status_lower == 'delivered':
            message = f"Your order {order_number} has been delivered. Thank you for your purchase! 🎉"
        elif status_lower in ['in_transit', 'out_for_delivery', 'shipped']:
            message = f"Your order {order_number} is out for delivery. 📦 Track it in the system for real-time updates."
        elif status_lower in ['returned', 'return', 'return_processing', 'return_arrived',
                              'return_initiated']:
            message = f"Your order {order_number} has been marked for return. Please contact support for details."
        elif status_lower in ['cod_collected', 'payment_collected']:
            amount = additional_info or ""
            message = f"Payment of {amount} collected for order {order_number}. Receipt sent to your email."
        else:
            return None
        
        return message
    
    def _send_twilio_sms(self, phone_number: str, message: str, order_number: str) -> Dict:
        """Send SMS using Twilio"""
        try:
            from twilio.rest import Client
            
            account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', None)
            auth_token = getattr(settings, 'TWILIO_AUTH_TOKEN', None)
            from_number = getattr(settings, 'TWILIO_PHONE_NUMBER', None)
            
            if not all([account_sid, auth_token, from_number]):
                logger.error("Twilio credentials not configured")
                return {'success': False, 'message': 'Twilio not configured', 'sent': False}
            
            client = Client(account_sid, auth_token)
            call = client.messages.create(
                body=message,
                from_=from_number,
                to=phone_number
            )
            
            logger.info(f"[SUCCESS] SMS sent via Twilio: {call.sid} to {phone_number} for order {order_number}")
            return {
                'success': True,
                'message': 'SMS sent successfully',
                'sent': True,
                'provider': 'twilio',
                'message_id': call.sid
            }
        except Exception as e:
            logger.error(f"Twilio SMS error: {str(e)}")
            return {'success': False, 'message': str(e), 'sent': False}
    
    def _send_sparrow_sms(self, phone_number: str, message: str, order_number: str) -> Dict:
        """Send SMS using Sparrow SMS (Popular in Nepal)"""
        try:
            import requests
            
            sparrow_url = "https://api.sparrowsms.com/v2/sms/"
            
            params = {
                'token': self.api_key,
                'from': self.sender_id,
                'to': phone_number,
                'text': message
            }
            
            response = requests.post(sparrow_url, data=params, timeout=10)
            result = response.json()
            
            if result.get('status_code') == 200:
                logger.info(f"[SUCCESS] SMS sent via Sparrow: {order_number} to {phone_number}")
                return {
                    'success': True,
                    'message': 'SMS sent successfully',
                    'sent': True,
                    'provider': 'sparrow',
                    'message_id': result.get('response', {}).get('sms_id')
                }
            else:
                error = result.get('response', {}).get('message', 'Unknown error')
                logger.error(f"Sparrow SMS error: {error}")
                return {'success': False, 'message': error, 'sent': False}
                
        except Exception as e:
            logger.error(f"Sparrow SMS error: {str(e)}")
            return {'success': False, 'message': str(e), 'sent': False}
    
    def _send_atuha_sms(self, phone_number: str, message: str, order_number: str) -> Dict:
        """Send SMS using Atuha SMS (Popular in Nepal)"""
        try:
            import requests
            
            atuha_url = "https://api.atuhao.com/send/"
            
            params = {
                'key': self.api_key,
                'mobile': phone_number,
                'message': message
            }
            
            response = requests.get(atuha_url, params=params, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"[SUCCESS] SMS sent via Atuha: {order_number} to {phone_number}")
                return {
                    'success': True,
                    'message': 'SMS sent successfully',
                    'sent': True,
                    'provider': 'atuha',
                    'response': response.text
                }
            else:
                logger.error(f"Atuha SMS error: {response.status_code} - {response.text}")
                return {'success': False, 'message': response.text, 'sent': False}
                
        except Exception as e:
            logger.error(f"Atuha SMS error: {str(e)}")
            return {'success': False, 'message': str(e), 'sent': False}
    
    def _send_console_sms(self, phone_number: str, message: str, order_number: str) -> Dict:
        """Log SMS to console (for development/testing)"""
        logger.info(f"""
        ═══════════════════════════════════════════════════════
        📱 SMS NOTIFICATION (Console Mode)
        ═══════════════════════════════════════════════════════
        To: {phone_number}
        Order: {order_number}
        Message: {message}
        ═══════════════════════════════════════════════════════
        """)
        return {
            'success': True,
            'message': 'SMS logged to console (development mode)',
            'sent': True,
            'provider': 'console'
        }
    
    @staticmethod
    def _clean_phone(phone: str) -> str:
        """Clean and format phone number"""
        if not phone:
            return ""
        
        # Remove non-digit characters
        cleaned = ''.join(filter(str.isdigit, str(phone)))
        
        # Handle Nepal phone numbers (should start with 98 or 97 for mobile)
        if not cleaned:
            return ""
        
        # Ensure it has proper length
        if len(cleaned) >= 10:
            return cleaned[-10:]  # Take last 10 digits
        
        return ""
    
    @staticmethod
    def send_bulk_sms(orders: list) -> Dict:
        """Send SMS to multiple customers"""
        sms_service = SMSService()
        results = {'sent': 0, 'failed': 0, 'details': []}
        
        for order in orders:
            try:
                result = sms_service.send_order_status_sms(
                    phone_number=order.customer_phone,
                    order_number=order.order_number,
                    status=order.ncm_status or order.status,
                    additional_info=str(order.cod_collected) if order.cod_collected else None
                )
                
                if result.get('sent'):
                    results['sent'] += 1
                else:
                    results['failed'] += 1
                
                results['details'].append({
                    'order': order.order_number,
                    'result': result
                })
            except Exception as e:
                logger.error(f"Error sending SMS for order {order.order_number}: {str(e)}")
                results['failed'] += 1
                results['details'].append({
                    'order': order.order_number,
                    'result': {'success': False, 'message': str(e)}
                })
        
        logger.info(f"Bulk SMS Results: {results['sent']} sent, {results['failed']} failed")
        return results
