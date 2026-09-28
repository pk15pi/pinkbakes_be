"""Django-served robots.txt and sitemap.xml for pinkbakes.com crawlers."""
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from catalog.models import Product

from .utils import (
    absolute_site_path,
    build_organization_jsonld,
    build_product_jsonld,
    build_website_jsonld,
    product_public_url,
    public_site_url,
    xml_text,
)


# Paths disallowed for indexing (SPA private surfaces + API/admin).
ROBOTS_DISALLOW = [
    '/admin',
    '/admin-login',
    '/api/',
    '/cart',
    '/checkout',
    '/payment',
    '/account',
    '/orders',
    '/login',
    '/signup',
    '/signin',
    '/reset-password',
]


@require_GET
def robots_txt(request):
    site = public_site_url()
    lines = [
        'User-agent: *',
        'Allow: /',
    ]
    for path in ROBOTS_DISALLOW:
        lines.append(f'Disallow: {path}')
    lines.append('')
    lines.append(f'Sitemap: {site}/sitemap.xml')
    lines.append('')
    body = '\n'.join(lines)
    return HttpResponse(body, content_type='text/plain; charset=utf-8')


def _published_products():
    return (
        Product.objects.filter(is_active=True, status='published')
        .annotate(
            approved_review_count=Count('reviews', filter=Q(reviews__status='approved')),
            approved_avg_rating=Avg('reviews__rating', filter=Q(reviews__status='approved')),
        )
        .order_by('id')
    )


@require_GET
def sitemap_xml(request):
    """Homepage + static public section anchors + active published products."""
    now = timezone.now().date().isoformat()
    # Hash section links (#cakes, #about, …) are omitted: crawlers ignore fragments
    # and this SPA has no separate public routes for those sections yet.
    urls = [
        ('/', '1.0', 'daily'),
    ]

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, priority, changefreq in urls:
        loc = public_site_url() + '/' if path == '/' else absolute_site_path(path)
        parts.append('  <url>')
        parts.append(f'    <loc>{xml_text(loc)}</loc>')
        parts.append(f'    <lastmod>{now}</lastmod>')
        parts.append(f'    <changefreq>{changefreq}</changefreq>')
        parts.append(f'    <priority>{priority}</priority>')
        parts.append('  </url>')

    for product in _published_products():
        loc = product_public_url(product)
        lastmod = (product.updated_at.date().isoformat() if product.updated_at else now)
        parts.append('  <url>')
        parts.append(f'    <loc>{xml_text(loc)}</loc>')
        parts.append(f'    <lastmod>{lastmod}</lastmod>')
        parts.append('    <changefreq>weekly</changefreq>')
        parts.append('    <priority>0.8</priority>')
        parts.append('  </url>')

    parts.append('</urlset>')
    parts.append('')
    return HttpResponse('\n'.join(parts), content_type='application/xml; charset=utf-8')


@require_GET
def product_schema(request, pk):
    """Optional JSON Product schema payload for a published product (API helper)."""
    from django.http import JsonResponse

    product = (
        Product.objects.filter(pk=pk, is_active=True, status='published')
        .annotate(
            approved_review_count=Count('reviews', filter=Q(reviews__status='approved')),
            approved_avg_rating=Avg('reviews__rating', filter=Q(reviews__status='approved')),
        )
        .first()
    )
    if not product:
        return JsonResponse({'detail': 'Product not found.'}, status=404)

    payload = build_product_jsonld(
        product,
        average_rating=product.approved_avg_rating,
        review_count=product.approved_review_count,
    )
    return JsonResponse(payload)


@require_GET
def site_schema(request):
    """Organization + WebSite JSON-LD bundle (verified fields only)."""
    from django.http import JsonResponse

    return JsonResponse({
        'organization': build_organization_jsonld(),
        'website': build_website_jsonld(),
    })
