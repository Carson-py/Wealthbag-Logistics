from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('create-user/', views.CreateUserView.as_view(), name='create-user'),
    path('users/', views.UserListView.as_view()),
    path('user/<str:pk>/', views.UserDetailView.as_view()),
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('users/update-status/', views.BlockUnblockAccountView.as_view(), name='block-unblock-user'),
    path('change-password/', views.ChangePasswordView.as_view(), name='change-password'),
    path('reset-password/', views.ResetPasswordView.as_view(), name='reset-password'),
]