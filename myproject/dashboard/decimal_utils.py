"""
Decimal field safety utilities for handling invalid decimal values.
Prevents decimal.InvalidOperation errors at input and storage time.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging

logger = logging.getLogger(__name__)


def safe_decimal(value, max_digits=10, decimal_places=2, default='0'):
    """
    Safely convert a value to Decimal, ensuring it fits the field constraints.
    Also detects and corrects corrupted/extreme values.
    
    Args:
        value: Any value to convert (str, int, float, Decimal)
        max_digits: Total number of digits (including decimal places)
        decimal_places: Number of decimal places
        default: Default value if conversion fails (string)
    
    Returns:
        Decimal: Safe, validated decimal value
    
    Examples:
        >>> safe_decimal('123.45')
        Decimal('123.45')
        >>> safe_decimal('abc')
        Decimal('0')
        >>> safe_decimal(None)
        Decimal('0')
        >>> safe_decimal('999999999.99', max_digits=10, decimal_places=2)  # Too large
        Decimal('9999999.99')  # Clamped to max
    """
    if value is None or value == '':
        return Decimal(default)
    
    try:
        # Convert to string first to handle various types
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, (int, float)):
            result = Decimal(str(value))
        else:
            result = Decimal(str(value).strip())
        
        # Detect corrupted extreme values (over 12 decimal places)
        # These are typically database corruption issues
        if max_digits and decimal_places:
            # Check for extreme values that exceed reasonable business limits
            max_integer_digits = max_digits - decimal_places
            max_reasonable_value = Decimal(10) ** max_integer_digits
            
            if abs(result) > max_reasonable_value * 100:  # 100x the max for single field
                logger.warning(f"Corrupted decimal value detected: {result}. Resetting to 0.")
                return Decimal(default)
            
            result = clamp_decimal(result, max_digits, decimal_places)
        
        return result
    
    except (InvalidOperation, ValueError, TypeError) as e:
        logger.warning(f"Invalid decimal value '{value}': {e}. Using default '{default}'")
        try:
            return Decimal(default)
        except:
            return Decimal('0')


def clamp_decimal(value, max_digits=10, decimal_places=2):
    """
    Clamp a decimal value to fit max_digits and decimal_places constraints.
    
    Args:
        value: Decimal value to clamp
        max_digits: Total digits allowed (including decimal part)
        decimal_places: Number of decimal places allowed
    
    Returns:
        Decimal: Clamped value that fits the constraints
        
    Examples:
        >>> clamp_decimal(Decimal('999999999.99'), max_digits=10, decimal_places=2)
        Decimal('9999999.99')
        >>> clamp_decimal(Decimal('123.456'), max_digits=10, decimal_places=2)
        Decimal('123.46')  # Rounded to 2 decimal places
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    
    # Round to the specified decimal places
    quantize_factor = Decimal(10) ** -decimal_places
    value = value.quantize(quantize_factor, rounding=ROUND_HALF_UP)
    
    # Calculate max value based on max_digits and decimal_places constraints
    # For example: max_digits=10, decimal_places=2 means max is 99999999.99
    max_integer_digits = max_digits - decimal_places
    max_value = Decimal(10) ** max_integer_digits - Decimal(10) ** -decimal_places
    
    # Clamp to maximum
    if value > max_value:
        value = max_value
        logger.warning(f"Decimal value clamped to max: {value}")
    
    # Clamp to minimum (-max_value)
    min_value = -max_value
    if value < min_value:
        value = min_value
        logger.warning(f"Decimal value clamped to min: {value}")
    
    return value


def validate_decimal_fields(order):
    """
    Validate and repair all decimal fields in an Order instance.
    
    Args:
        order: Order instance to validate
    
    Returns:
        tuple: (order, list of fixed field names)
    """
    decimal_fields = {
        'discount_amount': (18, 2),
        'shipping_charge': (18, 2),
        'delivery_charge': (18, 2),
        'expense_amount': (18, 2),
        'tax_percent': (5, 2),
        'total_amount': (18, 2),
        'partial_amount_paid': (18, 2),
        'remaining_amount': (18, 2),
        'cod_collected': (18, 2),
        'package_weight': (8, 2),
    }
    
    fixed_fields = []
    
    for field_name, (max_digits, decimal_places) in decimal_fields.items():
        try:
            if hasattr(order, field_name):
                original_value = getattr(order, field_name)
                
                # Try to convert to Decimal safely
                if original_value is None:
                    new_value = Decimal('0')
                else:
                    new_value = safe_decimal(
                        original_value,
                        max_digits=max_digits,
                        decimal_places=decimal_places,
                        default='0'
                    )
                
                # Only update if value changed
                if original_value != new_value:
                    setattr(order, field_name, new_value)
                    fixed_fields.append(field_name)
                    logger.info(f"Order {order.id}: Fixed {field_name}: {original_value} -> {new_value}")
        
        except Exception as e:
            logger.error(f"Error validating {field_name} on Order {order.id}: {e}")
    
    return order, fixed_fields


def validate_order_item_decimal_fields(item):
    """
    Validate and repair all decimal fields in an OrderItem instance.
    
    Args:
        item: OrderItem instance to validate
    
    Returns:
        tuple: (item, list of fixed field names)
    """
    decimal_fields = {
        'price': (18, 2),
        'total': (18, 2),
    }
    
    fixed_fields = []
    
    for field_name, (max_digits, decimal_places) in decimal_fields.items():
        try:
            if hasattr(item, field_name):
                original_value = getattr(item, field_name)
                
                # Try to convert to Decimal safely
                if original_value is None:
                    new_value = Decimal('0')
                else:
                    new_value = safe_decimal(
                        original_value,
                        max_digits=max_digits,
                        decimal_places=decimal_places,
                        default='0'
                    )
                
                # Only update if value changed
                if original_value != new_value:
                    setattr(item, field_name, new_value)
                    fixed_fields.append(field_name)
                    logger.info(f"OrderItem {item.id}: Fixed {field_name}: {original_value} -> {new_value}")
        
        except Exception as e:
            logger.error(f"Error validating {field_name} on OrderItem {item.id}: {e}")
    
    return item, fixed_fields
