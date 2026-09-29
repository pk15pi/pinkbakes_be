# Cake product images

## Output
- Backend: `media/products/cakes/` (+ `manifest.json`)
- Frontend mirror: `../pinksBakes/public/products/cakes/`

## Re-download / expand
1. Get free API keys:
   - Unsplash: https://unsplash.com/developers → Access Key
   - Pexels: https://www.pexels.com/api/ → API Key
2. Set env (or put in backend `.env`):
   ```
   UNSPLASH_ACCESS_KEY=...
   PEXELS_API_KEY=...
   ```
3. Run:
   ```
   python scripts/download_cake_images.py
   ```
   The script resumes from `manifest.json`, skips duplicate photo IDs, resizes to max edge 1200px JPEG q≈84.

Without keys it uses `curated_cake_ids.json` (known Unsplash/Pexels CDN IDs). API keys give better cake-search relevance and volume.

## Point products at local files
`Product.main_image` / `images` are URLFields. Examples:
- Local Django media (DEBUG): `http://127.0.0.1:8000/media/products/cakes/BirthdayCakes_VanillaButtercream.jpg`
- FE public static: `/products/cakes/BirthdayCakes_VanillaButtercream.jpg`

Do not bulk-replace DB URLs unless intended; use `manifest.json` to pick files per category.

## Wire products to local files (Collab 3D v1)

Collab's Product3DViewer does CSS `perspective` rotate on `product.image`
(API: `main_image`, fallback first of `gallery`/`images`). No GLB / no 360 frames.

```
python manage.py wire_local_cake_images
python manage.py wire_local_cake_images --dry-run
python manage.py wire_local_cake_images --gallery-size 3
```

Sets `Product.main_image` and `Product.images` to `/products/cakes/CategoryPrefix_*.jpg`
(category-matched round-robin). Also runs automatically at the end of `seed_catalog_products`.
