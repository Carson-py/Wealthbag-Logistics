from rest_framework import serializers
from decimal import Decimal
from django.db.models import Sum
from .models import (
    Product, Unit, Category, Barcode
)
from organization.serializers import BranchSerializer, WarehouseSerializer
from stock.services import get_average_purchase_price
from rest_framework import serializers as drf_serializers


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
    image_url = serializers.SerializerMethodField()
    
    class Meta:
        model = Product
        fields = [
            'id', 'sku', 'name', 'description', 'image', 'image_url', 'category', 'category_name', 'unit', 'unit_name',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_sku(self, value):
        """Validate SKU uniqueness, excluding current instance if updating"""
        # Get the instance if it exists (for updates)
        instance = self.instance
        
        # Check if SKU already exists, excluding current instance
        queryset = Product.objects.filter(sku=value)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        
        if queryset.exists():
            raise serializers.ValidationError("Product with this SKU already exists.")
        
        return value
    
    def get_image_url(self, obj):
        """Return the full URL of the product image"""
        if obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None


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
            'sku', 'name', 'image', 'category', 'unit', 'initial_warehouse_id', 'initial_quantity',
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


class ImportProductsFromStockSheetSerializer(serializers.Serializer):
    """
    Upload an Excel file (.xlsx) to create products (and suppliers if missing).
    Required columns (case-insensitive):
        - product name
        - description
        - selling prices
        - cost per unit
        - total quantity
        - supplier name
        - supplier email address
    """
    file = drf_serializers.FileField(required=True, help_text='Excel file (.xlsx)')
    notes = drf_serializers.CharField(required=False, allow_blank=True, help_text='Optional notes for traceability')


class BarcodeSerializer(serializers.ModelSerializer):
    """Barcode with basic product details and stock price information for listing/printing on frontend."""
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_id = serializers.IntegerField(source='product.id', read_only=True)
    purchase_price = serializers.SerializerMethodField()
    selling_price = serializers.SerializerMethodField()
    stock_location = serializers.SerializerMethodField()
    
    class Meta:
        model = Barcode
        fields = [
            'id',
            'barcode',
            'barcode_image',
            'is_primary',
            'notes',
            'created_at',
            'updated_at',
            'product',
            'product_id',
            'product_sku',
            'product_name',
            'purchase_price',
            'selling_price',
            'stock_location',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'purchase_price', 'selling_price', 'stock_location']
    
    def get_purchase_price(self, obj):
        """Get the latest purchase price from stock entries."""
        # Use prefetched data if available
        if hasattr(obj.product, 'recent_warehouse_stock') and obj.product.recent_warehouse_stock:
            return obj.product.recent_warehouse_stock[0].purchase_price
        
        if hasattr(obj.product, 'recent_branch_stock') and obj.product.recent_branch_stock:
            return obj.product.recent_branch_stock[0].purchase_price
        
        # Fallback to query if prefetch not available
        from stock.models import StockEntry, BranchStock
        
        warehouse_stock = StockEntry.objects.filter(
            product=obj.product,
            quantity__gt=0
        ).order_by('-received_date', '-created_at').first()
        
        if warehouse_stock:
            return warehouse_stock.purchase_price
        
        branch_stock = BranchStock.objects.filter(
            product=obj.product,
            quantity__gt=0
        ).order_by('-received_date', '-created_at').first()
        
        if branch_stock:
            return branch_stock.purchase_price
        
        return None
    
    def get_selling_price(self, obj):
        """Get the latest selling price from stock entries."""
        # Use prefetched data if available
        if hasattr(obj.product, 'recent_warehouse_stock') and obj.product.recent_warehouse_stock:
            stock = obj.product.recent_warehouse_stock[0]
            if stock.selling_price:
                return stock.selling_price
        
        if hasattr(obj.product, 'recent_branch_stock') and obj.product.recent_branch_stock:
            stock = obj.product.recent_branch_stock[0]
            if stock.selling_price:
                return stock.selling_price
        
        # Fallback to query if prefetch not available
        from stock.models import StockEntry, BranchStock
        
        warehouse_stock = StockEntry.objects.filter(
            product=obj.product,
            quantity__gt=0
        ).order_by('-received_date', '-created_at').first()
        
        if warehouse_stock and warehouse_stock.selling_price:
            return warehouse_stock.selling_price
        
        branch_stock = BranchStock.objects.filter(
            product=obj.product,
            quantity__gt=0
        ).order_by('-received_date', '-created_at').first()
        
        if branch_stock and branch_stock.selling_price:
            return branch_stock.selling_price
        
        return None
    
    def get_stock_location(self, obj):
        """Get the location (warehouse or branch) where stock is available."""
        # Use prefetched data if available
        if hasattr(obj.product, 'recent_warehouse_stock') and obj.product.recent_warehouse_stock:
            stock = obj.product.recent_warehouse_stock[0]
            return {
                'type': 'warehouse',
                'id': stock.warehouse.id,
                'name': stock.warehouse.name,
            }
        
        if hasattr(obj.product, 'recent_branch_stock') and obj.product.recent_branch_stock:
            stock = obj.product.recent_branch_stock[0]
            return {
                'type': 'branch',
                'id': stock.branch.id,
                'name': stock.branch.name,
            }
        
        # Fallback to query if prefetch not available
        from stock.models import StockEntry, BranchStock
        
        warehouse_stock = StockEntry.objects.filter(
            product=obj.product,
            quantity__gt=0
        ).select_related('warehouse').order_by('-received_date', '-created_at').first()
        
        if warehouse_stock:
            return {
                'type': 'warehouse',
                'id': warehouse_stock.warehouse.id,
                'name': warehouse_stock.warehouse.name,
            }
        
        branch_stock = BranchStock.objects.filter(
            product=obj.product,
            quantity__gt=0
        ).select_related('branch').order_by('-received_date', '-created_at').first()
        
        if branch_stock:
            return {
                'type': 'branch',
                'id': branch_stock.branch.id,
                'name': branch_stock.branch.name,
            }
        
        return None

