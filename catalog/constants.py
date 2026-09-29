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

# Local FE public static cake images (category-matched). Collab 3D uses product.image.
CATEGORY_FALLBACK_IMAGES = {
    'Birthday Cakes': '/products/cakes/BirthdayCakes_BerryMedley.jpg',
    'Anniversary Cakes': '/products/cakes/AnniversaryCakes_BlackForest.jpg',
    'Wedding Cakes': '/products/cakes/WeddingCakes_AlmondMarzipan.jpg',
    'Chocolate Cakes': '/products/cakes/ChocolateCakes_BlackForest2.jpg',
    'Designer Cakes': '/products/cakes/DesignerCakes_BerryMedley.jpg',
    'Photo Cakes': '/products/cakes/PhotoCakes_AlmondMarzipan.jpg',
    'Custom Cakes': '/products/cakes/CustomCakes_BerryMedley.jpg',
    'Eggless Cakes': '/products/cakes/EgglessCakes_BerryMedley.jpg',
}
