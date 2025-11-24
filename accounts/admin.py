from django.contrib import admin
from .models import (
    User, Employee, AuditLog
)

# Register your models here.
my_models = [User, Employee]

admin.site.register(my_models)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['activity_type', 'user', 'description', 'timestamp', 'ip_address']
    list_filter = ['activity_type', 'timestamp', 'related_model']
    search_fields = ['activity_type', 'description', 'user__email', 'ip_address']
    readonly_fields = ['timestamp', 'activity_type', 'user', 'description', 'metadata', 'ip_address', 'user_agent', 'related_model', 'related_object_id']
    date_hierarchy = 'timestamp'
    
    def has_add_permission(self, request):
        # Prevent manual creation of audit logs
        return False
    
    def has_change_permission(self, request, obj=None):
        # Make audit logs read-only
        return False