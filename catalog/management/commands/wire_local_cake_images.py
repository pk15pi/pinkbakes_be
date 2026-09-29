"""Assign every Product.main_image (and images gallery) to local cake JPEGs.

Collab v1 3D contract: CSS perspective rotate on the MAIN product image only
(API field `image` <- main_image; gallery fallback if empty). No GLB / no 360 frames.

Usage:
  python manage.py wire_local_cake_images
  python manage.py wire_local_cake_images --dry-run
  python manage.py wire_local_cake_images --gallery-size 3
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand

from catalog.constants import CATALOG_CATEGORIES
from catalog.models import Product


# Product.category label -> filename prefix used by download_cake_images.py
CATEGORY_TO_PREFIX = {
    'Birthday Cakes': 'BirthdayCakes',
    'Anniversary Cakes': 'AnniversaryCakes',
    'Wedding Cakes': 'WeddingCakes',
    'Chocolate Cakes': 'ChocolateCakes',
    'Designer Cakes': 'DesignerCakes',
    'Photo Cakes': 'PhotoCakes',
    'Custom Cakes': 'CustomCakes',
    'Eggless Cakes': 'EgglessCakes',
}

# Public FE static path (Vite public/). Collab Product3DViewer reads product.image.
PUBLIC_PREFIX = '/products/cakes/'


def _cakes_dir() -> Path:
    """Prefer backend media mirror; fall back to FE public copy."""
    backend = Path(__file__).resolve().parents[3] / 'media' / 'products' / 'cakes'
    if backend.is_dir():
        return backend
    fe = Path(__file__).resolve().parents[4] / 'pinksBakes' / 'public' / 'products' / 'cakes'
    return fe


def _load_pools(cakes_dir: Path) -> dict[str, list[str]]:
    """Map category label -> list of public URLs, preferring manifest order."""
    pools: dict[str, list[str]] = defaultdict(list)
    manifest_path = cakes_dir / 'manifest.json'
    filenames: list[str] = []
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding='utf-8'))
            filenames = [item['filename'] for item in data.get('images', []) if item.get('filename')]
        except (json.JSONDecodeError, OSError, TypeError):
            filenames = []
    if not filenames:
        filenames = sorted(p.name for p in cakes_dir.glob('*.jpg'))

    prefix_to_label = {v: k for k, v in CATEGORY_TO_PREFIX.items()}
    for name in filenames:
        prefix = name.split('_', 1)[0]
        label = prefix_to_label.get(prefix)
        if not label:
            continue
        pools[label].append(f'{PUBLIC_PREFIX}{name}')

    # Ensure every canonical category has at least an empty list key
    for label in CATALOG_CATEGORIES:
        pools.setdefault(label, [])
    return pools


class Command(BaseCommand):
    help = 'Wire Product.main_image / images to local /products/cakes/*.jpg (category-matched).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print changes without saving.')
        parser.add_argument(
            '--gallery-size',
            type=int,
            default=3,
            help='Number of same-category images to put in Product.images (includes main). Default 3.',
        )
        parser.add_argument(
            '--published-only',
            action='store_true',
            default=False,
            help='Only update products with status=published (default: all products).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        gallery_size = max(1, int(options['gallery_size']))
        cakes_dir = _cakes_dir()
        if not cakes_dir.is_dir():
            self.stderr.write(self.style.ERROR(f'Cakes directory not found: {cakes_dir}'))
            return

        pools = _load_pools(cakes_dir)
        total_assets = sum(len(v) for v in pools.values())
        self.stdout.write(f'Cakes dir: {cakes_dir} ({total_assets} category-tagged JPEGs)')

        qs = Product.objects.all().order_by('id')
        if options['published_only']:
            qs = qs.filter(status='published')

        # Round-robin cursors per category so products get different files when possible
        cursors = {label: 0 for label in pools}

        updated = 0
        skipped_no_pool = 0
        already_local = 0

        for product in qs:
            label = product.category if product.category in pools else None
            if not label or not pools.get(label):
                # Fall back to Birthday Cakes pool, then any pool
                for fallback in CATALOG_CATEGORIES:
                    if pools.get(fallback):
                        label = fallback
                        break
            pool = pools.get(label) or []
            if not pool:
                skipped_no_pool += 1
                self.stderr.write(self.style.WARNING(f'No assets for product id={product.id} category={product.category!r}'))
                continue

            idx = cursors[label] % len(pool)
            cursors[label] = idx + 1
            main = pool[idx]
            # Gallery: main + next distinct frames from same category (wrap)
            gallery = []
            for offset in range(gallery_size):
                url = pool[(idx + offset) % len(pool)]
                if url not in gallery:
                    gallery.append(url)
            if main not in gallery:
                gallery.insert(0, main)

            was_local = (product.main_image or '').startswith(PUBLIC_PREFIX)
            if was_local and product.main_image == main and list(product.images or []) == gallery:
                already_local += 1
                continue

            self.stdout.write(
                f'  id={product.id} [{product.category}] {product.name!r}\n'
                f'    main_image -> {main}\n'
                f'    images[{len(gallery)}] -> {gallery[0]}...'
            )
            if not dry_run:
                product.main_image = main
                product.images = gallery
                product.save(update_fields=['main_image', 'images', 'updated_at'])
            updated += 1

        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {updated} products; already matching {already_local}; no-pool {skipped_no_pool}; '
            f'total scanned {qs.count()}'
        ))

        # Verification summary
        empty = Product.objects.filter(main_image='').count()
        local = Product.objects.filter(main_image__startswith=PUBLIC_PREFIX).count()
        total = Product.objects.count()
        self.stdout.write(
            f'Verify: total={total} local_main_image={local} empty_main_image={empty}'
        )
