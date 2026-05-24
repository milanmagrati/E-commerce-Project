"""
Reward points logic – calculates and credits points after a bill
is approved.
"""
import logging
from decimal import Decimal

from django.utils import timezone

from .models import RewardConfig, RewardTransaction, CustomerRewardBalance

logger = logging.getLogger('bill_rewards')


def calculate_points(total_amount, config=None):
    """Calculate reward points for a given bill total."""
    if config is None:
        config = RewardConfig.get_config()

    if not config.is_active:
        return 0

    if total_amount is None or total_amount < config.min_bill_total_for_reward:
        return 0

    raw_points = int(total_amount * config.points_per_currency_unit)
    return min(raw_points, config.max_points_per_bill)


def credit_points(customer, bill_upload, points, user=None):
    """
    Credit *points* to *customer* for a verified *bill_upload*.
    Returns the RewardTransaction or None.
    """
    if points <= 0:
        return None

    config = RewardConfig.get_config()

    # Get or create balance
    balance, _ = CustomerRewardBalance.objects.get_or_create(customer=customer)

    expires_at = None
    if config.points_expiry_days:
        expires_at = timezone.now() + timezone.timedelta(days=config.points_expiry_days)

    new_balance = balance.current_balance + points

    txn = RewardTransaction.objects.create(
        customer=customer,
        bill_upload=bill_upload,
        transaction_type='earned',
        points=points,
        balance_after=new_balance,
        description=f'Earned from bill {str(bill_upload.id)[:8]}',
        created_by=user,
        expires_at=expires_at,
    )

    # Update denormalized balance
    balance.total_earned += points
    balance.current_balance = new_balance
    balance.last_earned_at = timezone.now()
    balance.save()

    logger.info(f'Credited {points} pts to customer {customer.id}, balance={new_balance}')
    return txn


def redeem_points(customer, points, description='', user=None):
    """Redeem points from customer's balance."""
    if points <= 0:
        return None

    balance, _ = CustomerRewardBalance.objects.get_or_create(customer=customer)

    if balance.current_balance < points:
        raise ValueError(f'Insufficient balance: {balance.current_balance} < {points}')

    new_balance = balance.current_balance - points

    txn = RewardTransaction.objects.create(
        customer=customer,
        transaction_type='redeemed',
        points=-points,
        balance_after=new_balance,
        description=description or 'Points redeemed',
        created_by=user,
    )

    balance.total_redeemed += points
    balance.current_balance = new_balance
    balance.save()

    logger.info(f'Redeemed {points} pts from customer {customer.id}, balance={new_balance}')
    return txn
