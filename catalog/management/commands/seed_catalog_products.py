"""Seed published catalog products so every canonical category has bakery items."""

from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from catalog.constants import CATALOG_CATEGORIES, CATEGORY_FALLBACK_IMAGES
from catalog.models import Product


# Real-looking bakery products; images reuse storefront Unsplash patterns.
SEED_PRODUCTS = [
    # Birthday
    {
        'name': 'Vanilla Dream Birthday Cake',
        'category': 'Birthday Cakes',
        'price': '1099.00',
        'discount': 0,
        'badge': '',
        'featured': True,
        'short_description': 'Light vanilla sponge with whipped cream and fresh berries.',
        'description': 'A classic birthday favourite — soft vanilla layers, cloud-like whipped cream, and a ring of seasonal berries. Perfect for kids and family celebrations.',
        'main_image': 'https://images.unsplash.com/photo-1563729784474-d77dbb933a9e?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1563729784474-d77dbb933a9e?auto=format&fit=crop&w=1400&q=90',
            'https://images.unsplash.com/photo-1571115177098-24ec42ed204d?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 40,
    },
    {
        'name': 'Rainbow Sprinkle Birthday Cake',
        'category': 'Birthday Cakes',
        'price': '1299.00',
        'discount': 5,
        'badge': 'Party Favourite',
        'featured': False,
        'short_description': 'Funfetti sponge finished with rainbow sprinkles and buttercream.',
        'description': 'Colourful funfetti layers topped with vanilla buttercream and generous rainbow sprinkles. A joyful centrepiece for any birthday table.',
        'main_image': 'https://images.unsplash.com/photo-1535141192574-5d4897c12636?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1535141192574-5d4897c12636?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 35,
    },
    {
        'name': 'Chocolate Fudge Birthday Cake',
        'category': 'Birthday Cakes',
        'price': '1199.00',
        'discount': 0,
        'badge': '',
        'featured': False,
        'short_description': 'Moist chocolate sponge with fudge frosting and chocolate curls.',
        'description': 'Deep cocoa sponge layered with chocolate fudge frosting and finished with dark chocolate curls — a crowd-pleasing birthday classic.',
        'main_image': 'https://images.unsplash.com/photo-1578985545062-69928b1d9587?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1578985545062-69928b1d9587?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 45,
    },
    # Anniversary
    {
        'name': 'Rose Garden Anniversary Cake',
        'category': 'Anniversary Cakes',
        'price': '1499.00',
        'discount': 0,
        'badge': 'Popular',
        'featured': True,
        'short_description': 'Elegant vanilla cake finished with buttercream roses.',
        'description': 'Delicate vanilla sponge with rose-scented buttercream blooms. An elegant choice for anniversaries and romantic celebrations.',
        'main_image': 'https://images.unsplash.com/photo-1535254973040-607b474cb50d?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1535254973040-607b474cb50d?auto=format&fit=crop&w=1400&q=90',
            'https://images.unsplash.com/photo-1571115177098-24ec42ed204d?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 30,
    },
    {
        'name': 'Golden Heart Anniversary Cake',
        'category': 'Anniversary Cakes',
        'price': '1599.00',
        'discount': 10,
        'badge': '',
        'featured': False,
        'short_description': 'Two-tone cream cake with edible gold accents and heart topper.',
        'description': 'Smooth cream layers with a soft caramel filling, finished with edible gold leaf and a handcrafted heart topper for milestone anniversaries.',
        'main_image': 'https://images.unsplash.com/photo-1519915028121-7d3463d20b13?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1519915028121-7d3463d20b13?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 25,
    },
    # Wedding
    {
        'name': 'Classic Tiered Wedding Cake',
        'category': 'Wedding Cakes',
        'price': '4999.00',
        'discount': 0,
        'badge': 'Wedding',
        'featured': True,
        'short_description': 'Three-tier vanilla and chocolate wedding cake with ivory frosting.',
        'description': 'A timeless three-tier celebration cake with alternating vanilla and chocolate layers, covered in smooth ivory frosting. Ideal for intimate weddings.',
        'main_image': 'https://images.unsplash.com/photo-1464349095431-e9a21285b5f3?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1464349095431-e9a21285b5f3?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 10,
        'delivery_time': '48-72 hours',
    },
    {
        'name': 'Floral Elegance Wedding Cake',
        'category': 'Wedding Cakes',
        'price': '5499.00',
        'discount': 0,
        'badge': '',
        'featured': False,
        'short_description': 'Tiered sponge dressed with fresh seasonal flowers.',
        'description': 'Soft sponge tiers with light cream cheese frosting, artfully decorated with fresh seasonal blooms for a garden-wedding look.',
        'main_image': 'https://images.unsplash.com/photo-1464349095431-e9a21285b5f3?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1464349095431-e9a21285b5f3?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 8,
        'delivery_time': '48-72 hours',
    },
    # Chocolate
    {
        'name': 'Chocolate Truffle Cake',
        'category': 'Chocolate Cakes',
        'price': '1299.00',
        'discount': 0,
        'badge': 'Bestseller',
        'featured': True,
        'short_description': 'Rich, moist and absolutely indulgent chocolate truffle layers.',
        'description': 'Dense chocolate sponge layered with silky truffle ganache. Our most-loved chocolate cake for birthdays and celebrations.',
        'main_image': 'https://images.unsplash.com/photo-1578985545062-69928b1d9587?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1578985545062-69928b1d9587?auto=format&fit=crop&w=1400&q=90',
            'https://images.unsplash.com/photo-1606890737304-57a1ca8a5b62?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 50,
    },
    {
        'name': 'Midnight Mocha Cake',
        'category': 'Chocolate Cakes',
        'price': '1599.00',
        'discount': 0,
        'badge': "Chef's Pick",
        'featured': False,
        'short_description': 'Deep chocolate sponge, espresso cream and dark ganache.',
        'description': 'Bold mocha sponge filled with espresso cream and finished with a glossy dark chocolate ganache — for serious chocolate lovers.',
        'main_image': 'https://images.unsplash.com/photo-1606890737304-57a1ca8a5b62?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1606890737304-57a1ca8a5b62?auto=format&fit=crop&w=1400&q=90',
            'https://images.unsplash.com/photo-1578985545062-69928b1d9587?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 35,
    },
    {
        'name': 'Belgian Dark Chocolate Cake',
        'category': 'Chocolate Cakes',
        'price': '1699.00',
        'discount': 5,
        'badge': '',
        'featured': False,
        'short_description': '70% Belgian cocoa sponge with dark ganache drip.',
        'description': 'Made with premium Belgian cocoa, this intense dark chocolate cake is finished with a dramatic ganache drip and cocoa nibs.',
        'main_image': 'https://images.unsplash.com/photo-1606890737304-57a1ca8a5b62?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1606890737304-57a1ca8a5b62?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 30,
    },
    # Designer
    {
        'name': 'Red Velvet Designer Cake',
        'category': 'Designer Cakes',
        'price': '1199.00',
        'discount': 0,
        'badge': 'New',
        'featured': True,
        'short_description': 'Velvety layers with a hint of cocoa and cream cheese frosting.',
        'description': 'Signature red velvet layers with tangy cream cheese frosting and a refined designer finish for parties and photos.',
        'main_image': 'https://images.unsplash.com/photo-1586788680434-30d324b2d46f?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1586788680434-30d324b2d46f?auto=format&fit=crop&w=1400&q=90',
            'https://images.unsplash.com/photo-1565958011703-44f9829ba187?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 40,
    },
    {
        'name': 'Berry Bliss Designer Cake',
        'category': 'Designer Cakes',
        'price': '1399.00',
        'discount': 0,
        'badge': '',
        'featured': False,
        'short_description': 'Chocolate sponge topped with mixed berries and cream.',
        'description': 'A perfect blend of chocolate sponge, light cream, and a crown of fresh strawberries and blueberries — as pretty as it is delicious.',
        'main_image': 'https://images.unsplash.com/photo-1565958011703-44f9829ba187?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1565958011703-44f9829ba187?auto=format&fit=crop&w=1400&q=90',
            'https://images.unsplash.com/photo-1557925923-cd4648e211a0?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 35,
    },
    {
        'name': 'Unicorn Fantasy Cake',
        'category': 'Designer Cakes',
        'price': '1799.00',
        'discount': 0,
        'badge': 'Kids Hit',
        'featured': False,
        'short_description': 'Pastel buttercream unicorn cake with edible shimmer.',
        'description': 'Hand-piped pastel buttercream, edible shimmer, and a whimsical unicorn horn — a showstopper for kids’ birthdays and themed parties.',
        'main_image': 'https://images.unsplash.com/photo-1535254973040-607b474cb50d?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1535254973040-607b474cb50d?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 20,
    },
    # Photo
    {
        'name': 'Custom Photo Print Cake',
        'category': 'Photo Cakes',
        'price': '1299.00',
        'discount': 0,
        'badge': '',
        'featured': True,
        'short_description': 'Edible photo print on soft vanilla sponge — upload your picture.',
        'description': 'Your favourite photo printed on an edible sheet over soft vanilla sponge and cream. Ideal for birthdays, retirements, and surprise parties.',
        'main_image': 'https://images.unsplash.com/photo-1559620192-032c4bc4674e?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1559620192-032c4bc4674e?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 40,
    },
    {
        'name': 'Family Memory Photo Cake',
        'category': 'Photo Cakes',
        'price': '1399.00',
        'discount': 5,
        'badge': '',
        'featured': False,
        'short_description': 'Chocolate photo cake with cream borders for family portraits.',
        'description': 'Rich chocolate base with an edible family portrait and cream borders — a thoughtful gift for reunions and anniversaries.',
        'main_image': 'https://images.unsplash.com/photo-1559620192-032c4bc4674e?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1559620192-032c4bc4674e?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 30,
    },
    # Custom
    {
        'name': 'Build-Your-Own Celebration Cake',
        'category': 'Custom Cakes',
        'price': '1599.00',
        'discount': 0,
        'badge': 'Custom',
        'featured': True,
        'short_description': 'Choose flavour, size, and finish — we bake it your way.',
        'description': 'Pick your sponge, filling, frosting, and message. Our bakers craft a one-of-a-kind celebration cake from your brief.',
        'main_image': 'https://images.unsplash.com/photo-1571115177098-24ec42ed204d?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1571115177098-24ec42ed204d?auto=format&fit=crop&w=1400&q=90',
            'https://images.unsplash.com/photo-1557925923-cd4648e211a0?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 25,
        'delivery_time': '48 hours',
    },
    {
        'name': 'Theme Party Custom Cake',
        'category': 'Custom Cakes',
        'price': '1899.00',
        'discount': 0,
        'badge': '',
        'featured': False,
        'short_description': 'Fully themed cake designed around your party concept.',
        'description': 'From cartoon characters to corporate branding, share your theme and we sculpt colours, toppers, and flavours to match.',
        'main_image': 'https://images.unsplash.com/photo-1557925923-cd4648e211a0?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1557925923-cd4648e211a0?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 20,
        'delivery_time': '48-72 hours',
    },
    # Eggless
    {
        'name': 'Eggless Butterscotch Delight',
        'category': 'Eggless Cakes',
        'price': '1099.00',
        'discount': 0,
        'badge': 'Eggless',
        'featured': True,
        'short_description': 'Soft eggless sponge with butterscotch cream and praline crunch.',
        'description': '100% eggless butterscotch cake with caramelised praline crunch — rich flavour without eggs, loved by all ages.',
        'main_image': 'https://images.unsplash.com/photo-1563729784474-d77dbb933a9e?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1563729784474-d77dbb933a9e?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 40,
    },
    {
        'name': 'Eggless Pineapple Cream Cake',
        'category': 'Eggless Cakes',
        'price': '999.00',
        'discount': 0,
        'badge': '',
        'featured': False,
        'short_description': 'Light eggless sponge with pineapple chunks and fresh cream.',
        'description': 'Airy eggless vanilla sponge layered with pineapple chunks and lightly sweetened cream — a refreshing classic.',
        'main_image': 'https://images.unsplash.com/photo-1571115177098-24ec42ed204d?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1571115177098-24ec42ed204d?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 45,
    },
    {
        'name': 'Eggless Black Forest Cake',
        'category': 'Eggless Cakes',
        'price': '1199.00',
        'discount': 5,
        'badge': 'Eggless',
        'featured': False,
        'short_description': 'Eggless chocolate sponge with cherries and whipped cream.',
        'description': 'Our eggless take on Black Forest — chocolate sponge, cherry filling, and clouds of whipped cream topped with chocolate shavings.',
        'main_image': 'https://images.unsplash.com/photo-1606890737304-57a1ca8a5b62?auto=format&fit=crop&w=1000&q=85',
        'images': [
            'https://images.unsplash.com/photo-1606890737304-57a1ca8a5b62?auto=format&fit=crop&w=1400&q=90',
        ],
        'available_quantity': 35,
    },
]


class Command(BaseCommand):
    help = 'Seed published products so every catalog category has at least one cake.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force-update',
            action='store_true',
            help='Update existing products matched by name with seed fields.',
        )

    def handle(self, *args, **options):
        force = options['force_update']
        created = 0
        updated = 0
        skipped = 0

        for spec in SEED_PRODUCTS:
            name = spec['name']
            existing = Product.objects.filter(name__iexact=name).first()
            if existing and not force:
                # Ensure published/active + category alignment without overwriting custom edits.
                dirty = False
                if existing.category != spec['category']:
                    existing.category = spec['category']
                    dirty = True
                if not existing.is_active:
                    existing.is_active = True
                    dirty = True
                if existing.status != 'published':
                    existing.status = 'published'
                    dirty = True
                if not (existing.main_image or '').strip():
                    existing.main_image = spec['main_image']
                    dirty = True
                if dirty:
                    existing.save()
                    updated += 1
                else:
                    skipped += 1
                continue

            defaults = {
                'category': spec['category'],
                'price': Decimal(spec['price']),
                'discount': int(spec.get('discount') or 0),
                'badge': spec.get('badge') or '',
                'featured': bool(spec.get('featured')),
                'short_description': spec.get('short_description') or '',
                'description': spec.get('description') or '',
                'main_image': spec.get('main_image') or CATEGORY_FALLBACK_IMAGES.get(spec['category'], ''),
                'images': list(spec.get('images') or []),
                'availability': 'in_stock',
                'status': 'published',
                'is_active': True,
                'available_quantity': int(spec.get('available_quantity') or 40),
                'low_stock_threshold': 5,
                'delivery_time': spec.get('delivery_time') or '24-48 hours',
                'rating': Decimal('4.8'),
            }

            if existing and force:
                for key, value in defaults.items():
                    setattr(existing, key, value)
                if not existing.slug:
                    existing.slug = slugify(name)
                existing.save()
                updated += 1
            else:
                Product.objects.create(name=name, **defaults)
                created += 1

        # Summary per category
        self.stdout.write(self.style.SUCCESS(
            f'Seed complete: created={created} updated={updated} skipped={skipped}'
        ))
        for cat in CATALOG_CATEGORIES:
            count = Product.objects.filter(
                category__iexact=cat, is_active=True, status='published'
            ).count()
            flag = 'OK' if count >= 1 else 'MISSING'
            self.stdout.write(f'  [{flag}] {cat}: {count}')

        # Point every product at local /products/cakes/* for Collab CSS 3D (main image).
        self.stdout.write('Wiring local cake images for 3D / catalog...')
        call_command('wire_local_cake_images')
