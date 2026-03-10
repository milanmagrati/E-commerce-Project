"""
Hybrid EWS (Exponential Weighted Smoothing) + Spike Detection
for inventory restock prediction.

All functions expect `sales` as a list of daily int quantities,
oldest-first, length up to 30.
"""
import math


def exponential_smoothing(sales, alpha=0.4):
    """
    Exponential weighted smoothing — biases toward recent data.
    Returns the smoothed daily rate (float).

    alpha closer to 1.0 = more reactive to recent changes.
    alpha closer to 0.0 = more conservative / stable.
    """
    if not sales:
        return 0.0

    smoothed = float(sales[0])
    for value in sales[1:]:
        smoothed = alpha * float(value) + (1 - alpha) * smoothed
    return smoothed


def detect_spike(sales, spike_window=5, spike_threshold=2.0):
    """
    Returns True if the last `spike_window` days' average is >=
    `spike_threshold` times the baseline average (all days before
    the window).

    If there aren't enough data points for a meaningful baseline,
    returns False.
    """
    if len(sales) < spike_window + 1:
        return False

    recent = sales[-spike_window:]
    baseline = sales[:-spike_window]

    baseline_avg = sum(baseline) / len(baseline) if baseline else 0.0
    recent_avg = sum(recent) / len(recent)

    if baseline_avg <= 0:
        return recent_avg > 0

    return recent_avg >= spike_threshold * baseline_avg


def smart_daily_rate(sales):
    """
    Returns a blended daily rate:
      - Normal:  pure EWS rate
      - Spike:   (recent_5d_avg * 0.7) + (EWS * 0.3)

    This avoids under-predicting during genuine demand surges while
    staying stable during normal fluctuations.
    """
    if not sales:
        return 0.0

    ews_rate = exponential_smoothing(sales, alpha=0.4)
    spike = detect_spike(sales, spike_window=5, spike_threshold=2.0)

    if spike and len(sales) >= 5:
        recent_5d_avg = sum(sales[-5:]) / 5.0
        return (recent_5d_avg * 0.7) + (ews_rate * 0.3)

    return ews_rate


def days_of_stock_remaining(stock, sales):
    """
    Estimated days of stock remaining = stock / smart_daily_rate.
    Returns 999 if daily rate is zero (effectively infinite).
    """
    rate = smart_daily_rate(sales)
    if rate <= 0:
        return 999
    return math.ceil(stock / rate)


def restock_urgency(days_left, lead_time_days=3):
    """
    Urgency label based on remaining days vs supplier lead time.

      critical — stock will run out before next restock can arrive
      urgent   — cutting it very close (less than double lead time)
      low      — comfortable but monitor (within 14 days)
      ok       — healthy inventory
    """
    if days_left <= lead_time_days:
        return "critical"
    elif days_left <= lead_time_days * 2:
        return "urgent"
    elif days_left <= 14:
        return "low"
    return "ok"
