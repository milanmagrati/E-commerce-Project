"""
Verifies the RTV-redirection quantity-matching fix in dashboard/views.py:
  - _parse_rtv_description_items() splits NCM's comma-joined package
    description into one {name, qty} entry per product (previously only the
    first product's quantity was ever parsed).
  - _product_matches() now REQUIRES the RTV's parsed quantity to equal the
    candidate order item's quantity whenever a quantity is present, instead of
    letting an exact product-name match silently override a quantity mismatch.

Uses unsaved OrderItem instances (no DB writes) since _product_matches only
reads .product_name / .quantity attributes.
"""
import sys, os
sys.path.append('.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
import django
django.setup()

from dashboard.models import OrderItem
from dashboard.views import _parse_rtv_description_items, _product_matches

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}")


def item(name, qty):
    return OrderItem(product_name=name, quantity=qty)


print("=" * 70)
print("TEST 1: _parse_rtv_description_items — multi-product descriptions")
print("=" * 70)

parsed = _parse_rtv_description_items("1x Hair Growth Serum")
check("single item parsed", len(parsed) == 1)
check("qty=1 parsed from prefix", parsed[0]['qty'] == 1)
check("name normalised", parsed[0]['name'] == 'hair growth serum')

parsed_multi = _parse_rtv_description_items(
    "2x Hair Growth Serum, 1x Vitamin C and 3 more"
)
check("multi-item split into 2 entries", len(parsed_multi) == 2)
check("trailing 'and N more' stripped from 2nd item",
      parsed_multi[1]['name'] == 'vitamin c')
check("first item qty", parsed_multi[0]['qty'] == 2)
check("second item qty", parsed_multi[1]['qty'] == 1)

print()
print("=" * 70)
print("TEST 2: The exact bug from the screenshots")
print("  NCM RTV package description: '1x Hair Growth Serum'")
print("  Candidate order T11608 wants: Hair Growth Serum x2")
print("=" * 70)

rtv_desc = "1x Hair Growth Serum"
order_items = [item("Hair Growth Serum", 2)]

matched = _product_matches(rtv_desc, order_items)
check("qty mismatch (RTV has 1, order needs 2) is correctly REJECTED",
      matched is False)

order_items_correct_qty = [item("Hair Growth Serum", 1)]
matched_correct = _product_matches(rtv_desc, order_items_correct_qty)
check("matching quantity (both 1) is correctly ACCEPTED",
      matched_correct is True)

print()
print("=" * 70)
print("TEST 3: Multi-product RTV vs multi-item order")
print("=" * 70)

rtv_desc_multi = "2x Hair Growth Serum, 1x Vitamin C"
order_items_multi = [item("Vitamin C", 1), item("Hair Growth Serum", 5)]
check("second product (qty 1) matches Vitamin C even though first product "
      "(qty 5 required vs 2 available) doesn't",
      _product_matches(rtv_desc_multi, order_items_multi) is True)

order_items_no_match = [item("Hair Growth Serum", 5), item("Vitamin C", 9)]
check("no product/qty combination matches -> rejected",
      _product_matches(rtv_desc_multi, order_items_no_match) is False)

print()
print("=" * 70)
print("TEST 4: No quantity info on RTV side falls back to name-only match")
print("=" * 70)

check("description without any Nx/xN token still matches by name",
      _product_matches("Hair Growth Serum", [item("Hair Growth Serum", 2)]) is True)

print()
print("=" * 70)
print("TEST 5: The SECOND bug — linked-local-order fallback names must carry an")
print("  explicit quantity even when qty=1, matching production data where")
print("  RTVOrder.product_description is empty for every current record, so")
print("  matching always falls back to _rtv_local_item_names.")
print("=" * 70)

# Old (buggy) formatting: quantity suffix was only appended when qty > 1, so a
# qty=1 original order item rendered as a bare name with no parseable quantity
# -- which _product_matches then treated as "no qty info, name match is enough",
# silently matching ANY candidate quantity.
old_style_label_qty1 = "Hair Growth Serum"  # what the old code emitted for qty=1
check("BUG (pre-fix behaviour): bare name with no qty wrongly matches qty=2 order",
      _product_matches(old_style_label_qty1, [item("Hair Growth Serum", 2)]) is True)

# New (fixed) formatting: dashboard/views.py now always appends ' x{qty}',
# including for qty=1, so the real quantity survives into the matcher.
new_style_label_qty1 = "Hair Growth Serum ×1"  # what the fixed code emits for qty=1
check("FIX: explicit x1 on the RTV side now correctly rejects a qty=2 candidate",
      _product_matches(new_style_label_qty1, [item("Hair Growth Serum", 2)]) is False)
check("FIX: explicit x1 on the RTV side still matches a qty=1 candidate",
      _product_matches(new_style_label_qty1, [item("Hair Growth Serum", 1)]) is True)

print()
print("=" * 70)
print(f"RESULT: {passed} passed, {failed} failed")
print("=" * 70)
sys.exit(1 if failed else 0)
