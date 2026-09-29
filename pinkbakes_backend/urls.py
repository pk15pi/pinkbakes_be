"""
URL configuration for pinkbakes_backend project.
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path


from accounts.urls import address_urlpatterns
from notifications.urls import account_urlpatterns as notification_account_urlpatterns, admin_urlpatterns as notification_admin_urlpatterns

urlpatterns = [
    path('', include('seo.urls')),
    path("admin/", admin.site.urls),
    path("api/accounts/", include("accounts.urls")),
    path("api/accounts/", include(notification_account_urlpatterns)),
    path("api/", include(notification_admin_urlpatterns)),
    path("api/", include(address_urlpatterns)),
    path("api/", include("catalog.urls")),
    path("api/catalog/", include("catalog.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

