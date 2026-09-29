from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'catalog'

    def ready(self):
        # Register cache invalidation signals (import side effects).
        from . import signals  # noqa: F401
