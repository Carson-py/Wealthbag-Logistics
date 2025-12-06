from django.urls import path

from .views import (
    AdminDashboard,
    ReportView,
    BranchManagerDashboard,
    BranchReportView,
    WarehouseManagerDashboard,
    WarehouseReportView,
    AuditorDashboard,
    AuditorReportView,
    CashierPerformanceView,
    ProductPerformanceView,
)

urlpatterns = [
    # Admin
    path('dashboard/', AdminDashboard.as_view(), name='admin-dashboard'),
    path('reports/', ReportView.as_view(), name='analytics-reports'),
    
    # Branch Manager
    path('branch-dashboard/', BranchManagerDashboard.as_view(), name='branch-manager-dashboard'),
    path('branch-reports/', BranchReportView.as_view(), name='branch-reports'),

    # Cashier Performance
    path('cashier-performance/', CashierPerformanceView.as_view(), name='cashier-performance'),

    # Warehouse Manager
    path('warehouse-dashboard/', WarehouseManagerDashboard.as_view(), name='warehouse-manager-dashboard'),
    path('warehouse-reports/', WarehouseReportView.as_view(), name='warehouse-reports'),

    # Auditor
    path('auditor-dashboard/', AuditorDashboard.as_view(), name='auditor-dashboard'),
    path('auditor-reports/', AuditorReportView.as_view(), name='auditor-reports'),

    # Product Performance
    path('product-performance/', ProductPerformanceView.as_view(), name='product-performance'),

]

