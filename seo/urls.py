from django.urls import path

from . import views

urlpatterns = [
    path('robots.txt', views.robots_txt, name='seo-robots'),
    path('sitemap.xml', views.sitemap_xml, name='seo-sitemap'),
    path('api/seo/product/<int:pk>.json', views.product_schema, name='seo-product-schema'),
    path('api/seo/site.json', views.site_schema, name='seo-site-schema'),
]
