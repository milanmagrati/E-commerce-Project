from django.core.management.base import BaseCommand
from django.db.models import Q
from decimal import Decimal
import logging
from dashboard.models import Order

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fetch and update delivery charges from NCM API for all orders'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update even if delivery_charge already exists',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of orders to update',
        )

    def handle(self, *args, **options):
        from services.ncm_service import NCMService
        
        try:
            ncm_service = NCMService()
            force = options.get('force', False)
            limit = options.get('limit')

            # Get NCM orders that either have no delivery_charge or need updating
            if force:
                orders = Order.objects.filter(ncm_order_id__isnull=False).exclude(is_deleted=True)
            else:
                # Only update orders with 0 or null delivery_charge
                orders = Order.objects.filter(
                    ncm_order_id__isnull=False,
                    is_deleted=False
                ).filter(
                    Q(delivery_charge__isnull=True) | Q(delivery_charge=0)
                )

            if limit:
                orders = orders[:limit]

            total = orders.count()
            updated = 0
            failed = 0
            skipped = 0

            self.stdout.write(self.style.SUCCESS(f'📦 Found {total} orders to process'))

            for idx, order in enumerate(orders, 1):
                try:
                    # Show progress
                    self.stdout.write(f'[{idx}/{total}] Processing order {order.order_number} (NCM ID: {order.ncm_order_id})...')

                    # Fetch order details from NCM API
                    details_result = ncm_service.get_order_details(order.ncm_order_id)

                    if details_result.get('success'):
                        details_data = details_result.get('data', {})
                        logger.info(f"NCM API response for {order.order_number}: {details_data}")

                        # Try multiple possible field names for delivery charge
                        delivery_charge = (
                            details_data.get('chargeDetail') or 
                            details_data.get('deliveryCharge') or 
                            details_data.get('deliverycharge') or 
                            details_data.get('delivery_charge') or 
                            details_data.get('chargedetail') or 
                            details_data.get('shippingCharge') or 
                            details_data.get('shipping_charge') or 
                            details_data.get('charge') or 
                            details_data.get('amount') or
                            0
                        )

                        if delivery_charge and float(delivery_charge) > 0:
                            old_value = order.delivery_charge
                            order.delivery_charge = Decimal(str(delivery_charge))
                            order.save(update_fields=['delivery_charge', 'updated_at'])
                            
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'   ✅ Updated: Rs. {old_value} → Rs. {delivery_charge}'
                                )
                            )
                            updated += 1
                        else:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'   ⚠️  No charge found in NCM response. Data keys: {list(details_data.keys())}'
                                )
                            )
                            skipped += 1
                            logger.warning(
                                f"No delivery charge found in NCM response for {order.order_number}. "
                                f"Response: {details_data}"
                            )
                    else:
                        self.stdout.write(
                            self.style.ERROR(
                                f'   ❌ Failed to fetch details: {details_result.get("error")}'
                            )
                        )
                        failed += 1
                        logger.error(
                            f"Failed to fetch order details for {order.order_number}: "
                            f"{details_result.get('error')}"
                        )

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'   ❌ Error: {str(e)}')
                    )
                    failed += 1
                    logger.error(f"Error processing order {order.order_number}: {str(e)}", exc_info=True)

            # Summary
            self.stdout.write('\n' + '='*60)
            self.stdout.write(self.style.SUCCESS(f'✅ Updated: {updated}'))
            self.stdout.write(self.style.WARNING(f'⚠️  Skipped: {skipped}'))
            self.stdout.write(self.style.ERROR(f'❌ Failed: {failed}'))
            self.stdout.write(self.style.SUCCESS(f'📊 Total processed: {updated + skipped + failed}/{total}'))
            self.stdout.write('='*60 + '\n')

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Fatal error: {str(e)}')
            )
            logger.error(f"Fatal error in fetch_ncm_delivery_charges: {str(e)}", exc_info=True)
