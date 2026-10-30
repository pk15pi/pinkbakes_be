"""Server-side customer cart (one per signed-in user).

Only product, chosen options and quantity are stored. Prices are never stored or
read from the client: every read re-prices lines from the live catalog, and
lines that can no longer be sold (product hidden/out of stock, size no longer
priced, expired delivery date) are removed and reported back.
"""
import json
from decimal import Decimal

from django.db import transaction

from .models import Cart, CartItem, Product

CART_QTY_MAX = 40  # mirrors CART_QTY_MAX in the storefront
CART_MAX_LINES = 50
_CENTS = Decimal('0.01')


class CartInputError(ValueError):
    """Malformed cart payload (400)."""


def options_key(options):
    return json.dumps(options or {}, sort_keys=True, separators=(',', ':'))


def _clean_options(raw, product):
    from .views import PaymentService  # local import: views imports this module
    return PaymentService.clean_item_options(raw, product)


def _parse_qty(value):
    if isinstance(value, bool):
        raise CartInputError('Quantity must be a whole number.')
    try:
        qty = int(value)
    except (TypeError, ValueError):
        raise CartInputError('Quantity must be a whole number.')
    if isinstance(value, float) and value != qty:
        raise CartInputError('Quantity must be a whole number.')
    if qty < 1 or qty > CART_QTY_MAX:
        raise CartInputError(f'Quantity must be between 1 and {CART_QTY_MAX}.')
    return qty


def _product_for(product_id):
    if isinstance(product_id, bool):
        return None
    try:
        pid = int(product_id)
    except (TypeError, ValueError):
        return None
    return Product.objects.filter(id=pid).first()


def unavailable_reason(product, options):
    if product is None or not product.is_active or product.status != 'published':
        return 'This cake is no longer available.'
    if product.availability == 'out_of_stock' or int(product.available_quantity or 0) <= 0:
        return f'"{product.name}" is out of stock.'
    try:
        product.base_price_for_size((options or {}).get('size'))
    except ValueError:
        return f'The chosen size of "{product.name}" is no longer offered.'
    return None


def parse_lines(raw_items, lenient=False):
    """Validate client lines.

    Returns (lines, skipped) where lines are (product, options, qty).
    Malformed input raises CartInputError unless ``lenient`` (guest-cart merge),
    in which case it is skipped. Unsellable products/sizes are always skipped
    and reported, never stored.
    """
    if not isinstance(raw_items, list):
        raise CartInputError('items must be a list.')
    if len(raw_items) > CART_MAX_LINES:
        raise CartInputError(f'A cart can hold at most {CART_MAX_LINES} lines.')
    lines, skipped = [], []
    for raw in raw_items:
        ref = raw.get('id') if isinstance(raw, dict) else None
        try:
            if not isinstance(raw, dict):
                raise CartInputError('Each cart item must be an object.')
            qty = _parse_qty(raw.get('quantity', 1))
            product = _product_for(raw.get('id'))
            if product is None:
                skipped.append({'id': ref, 'reason': 'This cake is no longer available.'})
                continue
            try:
                options = _clean_options(raw.get('options'), product)
            except ValueError as exc:
                raise CartInputError(str(exc))
            reason = unavailable_reason(product, options)
            if reason:
                skipped.append({'id': product.id, 'name': product.name, 'reason': reason})
                continue
            lines.append((product, options, qty))
        except CartInputError as exc:
            if not lenient:
                raise
            skipped.append({'id': ref, 'reason': str(exc)})
    return lines, skipped


def _locked_cart(user):
    Cart.objects.get_or_create(user=user)
    return Cart.objects.select_for_update().get(user=user)


@transaction.atomic
def replace_cart(user, lines):
    cart = _locked_cart(user)
    merged = {}
    for product, options, qty in lines:
        key = (product.id, options_key(options))
        if key in merged:
            merged[key][2] += qty
        else:
            merged[key] = [product, options, qty]
    cart.items.all().delete()
    CartItem.objects.bulk_create([
        CartItem(cart=cart, product=product, options=options or {}, options_key=options_key(options),
                 quantity=min(qty, CART_QTY_MAX))
        for product, options, qty in merged.values()
    ])
    cart.save(update_fields=['updated_at'])


@transaction.atomic
def merge_into_cart(user, lines):
    """Add guest lines to the saved cart: same product+options sum, capped."""
    cart = _locked_cart(user)
    for product, options, qty in lines:
        key = options_key(options)
        item = cart.items.filter(product=product, options_key=key).first()
        if item:
            item.quantity = min(item.quantity + qty, CART_QTY_MAX)
            item.save(update_fields=['quantity', 'updated_at'])
        elif cart.items.count() < CART_MAX_LINES:
            CartItem.objects.create(cart=cart, product=product, options=options or {}, options_key=key,
                                    quantity=min(qty, CART_QTY_MAX))
    cart.save(update_fields=['updated_at'])


def clear_cart(user_id):
    CartItem.objects.filter(cart__user_id=user_id).delete()


def serialize_cart(user, request=None):
    """Current cart, re-priced from the catalog. Unsellable lines are deleted and reported."""
    from .serializers import ProductListSerializer

    cart, _ = Cart.objects.get_or_create(user=user)
    items, removed, adjusted = [], [], []
    subtotal = Decimal('0.00')
    for item in cart.items.select_related('product').order_by('id'):
        product = item.product
        options = item.options or None
        reason = unavailable_reason(product, options)
        if not reason and options:
            try:
                options = _clean_options(options, product)
            except ValueError as exc:
                reason = str(exc)
        if reason:
            removed.append({'id': product.id, 'name': product.name, 'options': item.options or None, 'reason': reason})
            item.delete()
            continue
        qty = max(1, min(item.quantity, CART_QTY_MAX, int(product.available_quantity or 0)))
        if qty != item.quantity:
            adjusted.append({'id': product.id, 'name': product.name, 'from': item.quantity, 'to': qty})
            item.quantity = qty
            item.save(update_fields=['quantity', 'updated_at'])
        unit = Decimal(product.discounted_price_for_size((options or {}).get('size'))).quantize(_CENTS)
        line_total = (unit * qty).quantize(_CENTS)
        subtotal += line_total
        items.append({
            'id': product.id,
            'options': options,
            'quantity': qty,
            'unit_price': str(unit),
            'line_total': str(line_total),
            'product': ProductListSerializer(product, context={'request': request}).data,
        })
    return {
        'items': items,
        'count': sum(row['quantity'] for row in items),
        'subtotal': str(subtotal.quantize(_CENTS)),
        'removed': removed,
        'adjusted': adjusted,
        'max_quantity': CART_QTY_MAX,
    }