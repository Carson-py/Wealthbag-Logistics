"""
Audit logging utilities for tracking all system activities.

Usage Examples:

1. Basic logging:
    from shared.audit import log_activity, ActivityType
    
    log_activity(
        activity_type=ActivityType.USER_CREATED,
        user=request.user,
        description="Created new user account",
        request=request,
        related_object=user_instance,
    )

2. Logging with metadata:
    log_activity(
        activity_type=ActivityType.CUSTOM,
        user=request.user,
        description="Custom activity",
        metadata={'key': 'value', 'amount': 100},
        request=request,
    )

3. Using decorator:
    from shared.audit import audit_view, ActivityType
    
    @audit_view(ActivityType.VIEW_ACCESSED, "User accessed dashboard")
    def dashboard_view(request):
        ...

4. Logging model changes:
    from shared.audit import audit_model_change
    
    audit_model_change(
        instance=user_instance,
        action='updated',
        user=request.user,
        request=request,
        changes={'email': {'old': 'old@email.com', 'new': 'new@email.com'}},
    )

5. Enable automatic API logging (add to settings.py MIDDLEWARE):
    MIDDLEWARE = [
        ...
        'shared.audit.AuditMiddleware',
        ...
    ]
"""
import json
from typing import Optional, Dict, Any
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import models
from django.http import HttpRequest
from functools import wraps
from django.core.exceptions import PermissionDenied

User = get_user_model()

# Import the AuditLog model - will be created in accounts/models.py
try:
    from accounts.models import AuditLog
except ImportError:
    AuditLog = None


class ActivityType:
    """Constants for activity types."""
    # Authentication
    LOGIN = 'login'
    LOGOUT = 'logout'
    LOGIN_FAILED = 'login_failed'
    
    # User Management
    USER_CREATED = 'user_created'
    USER_UPDATED = 'user_updated'
    USER_DELETED = 'user_deleted'
    USER_BLOCKED = 'user_blocked'
    USER_UNBLOCKED = 'user_unblocked'
    PASSWORD_CHANGED = 'password_changed'
    PASSWORD_RESET = 'password_reset'
    
    # Organization
    BRANCH_CREATED = 'branch_created'
    BRANCH_UPDATED = 'branch_updated'
    BRANCH_DELETED = 'branch_deleted'
    WAREHOUSE_CREATED = 'warehouse_created'
    WAREHOUSE_UPDATED = 'warehouse_updated'
    WAREHOUSE_DELETED = 'warehouse_deleted'
    
    # Products
    PRODUCT_CREATED = 'product_created'
    PRODUCT_UPDATED = 'product_updated'
    PRODUCT_DELETED = 'product_deleted'
    
    # Stock
    STOCK_ADDED = 'stock_added'
    STOCK_UPDATED = 'stock_updated'
    STOCK_REMOVED = 'stock_removed'
    STOCK_ADJUSTED = 'stock_adjusted'
    
    # Sales
    SALE_CREATED = 'sale_created'
    SALE_UPDATED = 'sale_updated'
    SALE_CANCELLED = 'sale_cancelled'
    
    # Accounting
    TRANSACTION_CREATED = 'transaction_created'
    TRANSACTION_UPDATED = 'transaction_updated'
    TRANSACTION_DELETED = 'transaction_deleted'
    
    # General
    VIEW_ACCESSED = 'view_accessed'
    DATA_EXPORTED = 'data_exported'
    DATA_IMPORTED = 'data_imported'
    SETTINGS_CHANGED = 'settings_changed'
    CUSTOM = 'custom'


def log_activity(
    activity_type: str,
    user: Optional[User] = None,
    description: str = '',
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request: Optional[HttpRequest] = None,
    related_object: Optional[models.Model] = None,
) -> Optional['AuditLog']:
    """
    Log an activity to the audit log.
    
    Args:
        activity_type: Type of activity (use ActivityType constants)
        user: User who performed the activity (None for system actions)
        description: Human-readable description of the activity
        metadata: Additional data as a dictionary (will be JSON serialized)
        ip_address: IP address of the user
        user_agent: User agent string
        request: Django HttpRequest object (will extract IP and user agent)
        related_object: Related model instance (will extract model info)
    
    Returns:
        AuditLog instance if model exists, None otherwise
    """
    if AuditLog is None:
        # Model not available, skip logging
        return None
    
    # Extract info from request if provided
    if request:
        if not ip_address:
            ip_address = get_client_ip(request)
        if not user_agent:
            user_agent = request.META.get('HTTP_USER_AGENT', '')
        if not user and hasattr(request, 'user'):
            user = request.user if request.user.is_authenticated else None
    
    # Extract related object info
    related_model = None
    related_object_id = None
    if related_object:
        related_model = f"{related_object._meta.app_label}.{related_object._meta.model_name}"
        related_object_id = str(related_object.pk)
    
    # Serialize metadata
    metadata_json = None
    if metadata:
        try:
            metadata_json = json.dumps(metadata, default=str)
        except (TypeError, ValueError):
            metadata_json = json.dumps({'error': 'Failed to serialize metadata'})
    
    try:
        audit_log = AuditLog.objects.create(
            activity_type=activity_type,
            user=user,
            description=description or activity_type.replace('_', ' ').title(),
            metadata=metadata_json,
            ip_address=ip_address,
            user_agent=user_agent,
            related_model=related_model,
            related_object_id=related_object_id,
            timestamp=timezone.now()
        )
        return audit_log
    except Exception as e:
        # Log error but don't break the application
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to create audit log: {e}")
        return None


def get_audit_logs(user: Optional[User], **filters):
    """
    Retrieve audit logs with optional filters. Only admins may view logs.
    """
    if user is None or getattr(user, 'role', None) not in ['admin', 'auditor']:
        raise PermissionDenied('You are not authorized to view audit logs.')

    queryset = AuditLog.objects.all()
    if filters:
        queryset = queryset.filter(**filters)
    return queryset.order_by('-timestamp')


def get_client_ip(request: HttpRequest) -> str:
    """
    Get the client IP address from the request.
    Handles proxy headers like X-Forwarded-For.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '')
    return ip


def audit_view(activity_type: str = None, description: str = None):
    """
    Decorator to automatically log view access.
    
    Usage:
        @audit_view(ActivityType.VIEW_ACCESSED, "User accessed dashboard")
        def my_view(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Log before view execution
            log_activity(
                activity_type=activity_type or ActivityType.VIEW_ACCESSED,
                description=description or f"Accessed {view_func.__name__}",
                request=request,
            )
            
            # Execute the view
            response = view_func(request, *args, **kwargs)
            
            return response
        return wrapper
    return decorator


def audit_model_change(
    instance: models.Model,
    action: str,
    user: Optional[User] = None,
    request: Optional[HttpRequest] = None,
    changes: Optional[Dict[str, Any]] = None,
):
    """
    Log changes to a model instance.
    
    Args:
        instance: The model instance that was changed
        action: 'created', 'updated', or 'deleted'
        user: User who made the change
        request: Django HttpRequest object
        changes: Dictionary of field changes (for updates)
    """
    model_name = f"{instance._meta.app_label}.{instance._meta.model_name}"
    activity_type_map = {
        'created': f"{model_name.split('.')[-1]}_created",
        'updated': f"{model_name.split('.')[-1]}_updated",
        'deleted': f"{model_name.split('.')[-1]}_deleted",
    }
    
    activity_type = activity_type_map.get(action, ActivityType.CUSTOM)
    description = f"{action.title()} {model_name}"
    
    metadata = {
        'model': model_name,
        'object_id': str(instance.pk),
        'action': action,
    }
    
    if changes:
        metadata['changes'] = changes
    
    log_activity(
        activity_type=activity_type,
        user=user,
        description=description,
        metadata=metadata,
        request=request,
        related_object=instance,
    )


class AuditMiddleware:
    """
    Middleware to automatically log API requests.
    Add to MIDDLEWARE in settings.py
    """
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Skip logging for certain paths
        skip_paths = ['/admin/jsi18n/', '/static/', '/media/', '/api/docs/']
        if any(request.path.startswith(path) for path in skip_paths):
            return self.get_response(request)
        
        # Only log authenticated API requests
        if request.path.startswith('/api/') and hasattr(request, 'user') and request.user.is_authenticated:
            method = request.method
            if method in ['POST', 'PUT', 'PATCH', 'DELETE']:
                # Extract request data (be careful with large payloads)
                try:
                    if hasattr(request, 'data'):
                        request_data = dict(request.data)
                    elif hasattr(request, 'body'):
                        body = request.body.decode('utf-8')[:500]  # Limit size
                        request_data = {'body_preview': body}
                    else:
                        request_data = {}
                except Exception:
                    request_data = {}
                
                log_activity(
                    activity_type=ActivityType.CUSTOM,
                    description=f"{method} {request.path}",
                    metadata={
                        'method': method,
                        'path': request.path,
                        'data': request_data,
                    },
                    request=request,
                )
        
        response = self.get_response(request)
        return response

