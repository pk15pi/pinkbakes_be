"""Whole-rupee money rounding shared by every customer-facing price.

Discounted unit prices, coupon discounts and delivery charges are rounded to
whole rupees (ROUND_HALF_UP) so the price a customer sees is exactly the price
that is charged. Values stay Decimals with 2 decimal places (e.g. 629.00) so
DecimalField types and the API shape do not change.
"""
from decimal import ROUND_HALF_UP, Decimal

_RUPEE = Decimal('1')
_PAISE = Decimal('0.01')


def round_rupees(amount):
    """Round to whole rupees, half-up, returned with 2 decimal places."""
    value = Decimal(str(amount)) if isinstance(amount, float) else Decimal(amount or 0)
    return value.quantize(_RUPEE, rounding=ROUND_HALF_UP).quantize(_PAISE)


def discounted_unit_price(list_price, discount_percent):
    """List price less a whole-number percentage discount, rounded to whole rupees."""
    discount = Decimal(discount_percent or 0) / Decimal(100)
    return round_rupees(Decimal(list_price or 0) * (Decimal(1) - discount))
