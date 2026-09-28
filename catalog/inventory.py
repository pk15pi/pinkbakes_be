"""
Inventory / stock service for PinkBakes.

Quantity model (on Product):
  available_quantity  — units currently sellable
  reserved_quantity   — held for PENDING unpaid orders
  sold_quantity       — permanently consumed after payment
  low_stock_threshold — used to sync availability enum

Lifecycle:
  reserve(order)  : available -= qty, reserved += qty   (PaymentCreate / checkout)
  consume(order)  : reserved  -= qty, sold += qty       (mark_payment_paid, idempotent)
  release(order)  : reserved  -= qty, available += qty  (cancel unpaid / fail)
  restore(order)  : sold      -= qty, available += qty  (cancel after paid, idempotent)

availability (in_stock / low_stock / out_of_stock) is derived from available_quantity
and is kept separate from product status (draft/published/archived).
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone


class InsufficientStock(Exception):
    def __init__(self, product, requested, available):
        self.product = product
        self.requested = requested
        self.available = available
        name = getattr(product, 'name', 'Product')
        super().__init__(f'Only {available} units of "{name}" are currently available.')


class InventoryError(Exception):
    pass


def default_low_stock_threshold():
    return int(getattr(settings, 'DEFAULT_LOW_STOCK_THRESHOLD', 5) or 5)


def get_available(product):
    return int(getattr(product, 'available_quantity', 0) or 0)


def sync_availability(product, save=True):
    """Derive availability enum from available_quantity. Does not touch status."""
    available = get_available(product)
    threshold = int(getattr(product, 'low_stock_threshold', None) or default_low_stock_threshold())
    previous = getattr(product, 'availability', None) or ''
    if available <= 0:
        new_value = 'out_of_stock'
    elif available <= threshold:
        new_value = 'low_stock'
    else:
        new_value = 'in_stock'
    if product.availability != new_value:
        product.availability = new_value
        if save:
            product.save(update_fields=['availability', 'updated_at'])
        # Alert only when crossing into low_stock from a healthier state.
        if new_value == 'low_stock' and previous in ('in_stock', '', None):
            try:
                from notifications.service import notify_low_stock
                notify_low_stock(product, previous_availability=previous or '')
            except Exception:
                pass
    return product.availability


def _lock_product(product_id):
    from .models import Product
    # select_for_update works with Postgres; on SQLite it is a no-op but still
    # runs inside the outer atomic() so concurrent writers serialize at DB level.
    return Product.objects.select_for_update().get(pk=product_id)


def _record_txn(
    product,
    quantity_change,
    previous_quantity,
    new_quantity,
    adjustment_type,
    reason='',
    reference_type='',
    reference_id='',
    performed_by=None,
):
    from .models import InventoryTransaction
    return InventoryTransaction.objects.create(
        product=product,
        quantity_change=quantity_change,
        previous_quantity=previous_quantity,
        new_quantity=new_quantity,
        adjustment_type=adjustment_type,
        reason=reason or '',
        reference_type=reference_type or '',
        reference_id=str(reference_id or ''),
        performed_by=performed_by,
    )


def _has_txn(order_id, adjustment_type):
    from .models import InventoryTransaction
    return InventoryTransaction.objects.filter(
        reference_type='order',
        reference_id=str(order_id),
        adjustment_type=adjustment_type,
    ).exists()


def _order_item_rows(order):
    return list(order.items.select_related('product').all())


@transaction.atomic
def reserve(product_id, qty, order_id, user=None, reason=''):
    """Atomically move qty from available → reserved for an order line."""
    qty = int(qty)
    if qty <= 0:
        raise InventoryError('Reserve quantity must be greater than zero.')

    product = _lock_product(product_id)
    available = get_available(product)
    if available < qty:
        raise InsufficientStock(product, qty, available)

    previous = available
    product.available_quantity = available - qty
    product.reserved_quantity = int(product.reserved_quantity or 0) + qty
    product.stock_updated_at = timezone.now()
    sync_availability(product, save=False)
    product.save(update_fields=[
        'available_quantity', 'reserved_quantity', 'availability',
        'stock_updated_at', 'updated_at',
    ])
    _record_txn(
        product=product,
        quantity_change=-qty,
        previous_quantity=previous,
        new_quantity=product.available_quantity,
        adjustment_type='ORDER_RESERVED',
        reason=reason or f'Reserved for order {order_id}',
        reference_type='order',
        reference_id=order_id,
        performed_by=user,
    )
    return product


@transaction.atomic
def reserve_order(order, user=None):
    """
    Reserve stock for every line on an order. Idempotent if ORDER_RESERVED
    transactions already exist for this order. Rolls back entirely on failure.
    """
    if _has_txn(order.id, 'ORDER_RESERVED'):
        return order

    for item in _order_item_rows(order):
        if not item.product_id:
            continue
        reserve(
            product_id=item.product_id,
            qty=item.quantity,
            order_id=order.id,
            user=user,
            reason=f'Reserved for order {order.order_number}',
        )
    return order


@transaction.atomic
def consume(order_id, user=None):
    """Convert reserved → sold for an order. Idempotent."""
    from .models import Order

    if _has_txn(order_id, 'ORDER_CONSUMED'):
        return False

    if not _has_txn(order_id, 'ORDER_RESERVED'):
        # Nothing reserved (legacy order) — nothing to consume.
        return False

    if _has_txn(order_id, 'ORDER_RELEASED'):
        # Already released (cancel unpaid) — cannot consume.
        return False

    order = Order.objects.select_related('user').prefetch_related('items__product').get(pk=order_id)
    for item in _order_item_rows(order):
        if not item.product_id:
            continue
        product = _lock_product(item.product_id)
        qty = int(item.quantity)
        reserved = int(product.reserved_quantity or 0)
        take = min(reserved, qty)
        previous = get_available(product)
        product.reserved_quantity = max(0, reserved - take)
        product.sold_quantity = int(product.sold_quantity or 0) + take
        product.stock_updated_at = timezone.now()
        sync_availability(product, save=False)
        product.save(update_fields=[
            'reserved_quantity', 'sold_quantity', 'availability',
            'stock_updated_at', 'updated_at',
        ])
        _record_txn(
            product=product,
            quantity_change=0,
            previous_quantity=previous,
            new_quantity=product.available_quantity,
            adjustment_type='ORDER_CONSUMED',
            reason=f'Consumed for paid order {order.order_number}',
            reference_type='order',
            reference_id=order_id,
            performed_by=user,
        )
    return True


@transaction.atomic
def release(order_id, user=None):
    """Return reserved → available (unpaid cancel / payment fail). Idempotent."""
    from .models import Order

    if _has_txn(order_id, 'ORDER_RELEASED'):
        return False
    if _has_txn(order_id, 'ORDER_CONSUMED'):
        # Already sold — use restore instead.
        return False
    if not _has_txn(order_id, 'ORDER_RESERVED'):
        return False

    order = Order.objects.prefetch_related('items__product').get(pk=order_id)
    for item in _order_item_rows(order):
        if not item.product_id:
            continue
        product = _lock_product(item.product_id)
        qty = int(item.quantity)
        reserved = int(product.reserved_quantity or 0)
        give = min(reserved, qty)
        previous = get_available(product)
        product.reserved_quantity = max(0, reserved - give)
        product.available_quantity = previous + give
        product.stock_updated_at = timezone.now()
        sync_availability(product, save=False)
        product.save(update_fields=[
            'available_quantity', 'reserved_quantity', 'availability',
            'stock_updated_at', 'updated_at',
        ])
        _record_txn(
            product=product,
            quantity_change=give,
            previous_quantity=previous,
            new_quantity=product.available_quantity,
            adjustment_type='ORDER_RELEASED',
            reason=f'Released reservation for order {order.order_number}',
            reference_type='order',
            reference_id=order_id,
            performed_by=user,
        )
    return True


@transaction.atomic
def restore(order_id, user=None):
    """Return sold → available after paid cancel. Idempotent."""
    from .models import Order

    if _has_txn(order_id, 'ORDER_RESTORED'):
        return False

    # Prefer restoring from sold (post-consume). If still only reserved, release.
    if not _has_txn(order_id, 'ORDER_CONSUMED'):
        return release(order_id, user=user)

    order = Order.objects.prefetch_related('items__product').get(pk=order_id)
    for item in _order_item_rows(order):
        if not item.product_id:
            continue
        product = _lock_product(item.product_id)
        qty = int(item.quantity)
        sold = int(product.sold_quantity or 0)
        give = min(sold, qty)
        previous = get_available(product)
        product.sold_quantity = max(0, sold - give)
        product.available_quantity = previous + give
        product.stock_updated_at = timezone.now()
        sync_availability(product, save=False)
        product.save(update_fields=[
            'available_quantity', 'sold_quantity', 'availability',
            'stock_updated_at', 'updated_at',
        ])
        _record_txn(
            product=product,
            quantity_change=give,
            previous_quantity=previous,
            new_quantity=product.available_quantity,
            adjustment_type='ORDER_RESTORED',
            reason=f'Restored stock after cancel of order {order.order_number}',
            reference_type='order',
            reference_id=order_id,
            performed_by=user,
        )
    return True


@transaction.atomic
def adjust(product, *, action='adjust', quantity=0, reason='', admin=None, low_stock_threshold=None):
    """
    Admin stock adjustment.
    action: restock | remove | set | adjust
      restock: available += quantity
      remove:  available -= quantity (floored at 0)
      set:     available = quantity
      adjust:  available += quantity (quantity may be negative)
    """
    product = _lock_product(product.pk if hasattr(product, 'pk') else product)
    qty = int(quantity)
    previous = get_available(product)

    if action == 'restock':
        if qty < 0:
            raise InventoryError('Restock quantity must be non-negative.')
        new_available = previous + qty
        adj_type = 'RESTOCK'
        change = qty
    elif action == 'remove':
        if qty < 0:
            raise InventoryError('Remove quantity must be non-negative.')
        new_available = max(0, previous - qty)
        adj_type = 'MANUAL_ADJUSTMENT'
        change = new_available - previous
    elif action == 'set':
        if qty < 0:
            raise InventoryError('Stock quantity cannot be negative.')
        new_available = qty
        adj_type = 'MANUAL_ADJUSTMENT'
        change = new_available - previous
    elif action == 'adjust':
        new_available = previous + qty
        if new_available < 0:
            raise InventoryError('Adjustment would make stock negative.')
        adj_type = 'MANUAL_ADJUSTMENT'
        change = qty
    else:
        raise InventoryError(f'Unknown adjust action: {action}')

    product.available_quantity = new_available
    if low_stock_threshold is not None:
        product.low_stock_threshold = max(0, int(low_stock_threshold))
    product.stock_updated_at = timezone.now()
    sync_availability(product, save=False)
    update_fields = [
        'available_quantity', 'availability', 'stock_updated_at', 'updated_at',
    ]
    if low_stock_threshold is not None:
        update_fields.append('low_stock_threshold')
    product.save(update_fields=update_fields)
    _record_txn(
        product=product,
        quantity_change=change,
        previous_quantity=previous,
        new_quantity=new_available,
        adjustment_type=adj_type,
        reason=reason or f'Admin {action}',
        reference_type='admin',
        reference_id=str(admin.id) if admin else '',
        performed_by=admin,
    )
    return product


def validate_cart_items(items):
    """
    Validate cart line quantities against current available stock.
    items: list of {'id': product_id, 'quantity': n}
    Returns list of {product_id, name, requested, available, ok, detail}.
    Raises InsufficientStock if any line fails when raise_on_error=True path used by callers.
    """
    from .models import Product

    results = []
    errors = []
    # Aggregate quantities per product (same product may appear twice).
    aggregated = {}
    for item in items or []:
        if not isinstance(item, dict):
            raise InventoryError('Cart items must be objects.')
        pid = item.get('id')
        try:
            qty = int(item.get('quantity', 1))
        except (TypeError, ValueError):
            raise InventoryError('Quantity must be a number.')
        if pid is None:
            raise InventoryError('Each cart item needs an id and quantity.')
        if qty <= 0:
            raise InventoryError('Quantity must be greater than zero.')
        aggregated[pid] = aggregated.get(pid, 0) + qty

    products = {p.id: p for p in Product.objects.filter(id__in=list(aggregated.keys()), is_active=True)}
    for pid, qty in aggregated.items():
        product = products.get(pid)
        if not product:
            results.append({
                'product_id': pid,
                'name': None,
                'requested': qty,
                'available': 0,
                'ok': False,
                'detail': f'Product #{pid} is unavailable.',
            })
            errors.append(f'Product #{pid} is unavailable.')
            continue
        available = get_available(product)
        ok = available >= qty and product.availability != 'out_of_stock'
        detail = None if ok else f'Only {available} units of "{product.name}" are currently available.'
        results.append({
            'product_id': pid,
            'name': product.name,
            'requested': qty,
            'available': available,
            'ok': ok,
            'detail': detail,
            'availability': product.availability,
            'low_stock_threshold': product.low_stock_threshold,
        })
        if not ok:
            errors.append(detail)

    return results, errors


def seed_initial_stock_for_product(product):
    """Used by data migration / product create defaults."""
    if product.available_quantity and product.available_quantity > 0:
        sync_availability(product, save=True)
        return product
    if product.availability == 'out_of_stock':
        product.available_quantity = 0
    elif product.availability == 'low_stock':
        product.available_quantity = min(3, default_low_stock_threshold())
    else:
        product.available_quantity = 50
    product.reserved_quantity = product.reserved_quantity or 0
    product.sold_quantity = product.sold_quantity or 0
    if not product.low_stock_threshold:
        product.low_stock_threshold = default_low_stock_threshold()
    product.stock_updated_at = timezone.now()
    sync_availability(product, save=False)
    product.save(update_fields=[
        'available_quantity', 'reserved_quantity', 'sold_quantity',
        'low_stock_threshold', 'availability', 'stock_updated_at', 'updated_at',
    ])
    return product
