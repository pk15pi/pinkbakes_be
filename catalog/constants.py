"""Canonical cake category labels used across admin, seed, and public catalog APIs.

Product.category is a CharField (no separate Category model); keep this list in sync
with the admin cake form and the home "Shop by category" occasion cards.
"""

CATALOG_CATEGORIES = [
    'Birthday Cakes',
    'Anniversary Cakes',
    'Wedding Cakes',
    'Chocolate Cakes',
    'Designer Cakes',
    'Photo Cakes',
    'Custom Cakes',
    'Eggless Cakes',
]

# Decorative fallback images (same Unsplash paths already used by the storefront).
CATEGORY_FALLBACK_IMAGES = {
    'Birthday Cakes': 'https://images.unsplash.com/photo-1535141192574-5d4897c12636?auto=format&fit=crop&w=600&q=85',
    'Anniversary Cakes': 'https://images.unsplash.com/photo-1519915028121-7d3463d20b13?auto=format&fit=crop&w=600&q=85',
    'Wedding Cakes': 'https://images.unsplash.com/photo-1464349095431-e9a21285b5f3?auto=format&fit=crop&w=600&q=85',
    'Chocolate Cakes': 'https://images.unsplash.com/photo-1578985545062-69928b1d9587?auto=format&fit=crop&w=600&q=85',
    'Designer Cakes': 'https://images.unsplash.com/photo-1535254973040-607b474cb50d?auto=format&fit=crop&w=600&q=85',
    'Photo Cakes': 'https://images.unsplash.com/photo-1559620192-032c4bc4674e?auto=format&fit=crop&w=600&q=85',
    'Custom Cakes': 'https://images.unsplash.com/photo-1571115177098-24ec42ed204d?auto=format&fit=crop&w=600&q=85',
    'Eggless Cakes': 'https://images.unsplash.com/photo-1563729784474-d77dbb933a9e?auto=format&fit=crop&w=600&q=85',
}
