from django.urls import path
from . import views

urlpatterns = [
    # Categories
    path('categories/', views.CategoryListView.as_view(), name='category-list'),
    
    # Units
    path('units/', views.UnitListView.as_view(), name='unit-list'),
    
    # Products
    path('products/', views.ProductListView.as_view(), name='product-list'),
    path('products/bulk/', views.BulkCreateProductView.as_view(), name='product-bulk-create'),
    path('products/<int:pk>/', views.ProductDetailView.as_view(), name='product-detail'),
    
    # Barcode lookup (for React app scanning)
    path('lookup/', views.BarcodeLookupView.as_view(), name='barcode-lookup'),
    path('barcodes/', views.BarcodeListView.as_view(), name='barcodes')
]
