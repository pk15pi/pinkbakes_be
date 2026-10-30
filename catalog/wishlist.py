"""Customer wishlist (one row per signed-in user + product).

Mirrors the cart: guests keep a browser list, signed-in customers have a saved
list that is merged on sign-in. Products are re-validated on every read: hidden,
unpublished or deleted products are dropped and reported; out-of-stock products
stay listed but are flagged so the storefront can disable "Move to cart".
"""
from django.db import transaction

from .models import Product, WishlistItem

WISHLIST_MAX_ITEMS = 100


class WishlistInputError(ValueError):
    """Malformed wishlist payload (400)."""


def _visible(product):
    return product is not None and product.is_active and product.status == 'published'


def parse_product_id(value):
    if isinstance(value, bool):
        raise WishlistInputError('Product id must be a whole number.')
    try:
        pid = int(value)
    except (TypeError, ValueError):
        raise WishlistInputError('Product id must be a whole number.')
    if pid < 1:
        raise WishlistInputError('Product id must be a whole number.')
    return pid


def parse_ids(raw, lenient=False):
    """Return (products, skipped) for a list of product ids (or {id: ..} objects)."""
    if not isinstance(raw, list):
        raise WishlistInputError('items must be a list.')
    if len(raw) > WISHLIST_MAX_ITEMS:
        raise WishlistInputError(f'A wishlist can hold at most {WISHLIST_MAX_ITEMS} cakes.')
    products, skipped, seen = [], [], set()
    for entry in raw:
        ref = entry.get('id') if isinstance(entry, dict) else entry
        try:
            pid = parse_product_id(ref)
        except WishlistInputError:
            if not lenient:
                raise
            skipped.append({'id': ref, 'reason': 'Invalid product id.'})
            continue
        if pid in seen:
            continue
        seen.add(pid)
        product = Product.objects.filter(id=pid).first()
        if not _visible(product):
            skipped.append({'id': pid, 'reason': 'This cake is no longer available.'})
            continue
        products.append(product)
    return products, skipped


def get_visible_product(pid):
    product = Product.objects.filter(id=pid).first()
    return product if _visible(product) else None


@transaction.atomic
def replace_wishlist(user, products):
    WishlistItem.objects.filter(user=user).delete()
    WishlistItem.objects.bulk_create([WishlistItem(user=user, product=p) for p in products[:WISHLIST_MAX_ITEMS]])


@transaction.atomic
def merge_into_wishlist(user, products):
    existing = set(WishlistItem.objects.filter(user=user).values_list('product_id', flat=True))
    room = WISHLIST_MAX_ITEMS - len(existing)
    new = [p for p in products if p.id not in existing][:max(0, room)]
    WishlistItem.objects.bulk_create([WishlistItem(user=user, product=p) for p in new])


def add_item(user, product):
    if WishlistItem.objects.filter(user=user, product=product).exists():
        return
    if WishlistItem.objects.filter(user=user).count() >= WISHLIST_MAX_ITEMS:
        raise WishlistInputError(f'A wishlist can hold at most {WISHLIST_MAX_ITEMS} cakes.')
    WishlistItem.objects.get_or_create(user=user, product=product)


def remove_item(user, pid):
    WishlistItem.objects.filter(user=user, product_id=pid).delete()


def serialize_wishlist(user, request=None):
    from .serializers import ProductListSerializer

    items, removed = [], []
    for row in WishlistItem.objects.filter(user=user).select_related('product').order_by('-created_at', '-id'):
        product = row.product
        if not _visible(product):
            removed.append({'id': product.id, 'name': product.name, 'reason': 'This cake is no longer available.'})
            row.delete()
            continue
        in_stock = product.availability != 'out_of_stock' and int(product.available_quantity or 0) > 0
        items.append({
            'id': product.id,
            'added_at': row.created_at.isoformat() if row.created_at else None,
            'in_stock': in_stock,
            'product': ProductListSerializer(product, context={'request': request}).data,
        })
    return {'items': items, 'count': len(items), 'removed': removed, 'max_items': WISHLIST_MAX_ITEMS}
