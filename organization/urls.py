from django.urls import path
from . import views

urlpatterns = [
    path('branches/', views.ListCreateBranchView.as_view(), name='list-create-branches'),
    path('branches/<int:pk>/', views.BranchDetailView.as_view(), name='branch-detail'),
    path('warehouses/', views.ListCreateWarehouseView.as_view(), name='list-create-warehouses'),
    path('warehouses/<int:pk>/', views.WarehouseDetailView.as_view(), name='warehouse-detail'),
]