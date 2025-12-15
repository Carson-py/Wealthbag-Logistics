from django.urls import path
from . import views

urlpatterns = [
    # Supplier endpoints
    path('suppliers/', views.ListCreateSupplierView.as_view(), name='supplier-list-create'),
    path('suppliers/<int:pk>/', views.SupplierDetailView.as_view(), name='supplier-detail'),
    
    # Warehouse stock endpoints
    path('entries/', views.StockEntryListView.as_view(), name='stock-entry-list'),
    path('entries/<int:pk>/', views.StockEntryDetailView.as_view(), name='stock-entry-detail'),
    path('entries/bulk/', views.BulkAddStockView.as_view(), name='stock-entry-bulk-add'),
    path('entries/import-excel/', views.ImportStockFromExcelView.as_view(), name='stock-entry-import-excel'),
    path('entries/increment/', views.IncrementStockEntryView.as_view(), name='stock-entry-increment'),
    path('products/import-excel/', views.ImportProductsFromExcelView.as_view(), name='products-import-excel'),
    path('adjustments/', views.StockAdjustmentListView.as_view(), name='stock-adjustment-list'),
    path('summary/', views.StockSummaryView.as_view(), name='stock-summary'),
    path('value/', views.StockValueView.as_view(), name='stock-value'),
    path('low-stock/', views.LowStockView.as_view(), name='stock-low-stock'),
    path('branch-low-stock/', views.BranchLowStockView.as_view(), name='branch-low-stock'),
    
    # Branch stock endpoints
    path('branch-stock/', views.BranchStockListView.as_view(), name='branch-stock-list'),
    path('branch-stock/remove/', views.RemoveBranchStockView.as_view(), name='branch-stock-remove'),
    path('branch-stock/summary/', views.BranchStockSummaryView.as_view(), name='branch-stock-summary'),
    path('branch-stock/value/', views.BranchStockValueView.as_view(), name='branch-stock-value'),
    
    # Stock transfer endpoints
    path('transfers/', views.StockTransferListView.as_view(), name='stock-transfer-list'),
    path('transfers/bulk/', views.BulkCreateStockTransferView.as_view(), name='stock-transfer-bulk-create'),
    path('transfers/import-excel/', views.ImportStockTransfersFromExcelView.as_view(), name='stock-transfer-import-excel'),
    path('transfers/<int:pk>/', views.StockTransferDetailView.as_view(), name='stock-transfer-detail'),
    path('transfers/<int:pk>/complete/', views.CompleteStockTransferView.as_view(), name='stock-transfer-complete'),
    path('transfers/<int:pk>/cancel/', views.CancelStockTransferView.as_view(), name='stock-transfer-cancel'),
]

