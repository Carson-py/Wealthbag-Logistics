from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/accounts/', include('accounts.urls')),
    path('api/organization/', include('organization.urls')),
    path('api/products/', include('products.urls')),
    path('api/stock/', include('stock.urls')),
    path('api/sales/', include('sales.urls')),
    path('api/shared/', include('shared.urls')),
    path('api/accounting/', include('accounting.urls')),
    path('api/analytics/', include('analytics.urls')),
    path('api/layby/', include('layby.urls')),
]

# Serve static and media files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
