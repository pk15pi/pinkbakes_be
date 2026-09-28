"""SEO helpers: absolute URLs and Product JSON-LD from real catalog data."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings


AVAILABILITY_SCHEMA = {
    'in_stock': 'https://schema.org/InStock',
    'low_stock': 'https://schema.org/InStock',
    'out_of_stock': 'https://schema.org/OutOfStock',
}


def public_site_url() -> str:
    raw = (
        getattr(settings, 'PUBLIC_SITE_URL', None)
        or getattr(settings, 'FRONTEND_URL', None)
        or 'https://pinkbakes.com'
    )
    return str(raw).rstrip('/')


def absolute_site_path(path: str) -> str:
    path = path or '/'
    if not path.startswith('/'):
        path = '/' + path
    return f'{public_site_url()}{path}'


def product_public_path(product) -> str:
    slug = (getattr(product, 'slug', None) or '').strip()
    if slug:
        return f'/products/{slug}'
    return f'/products/{product.pk}'


def product_public_url(product) -> str:
    return absolute_site_path(product_public_path(product))


def _plain_text(value: Any) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    # Strip naive tags; FE never injects raw HTML into JSON-LD either.
    while '<' in text and '>' in text:
        start = text.find('<')
        end = text.find('>', start)
        if end == -1:
            break
        text = text[:start] + ' ' + text[end + 1:]
    return ' '.join(text.split())


def build_product_jsonld(product, *, average_rating=None, review_count=None) -> dict:
    """Build schema.org Product dict. Omit unknown / unverified fields."""
    name = _plain_text(getattr(product, 'name', '') or 'Cake')
    description = _plain_text(
        getattr(product, 'short_description', '')
        or getattr(product, 'description', '')
        or ''
    )
    image = (getattr(product, 'main_image', None) or '').strip()
    if not image:
        images = getattr(product, 'images', None) or []
        if isinstance(images, list) and images:
            image = str(images[0]).strip()

    try:
        price = product.discounted_price
    except Exception:
        price = getattr(product, 'price', 0)
    if isinstance(price, Decimal):
        price_str = f'{price.quantize(Decimal("0.01"))}'
    else:
        price_str = f'{Decimal(str(price or 0)).quantize(Decimal("0.01"))}'

    currency = getattr(settings, 'RAZORPAY_CURRENCY', None) or 'INR'
    availability = AVAILABILITY_SCHEMA.get(
        getattr(product, 'availability', '') or 'in_stock',
        'https://schema.org/InStock',
    )
    # Stock zero forces OutOfStock even if availability flag lags.
    try:
        qty = int(getattr(product, 'available_quantity', 0) or 0)
        if qty <= 0:
            availability = 'https://schema.org/OutOfStock'
    except (TypeError, ValueError):
        pass

    data: dict[str, Any] = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': name,
        'url': product_public_url(product),
        'sku': str(product.pk),
        'offers': {
            '@type': 'Offer',
            'url': product_public_url(product),
            'priceCurrency': currency,
            'price': price_str,
            'availability': availability,
        },
    }
    if description:
        data['description'] = description
    if image:
        data['image'] = [image] if isinstance(image, str) else list(image)
    category = _plain_text(getattr(product, 'category', '') or '')
    if category:
        data['category'] = category

    # aggregateRating only from approved public reviews (caller supplies counts).
    try:
        count = int(review_count or 0)
    except (TypeError, ValueError):
        count = 0
    try:
        rating_val = float(average_rating) if average_rating is not None else None
    except (TypeError, ValueError):
        rating_val = None
    if count > 0 and rating_val is not None and rating_val > 0:
        data['aggregateRating'] = {
            '@type': 'AggregateRating',
            'ratingValue': round(rating_val, 1),
            'reviewCount': count,
            'bestRating': 5,
            'worstRating': 1,
        }
    return data


def build_organization_jsonld() -> dict:
    """Organization JSON-LD using only verified project contact facts."""
    email = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or 'pinkbakes@pinkbakes.com'
    data = {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        'name': 'pinkbakes',
        'url': public_site_url(),
        'email': email,
    }
    # WhatsApp number is verified in the storefront; expose as telephone when present.
    phone = getattr(settings, 'PUBLIC_CONTACT_PHONE', '') or ''
    if phone:
        data['telephone'] = str(phone)
    return data


def build_website_jsonld() -> dict:
    return {
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        'name': 'pinkbakes',
        'url': public_site_url(),
        # No SearchAction: site search is client-side only (no dedicated search URL).
    }


def xml_text(value: str) -> str:
    return xml_escape(value or '', {'"': '&quot;', "'": '&apos;'})
