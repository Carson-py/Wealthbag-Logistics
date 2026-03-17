from django.urls import path
from . import views

urlpatterns = [
    path('', views.LaybyListCreateView.as_view(), name='layby-list-create'),
    path('<int:pk>/', views.LaybyDetailView.as_view(), name='layby-detail'),
    path('<int:pk>/payment/', views.LaybyPaymentView.as_view(), name='layby-payment'),
    path('<int:pk>/finalize/', views.LaybyFinalizeView.as_view(), name='layby-finalize'),
    path('<int:pk>/cancel/', views.LaybyCancelView.as_view(), name='layby-cancel'),
]
