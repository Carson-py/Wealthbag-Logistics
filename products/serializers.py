from rest_framework import serializers
from decimal import Decimal
from django.db.models import Sum
from .models import (
    Product, Unit, Category
)
from organization.serializers import BranchSerializer, WarehouseSerializer
from stock.services import get_average_purchase_price


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']
        read_only_fields = ['id']


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ['id', 'name', 'abbreviation']
        read_only_fields = ['id']

class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    
    class Meta:
        model = Product
        fields = [
            'id', 'sku', 'name', 'category', 'category_name', 'unit', 'unit_name',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CreateProductSerializer(serializers.ModelSerializer):
    initial_warehouse_id = serializers.IntegerField(write_only=True, required=False, allow_null=True,
                                                   help_text='Warehouse ID for initial stock. If not provided, uses the main warehouse.')
    initial_quantity = serializers.DecimalField(max_digits=12, decimal_places=2, write_only=True, required=True)
    initial_purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, write_only=True, required=False, 
                                                     help_text='Purchase price for initial stock. If not provided, uses product purchase_price.')
    batch_number = serializers.CharField(write_only=True, required=False, allow_blank=True,
                                        help_text='Batch/Lot number for initial stock (auto-generated if not provided)')
    supplier_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    
    
    class Meta:
        model = Product
        fields = [
            'sku', 'name', 'category', 'unit', 'initial_warehouse_id', 'initial_quantity',
            'initial_purchase_price', 'batch_number', 'supplier_id'
        ]


class BulkCreateProductSerializer(serializers.Serializer):
    """Serializer for bulk product creation"""
    products = CreateProductSerializer(many=True, required=True, 
                                      help_text='List of products to create with their initial stock')
    
    def validate_products(self, value):
        """Validate that at least one product is provided"""
        if not value or len(value) == 0:
            raise serializers.ValidationError('At least one product must be provided.')
        return value

