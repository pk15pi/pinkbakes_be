"""
Coupon / promo validation, reservation, redemption, and void helpers.

Lifecycle (documented choice):
  1. validate_coupon / POST /api/coupons/validate/ — preview only; does NOT create
     redemptions or bump usage counters.
  2. PaymentCreateView (and OrderCheckoutView when coupon_code is sent) —
     revalidate under select_for_update, attach coupon snapshot on Order,
     create CouponRedemption(status='pending'), increment Coupon.total_used
     so concurrent checkouts cannot oversell global usage_limit.
  3. PaymentService.mark_payment_paid — finalize pending → redeemed.
  4. PaymentService.cancel_order on unpaid cancel — void pending redemption and
     release the global total_used slot so abandoned carts do not burn limits.
     Per-user usage is NOT restored by default (COUPON_RESTORE_ON_CANCEL=false)
     so voided redemptions still count toward usage_limit_per_user (anti-abuse).
     Set COUPON_RESTORE_ON_CANCEL=true to exclude voided from per-user counts.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Coupon, CouponRedemption, Order, Product


class CouponError(ValueError):
    """Raised when a coupon cannot be applied."""


def normalize_code(code):
    return (code or '').strip().upper()


def _quantize(amount):
    return Decimal(amount).quantize(Decimal('0.01'))


def _eligible_line_total(coupon, cart_rows):
    """
    cart_rows: list of dicts with keys product (Product), quantity (int), unit_price (Decimal).
    Returns (eligible_subtotal, full_subtotal) based on applies_to / exclude_already_discounted.
    """
    full = Decimal('0')
    eligible = Decimal('0')
    product_ids = set()
    if coupon.applies_to == 'products':
        product_ids = set(coupon.products.values_list('id', flat=True))
    category_names = set()
    if coupon.applies_to == 'categories':
        category_names = {str(c).strip().lower() for c in (coupon.category_names or []) if str(c).strip()}

    for row in cart_rows:
        product = row['product']
        line = _quantize(row['unit_price'] * row['quantity'])
        full += line
        if coupon.exclude_already_discounted and int(getattr(product, 'discount', 0) or 0) > 0:
            continue
        if coupon.applies_to == 'products':
            if product.id not in product_ids:
                continue
        elif coupon.applies_to == 'categories':
            if (product.category or '').strip().lower() not in category_names:
                continue
        eligible += line
    return eligible, full


def _per_user_statuses():
    statuses = ['pending', 'redeemed']
    if not getattr(settings, 'COUPON_RESTORE_ON_CANCEL', False):
        statuses.append('voided')
    return statuses


def _user_redemption_count(coupon, user, exclude_redemption_id=None):
    if not user or not getattr(user, 'is_authenticated', False):
        return 0
    qs = CouponRedemption.objects.filter(
        coupon=coupon,
        user=user,
        status__in=_per_user_statuses(),
    )
    if exclude_redemption_id:
        qs = qs.exclude(pk=exclude_redemption_id)
    return qs.count()


def _is_new_customer(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return not Order.objects.filter(
        user=user,
        payment_status='paid',
    ).exclude(status='CANCELLED').exists()


def compute_discount_amount(coupon, eligible_subtotal):
    eligible_subtotal = _quantize(eligible_subtotal)
    if eligible_subtotal <= 0:
        return Decimal('0.00')
    if coupon.discount_type == 'percentage':
        raw = eligible_subtotal * (Decimal(coupon.discount_value) / Decimal('100'))
    else:
        raw = Decimal(coupon.discount_value)
    discount = _quantize(raw)
    if coupon.maximum_discount_amount is not None:
        discount = min(discount, _quantize(coupon.maximum_discount_amount))
    discount = min(discount, eligible_subtotal)
    if discount < 0:
        discount = Decimal('0.00')
    return discount


def validate_coupon(user, code, cart_items, *, lock=False):
    """
    Validate a coupon against backend-priced cart lines.
    cart_items: [{'id': product_id, 'quantity': n}, ...] OR pre-normalized rows
                with product/unit_price (from PaymentService.ensure_valid_cart).

    Returns dict:
      valid, code, discount_type, discount_value, discount_amount, message,
      coupon (model), cart_rows, subtotal, eligible_subtotal
    Raises CouponError on hard failure paths used by checkout; for the public
    validate API callers may catch and return valid=False.
    """
    normalized = normalize_code(code)
    if not normalized:
        raise CouponError('Coupon code is required.')

    qs = Coupon.objects.all()
    if lock:
        qs = qs.select_for_update()
    coupon = qs.filter(code=normalized).first()
    if not coupon:
        raise CouponError('Invalid coupon code.')
    if not coupon.is_active:
        raise CouponError('This coupon is inactive.')

    now = timezone.now()
    if coupon.start_at and now < coupon.start_at:
        raise CouponError('This coupon is not active yet.')
    if coupon.end_at and now > coupon.end_at:
        raise CouponError('This coupon has expired.')

    # Normalize cart to priced rows.
    if cart_items and isinstance(cart_items[0], dict) and 'product' in cart_items[0] and 'unit_price' in cart_items[0]:
        cart_rows = cart_items
    else:
        cart_rows = []
        if not cart_items:
            raise CouponError('Cart is empty.')
        for item in cart_items:
            if not isinstance(item, dict):
                raise CouponError('Cart items must be objects.')
            product_id = item.get('id')
            try:
                quantity = int(item.get('quantity', 1))
            except (TypeError, ValueError):
                raise CouponError('Quantity must be a number.')
            if product_id is None or quantity <= 0:
                raise CouponError('Each cart item needs a valid id and quantity.')
            product = Product.objects.filter(id=product_id, is_active=True).first()
            if not product:
                raise CouponError(f'Product #{product_id} is unavailable.')
            cart_rows.append({
                'product': product,
                'quantity': quantity,
                'unit_price': product.discounted_price,
            })

    eligible_subtotal, full_subtotal = _eligible_line_total(coupon, cart_rows)

    if coupon.minimum_order_amount and full_subtotal < _quantize(coupon.minimum_order_amount):
        raise CouponError(
            f'Minimum order amount of Rs.{_quantize(coupon.minimum_order_amount)} required for this coupon.'
        )

    if coupon.new_customers_only and not _is_new_customer(user):
        raise CouponError('This coupon is only for new customers.')

    if coupon.usage_limit is not None and int(coupon.total_used or 0) >= int(coupon.usage_limit):
        raise CouponError('This coupon has reached its usage limit.')

    if coupon.usage_limit_per_user is not None and user and getattr(user, 'is_authenticated', False):
        used = _user_redemption_count(coupon, user)
        if used >= int(coupon.usage_limit_per_user):
            raise CouponError('You have already used this coupon the maximum number of times.')

    if eligible_subtotal <= 0:
        raise CouponError('No eligible items in your cart for this coupon.')

    discount_amount = compute_discount_amount(coupon, eligible_subtotal)
    if discount_amount <= 0:
        raise CouponError('Coupon does not reduce the order total.')

    return {
        'valid': True,
        'code': coupon.code,
        'discount_type': coupon.discount_type,
        'discount_value': coupon.discount_value,
        'discount_amount': discount_amount,
        'message': 'Coupon applied successfully.',
        'coupon': coupon,
        'cart_rows': cart_rows,
        'subtotal': full_subtotal,
        'eligible_subtotal': eligible_subtotal,
    }


def public_validate_payload(user, code, cart_items):
    """Safe response for the validate API (never leaks admin internals)."""
    try:
        result = validate_coupon(user, code, cart_items, lock=False)
        return {
            'valid': True,
            'code': result['code'],
            'discount_type': result['discount_type'],
            'discount_amount': float(result['discount_amount']),
            'message': result['message'],
        }
    except CouponError as exc:
        return {
            'valid': False,
            'code': normalize_code(code),
            'discount_type': None,
            'discount_amount': 0,
            'message': str(exc),
        }


@transaction.atomic
def reserve_coupon_for_order(user, code, cart_rows, order):
    """
    Revalidate under lock, stamp Order coupon snapshot fields, create pending
    CouponRedemption, and increment total_used.
    Returns (discount_amount, coupon).
    """
    result = validate_coupon(user, code, cart_rows, lock=True)
    coupon = result['coupon']
    discount_amount = result['discount_amount']

    # Re-check limits after lock (total_used may have changed).
    if coupon.usage_limit is not None and int(coupon.total_used or 0) >= int(coupon.usage_limit):
        raise CouponError('This coupon has reached its usage limit.')
    if coupon.usage_limit_per_user is not None:
        used = _user_redemption_count(coupon, user)
        if used >= int(coupon.usage_limit_per_user):
            raise CouponError('You have already used this coupon the maximum number of times.')

    # Atomic conditional increment so concurrent checkouts cannot oversell usage_limit
    # even on SQLite where select_for_update is a no-op.
    from django.db.models import F
    qs = Coupon.objects.filter(pk=coupon.pk)
    if coupon.usage_limit is not None:
        qs = qs.filter(total_used__lt=coupon.usage_limit)
    updated = qs.update(total_used=F('total_used') + 1)
    if updated != 1:
        raise CouponError('This coupon has reached its usage limit.')

    order.coupon = coupon
    order.coupon_code = coupon.code
    order.coupon_discount_type = coupon.discount_type
    order.coupon_discount_value = coupon.discount_value
    order.coupon_discount_amount = discount_amount
    order.discount_amount = discount_amount
    order.save(update_fields=[
        'coupon', 'coupon_code', 'coupon_discount_type', 'coupon_discount_value',
        'coupon_discount_amount', 'discount_amount', 'updated_at',
    ])

    CouponRedemption.objects.create(
        coupon=coupon,
        user=user,
        order=order,
        discount_amount=discount_amount,
        status='pending',
    )
    coupon.refresh_from_db(fields=['total_used'])
    return discount_amount, coupon


@transaction.atomic
def finalize_redemption_for_order(order):
    """Mark pending redemption as redeemed when payment succeeds."""
    redemption = (
        CouponRedemption.objects.select_for_update()
        .filter(order=order, status='pending')
        .first()
    )
    if not redemption:
        return None
    redemption.status = 'redeemed'
    redemption.redeemed_at = timezone.now()
    redemption.save(update_fields=['status', 'redeemed_at'])
    return redemption


@transaction.atomic
def void_redemption_for_order(order, *, restore_per_user=None):
    """
    Void a pending (or optionally redeemed) redemption and release global usage.
    Per-user restore is controlled by COUPON_RESTORE_ON_CANCEL (default False):
    voided rows still count toward usage_limit_per_user unless restore is True.
    """
    if restore_per_user is None:
        restore_per_user = bool(getattr(settings, 'COUPON_RESTORE_ON_CANCEL', False))

    redemption = (
        CouponRedemption.objects.select_for_update()
        .filter(order=order, status__in=['pending', 'redeemed'])
        .first()
    )
    if not redemption:
        return None

    was_counting_global = redemption.status in ('pending', 'redeemed')
    redemption.status = 'voided'
    redemption.save(update_fields=['status'])

    if was_counting_global:
        coupon = Coupon.objects.select_for_update().filter(pk=redemption.coupon_id).first()
        if coupon and int(coupon.total_used or 0) > 0:
            Coupon.objects.filter(pk=coupon.pk).update(total_used=coupon.total_used - 1)

    # When restore_per_user is True, delete the voided row so it no longer counts.
    if restore_per_user:
        redemption.delete()
        return None
    return redemption


def apply_totals(subtotal, discount_amount, delivery_fee=None, tax_amount=None):
    """Compute payable total; never below zero."""
    subtotal = _quantize(subtotal)
    discount_amount = _quantize(discount_amount or 0)
    delivery_fee = _quantize(delivery_fee or 0)
    tax_amount = _quantize(tax_amount or 0)
    total = subtotal - discount_amount + delivery_fee + tax_amount
    if total < 0:
        total = Decimal('0.00')
    return {
        'subtotal_amount': subtotal,
        'discount_amount': discount_amount,
        'delivery_fee': delivery_fee,
        'tax_amount': tax_amount,
        'total_amount': _quantize(total),
    }
