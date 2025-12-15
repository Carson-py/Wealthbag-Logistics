from django.urls import path

from . import views

urlpatterns = [
    path('bulk/', views.BulkSaleCreateView.as_view(), name='sale-bulk-create'),
    path('scan-barcode/', views.SaleBarcodeLookupView.as_view(), name='sale-barcode-lookup'),
    path('history/', views.SalesHistoryView.as_view(), name='sales-history'),
    path('', views.SaleListCreateView.as_view(), name='sale-list-create'),
    path('<int:pk>/', views.SaleDetailView.as_view(), name='sale-detail'),
    path('<int:pk>/items/', views.SaleAddItemView.as_view(), name='sale-add-items'),
    path('<int:pk>/complete/', views.SaleCompleteView.as_view(), name='sale-complete'),
    path('<int:pk>/cancel/', views.SaleCancelView.as_view(), name='sale-cancel'),
    path('<int:pk>/returns/', views.SaleReturnView.as_view(), name='sale-return'),
    path('<int:pk>/apply-discount/', views.SaleApplyDiscountView.as_view(), name='sale-apply-discount'),
    path('returns-rate/', views.ReturnRateView.as_view(), name='return-rate'),
    path('returns-code/', views.ReturnAuthorizationCodeView.as_view(), name='return-authorization-code'),
    
    # Discount management
    path('discounts/', views.DiscountListView.as_view(), name='discount-list'),
    path('discounts/available/', views.GetAvailableDiscountsForSaleView.as_view(), name='available-discounts-for-sale'),
    path('discounts/validate/', views.ValidateDiscountCodeView.as_view(), name='validate-discount-code'),
    path('discounts/<int:pk>/', views.DiscountDetailView.as_view(), name='discount-detail'),
    
    # Cash received and variance
    path('cash-received/', views.CashReceivedListCreateView.as_view(), name='cash-received-list-create'),
    path('cash-received/<int:pk>/', views.CashReceivedListCreateView.as_view(), name='cash-received-detail'),
    path('cash-variance/', views.CashVarianceView.as_view(), name='cash-variance'),

    # Exchange rate
    path('exchange-rate/', views.ExchangeRateView.as_view(), name='exchange-rate'),
]
