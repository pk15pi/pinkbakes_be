"""Short-lived caches for stable public catalog + admin overview KPIs.

Never cache: checkout, payments, inventory mutations, coupon redemption,
auth tokens, or per-user cart state.

Keys / TTLs / invalidation
--------------------------
- catalog:categories:v1
    TTL 60s. Public category list. Invalidated on Product save/delete
    (and manually via invalidate_catalog_caches).
- catalog:products:list:{sha1(query)}
    TTL 30s. Optional public product-list payload cache for unfiltered /
    category/search list responses (non-auth). Invalidated with categories.
- admin:dashboard:{preset}:{from}:{to}
    TTL 20s. Admin overview KPI snapshot only. Not used for payment verify
    or order mutations. Expires quickly so financial counts stay near-live.
"""
from __future__ import annotations

import hashlib

from django.core.cache import cache

CATEGORIES_KEY = "catalog:categories:v1"
CATEGORIES_TTL = 60
PRODUCT_LIST_TTL = 30
ADMIN_DASHBOARD_TTL = 20


def product_list_cache_key(query_items) -> str:
    raw = "&".join(f"{k}={v}" for k, v in sorted(query_items))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"catalog:products:list:{digest}"


def admin_dashboard_cache_key(preset: str, from_date: str | None, to_date: str | None) -> str:
    return f"admin:dashboard:{preset or 'today'}:{from_date or ''}:{to_date or ''}"


def invalidate_catalog_caches() -> None:
    """Drop category cache. Product-list keys are TTL-short; delete known prefix via version bump."""
    cache.delete(CATEGORIES_KEY)
    # Version bump invalidates all product-list keys without SCAN (locmem-friendly).
    try:
        ver = int(cache.get("catalog:products:ver") or 0)
    except (TypeError, ValueError):
        ver = 0
    cache.set("catalog:products:ver", ver + 1, timeout=None)


def products_cache_version() -> int:
    try:
        return int(cache.get("catalog:products:ver") or 0)
    except (TypeError, ValueError):
        return 0


def versioned_product_list_key(query_items) -> str:
    return f"{product_list_cache_key(query_items)}:v{products_cache_version()}"


def invalidate_admin_dashboard_caches() -> None:
    """Best-effort: locmem cannot list keys; rely on short TTL. No-op hook for Redis later."""
    return
