from rest_framework import serializers
from . models import (
    Branch, Warehouse
)


class WarehouseSerializer(serializers.ModelSerializer):
    is_main = serializers.BooleanField(required=False, help_text='Mark as main warehouse (only one can be main)')
    
    class Meta:
        model = Warehouse
        fields = ['id', 'name', 'is_main', 'location', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

class BranchSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)

    class Meta:
        model = Branch
        fields = ['id', 'name', 'warehouse', 'warehouse_name', 'address']
        read_only_fields = ['id']