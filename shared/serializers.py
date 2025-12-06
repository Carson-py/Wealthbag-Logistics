from rest_framework import serializers

from accounts.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            'id', 'activity_type', 'user', 'user_email', 'description',
            'metadata', 'ip_address', 'user_agent',
            'related_model', 'related_object_id', 'timestamp',
        ]
        read_only_fields = fields

