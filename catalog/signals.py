"""Cache invalidation hooks for catalog performance caches."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Product


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
def _invalidate_catalog_on_product_change(sender, **kwargs):
    from .cache_utils import invalidate_catalog_caches
    invalidate_catalog_caches()
