# PinkBakes performance notes (Grok pass)

## Caching (Django locmem — no Redis)

Defined in `pinkbakes_backend.settings.CACHES` (LocMemCache) and documented in `catalog.cache_utils`.

| Key | TTL | Contents | Invalidation |
|-----|-----|----------|--------------|
| `catalog:categories:v1` | 60s | Public category list payload | Product save/delete signals + `invalidate_catalog_caches()` |
| `catalog:products:list:{hash}:v{ver}` | 30s | Public product list (array or paginated) | Version bump on Product save/delete |
| `admin:dashboard:{preset}:{from}:{to}` | 20s | Admin overview KPI snapshot | Short TTL only (locmem cannot SCAN-delete) |
| `admin:report:summary:{...}` | 20s | Admin report summary | Short TTL only |

**Never cached:** checkout, payment create/verify/webhook, inventory adjust, coupon validate/redeem, auth tokens, cart validate, per-user orders.

## Frontend client cache (`authService.js`)

- `fetchCategories` TTL 60s, `fetchProducts` TTL 30s (memory Map).
- Cleared via `invalidateCatalogClientCache()` on admin product create/update/delete.
- Not used for payment/checkout/inventory/coupon/auth.

## Indexes

Migration `catalog.0013_product_perf_indexes`:
- `(is_active, status, featured, created_at)`
- `(category, is_active, status)`
- `(availability)`

## Pagination

Product list default remains a JSON **array** (SPA + tests). Optional `?page=` returns `{count,page,page_size,total_pages,results}` via existing `paginate_queryset` (max page_size 100). Unpaginated list hard-capped at 200.
