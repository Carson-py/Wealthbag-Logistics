from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal
import uuid
import random
import string
from django.utils import timezone
from datetime import date, timedelta
import calendar
from django.db.models import Avg
from organization.models import Branch, Warehouse



def current_year():
    from django.utils import timezone
    return timezone.now().year


class UserManager(BaseUserManager):
    """Custom user manager for creating and managing users."""
    
    def create_user(self, email, password=None, **extra_fields):
        """Create and save a regular user with the given email and password."""
        if not email:
            raise ValueError('Users must have an email address')
        
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Create and save a superuser with the given email and password."""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    """Custom user model for the performance management system."""
    
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('admin', 'Admin'),
        ('auditor', 'Auditor'),
        ('branch_manager', 'Branch Manager'),
        ('warehouse_manager', 'Warehouse Manager'),
        ('cashier', 'Cashier')

    ]

    ACCOUNT_STATUS = [
        ('active', 'Active'),
        ('blocked', 'Blocked')
    ]

    email = models.EmailField(unique=True, verbose_name='Email Address')
    first_login = models.BooleanField(default=True, verbose_name='First Login')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier', verbose_name='Role')
    is_active = models.BooleanField(default=True, verbose_name='Active')
    account_status = models.CharField(max_length=20, choices=ACCOUNT_STATUS, default='active')
    is_staff = models.BooleanField(default=False, verbose_name='Staff Status')
    date_joined = models.DateField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-date_joined']

    def __str__(self):
        return self.email

class Employee(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='profile', verbose_name='User')
    code = models.CharField(max_length=100, unique=True, verbose_name='Code')
    first_name = models.CharField(max_length = 100, null=True, blank=True)
    last_name = models.CharField(max_length = 100, null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True)



    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return self.get_full_name()


class AuditLog(models.Model):
    """
    Audit log model to track all system activities.
    """
    activity_type = models.CharField(max_length=100, db_index=True, verbose_name='Activity Type')
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        verbose_name='User'
    )
    description = models.TextField(verbose_name='Description')
    metadata = models.JSONField(null=True, blank=True, verbose_name='Metadata')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP Address')
    user_agent = models.TextField(null=True, blank=True, verbose_name='User Agent')
    related_model = models.CharField(max_length=255, null=True, blank=True, verbose_name='Related Model')
    related_object_id = models.CharField(max_length=255, null=True, blank=True, verbose_name='Related Object ID')
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Timestamp')

    class Meta:
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp', 'activity_type']),
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['related_model', 'related_object_id']),
        ]

    def __str__(self):
        user_str = self.user.email if self.user else 'System'
        return f"{self.activity_type} by {user_str} at {self.timestamp}"