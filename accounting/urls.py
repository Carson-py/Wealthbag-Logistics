from django.urls import path

from . import views

urlpatterns = [
    path('expense-categories/', views.ExpenseCategoryListCreateView.as_view(), name='expense-category-list-create'),
    path('expenses/', views.ExpenseListCreateView.as_view(), name='expense-list-create'),
    path('expenses/<int:pk>/', views.ExpenseDetailView.as_view(), name='expense-detail'),
    path('profit-loss/', views.ProfitLossReportView.as_view(), name='profit-loss-report'),
    path('sales-report/', views.SalesReportView.as_view(), name='sales-report'),
]

