from rest_framework import serializers
from .models import StockEntry, StockAdjustment, Supplier, BranchStock, StockTransfer, StockTransferItem, StockEntryGroup
from products.serializers import ProductSerializer
from organization.serializers import WarehouseSerializer, BranchSerializer


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'name', 'email', 'phone', 'address', 'created_at']
        read_only_fields = ['id', 'created_at']

class StockEntrySerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    total_cost = serializers.ReadOnlyField()
    original_stock_entry_id = serializers.IntegerField(source='original_stock_entry.id', read_only=True)
    source_transfer_reference = serializers.CharField(source='source_transfer.reference_number', read_only=True)
    entry_group_reference = serializers.CharField(source='entry_group.reference_number', read_only=True)
    
    class Meta:
        model = StockEntry
        fields = [
            'id', 'product', 'product_name', 'product_sku', 'warehouse', 'warehouse_name',
            'supplier', 'supplier_name', 'quantity', 'reorder_level', 'purchase_price', 'total_cost',
            'batch_number', 'original_batch_number', 'received_date', 'notes',
            'is_initial_stock', 'source_transfer', 'source_transfer_reference',
            'original_stock_entry', 'original_stock_entry_id', 'entry_group', 'entry_group_reference',
            'created_by', 'created_by_email', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'total_cost', 'is_initial_stock', 'source_transfer', 'original_stock_entry', 'original_batch_number', 'entry_group']


class StockEntryGroupSerializer(serializers.ModelSerializer):
    """Serializer for stock entry groups"""
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    total_cost = serializers.ReadOnlyField()
    entry_count = serializers.SerializerMethodField()
    entries = StockEntrySerializer(many=True, read_only=True)
    
    class Meta:
        model = StockEntryGroup
        fields = [
            'id', 'warehouse', 'warehouse_name', 'reference_number', 'notes',
            'total_cost', 'entry_count', 'entries', 'created_by', 'created_by_email', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'total_cost', 'entry_count', 'entries', 'reference_number']
    
    def get_entry_count(self, obj):
        """Get the number of entries in this group"""
        if hasattr(obj, 'entries'):
            return obj.entries.count()
        return 0


class StockAdjustmentSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    
    class Meta:
        model = StockAdjustment
        fields = [
            'id', 'product', 'product_name', 'product_sku', 'warehouse', 'warehouse_name',
            'adjustment_type', 'quantity', 'purchase_price', 'reason', 'reference_number',
            'created_by', 'created_by_email', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class StockEntryItemSerializer(serializers.Serializer):
    """Serializer for a single item in a stock entry"""
    product_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    reorder_level = serializers.IntegerField(required=False, allow_null=True, help_text='Reorder level (optional)')
    supplier_id = serializers.IntegerField(required=False, allow_null=True, help_text='Supplier ID (optional)')
    batch_number = serializers.CharField(required=False, allow_blank=True, help_text='Batch number (auto-generated if not provided)')
    notes = serializers.CharField(required=False, allow_blank=True, help_text='Item-specific notes (optional)')


class AddStockSerializer(serializers.Serializer):
    # Single product fields (for backward compatibility)
    product_id = serializers.IntegerField(required=False, allow_null=True, help_text='Product ID (required if items not provided)')
    warehouse_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, help_text='Quantity (required if items not provided)')
    reorder_level = serializers.IntegerField(required=False, allow_null=True, help_text='Reorder level (optional)')
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, help_text='Purchase price (required if items not provided)')
    supplier_id = serializers.IntegerField(required=False, allow_null=True, help_text='Supplier ID (optional, for single product)')
    batch_number = serializers.CharField(required=False, allow_blank=True,
                                        help_text='Batch number (auto-generated if not provided, for single product)')
    notes = serializers.CharField(required=False, allow_blank=True, help_text='Notes (for single product)')
    
    # Multi-product support
    items = StockEntryItemSerializer(many=True, required=False, help_text='List of products to add (for multi-product entries)')
    reference_number = serializers.CharField(required=False, allow_blank=True, help_text='Reference number for the entry group (auto-generated if not provided)')
    group_notes = serializers.CharField(required=False, allow_blank=True, help_text='Notes for the entire entry group')
    
    def validate(self, data):
        """Validate that either items or single product is provided"""
        items = data.get('items', [])
        product_id = data.get('product_id')
        quantity = data.get('quantity')
        reorder_level = data.get('reorder_level')
        purchase_price = data.get('purchase_price')
        
        if items and len(items) > 0:
            # Multi-product entry
            if product_id or quantity is not None or reorder_level is not None or purchase_price is not None:
                raise serializers.ValidationError(
                    'Cannot provide both items and single product fields. Use items for multi-product entries.'
                )
        else:
            # Single product entry (backward compatible)
            if not product_id or quantity is None or purchase_price is None:
                raise serializers.ValidationError(
                    'Either provide items (for multi-product) or product_id, quantity, and purchase_price (for single product).'
                )
        
        return data


class BulkAddStockSerializer(serializers.Serializer):
    """Serializer for bulk stock entry creation"""
    stock_entries = AddStockSerializer(many=True, required=True,
                                       help_text='List of stock entries to add')
    
    def validate_stock_entries(self, value):
        """Validate that at least one stock entry is provided"""
        if not value or len(value) == 0:
            raise serializers.ValidationError('At least one stock entry must be provided.')
        return value


class RemoveStockSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    warehouse_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    adjustment_type = serializers.ChoiceField(
        choices=[('removal', 'Removal'), ('damaged', 'Damaged')],
        required=True,
        help_text='Specify if stock is being removed normally or written off as damaged.'
    )


class IncrementStockEntrySerializer(serializers.Serializer):
    stock_entry_id = serializers.IntegerField(required=True, help_text='ID of the stock entry to increment')
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True, help_text='Quantity to add to the stock entry')
    reason = serializers.CharField(required=False, allow_blank=True, help_text='Reason for the increment (optional)')


class BranchStockSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    warehouse_name = serializers.CharField(source='branch.warehouse.name', read_only=True, allow_null=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    total_cost = serializers.ReadOnlyField()
    original_stock_entry_id = serializers.IntegerField(source='original_stock_entry.id', read_only=True)
    original_branch_stock_id = serializers.IntegerField(source='original_branch_stock.id', read_only=True)
    source_transfer_reference = serializers.CharField(source='source_transfer.reference_number', read_only=True)
    
    class Meta:
        model = BranchStock
        fields = [
            'id', 'product', 'product_name', 'product_sku', 'branch', 'branch_name', 
            'warehouse_name', 'supplier', 'supplier_name', 'quantity', 'purchase_price', 
            'selling_price', 'total_cost', 'batch_number', 'original_batch_number',
            'received_date', 'notes', 'is_initial_stock', 'source_transfer', 'source_transfer_reference',
            'original_stock_entry', 'original_stock_entry_id', 'original_branch_stock', 'original_branch_stock_id',
            'created_by', 'created_by_email', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'total_cost', 'is_initial_stock', 'source_transfer', 'original_stock_entry', 'original_branch_stock', 'original_batch_number']


class StockTransferItemSerializer(serializers.ModelSerializer):
    """Serializer for individual items within a stock transfer"""
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    total_cost = serializers.ReadOnlyField()
    
    class Meta:
        model = StockTransferItem
        fields = [
            'id', 'transfer', 'product', 'product_name', 'product_sku', 'supplier', 
            'supplier_name', 'quantity', 'purchase_price', 'reorder_level', 'selling_price', 'total_cost', 'batch_number', 
            'notes', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'total_cost']


class LowStockSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    product_sku = serializers.CharField()
    warehouse_id = serializers.IntegerField()
    warehouse_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    reorder_level = serializers.DecimalField(max_digits=12, decimal_places=2)


class BranchLowStockSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    product_sku = serializers.CharField()
    branch_id = serializers.IntegerField()
    branch_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    reorder_level = serializers.DecimalField(max_digits=12, decimal_places=2)


class StockTransferSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True, allow_null=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True, allow_null=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, allow_null=True)
    source_warehouse_name = serializers.CharField(source='source_warehouse.name', read_only=True)
    source_branch_name = serializers.CharField(source='source_branch.name', read_only=True)
    destination_warehouse_name = serializers.CharField(source='destination_warehouse.name', read_only=True)
    destination_branch_name = serializers.CharField(source='destination_branch.name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    completed_by_email = serializers.CharField(source='completed_by.email', read_only=True)
    total_cost = serializers.ReadOnlyField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    transfer_type_display = serializers.CharField(source='get_transfer_type_display', read_only=True)
    items = StockTransferItemSerializer(many=True, read_only=True, help_text='Transfer items (for multi-product transfers)')
    item_count = serializers.SerializerMethodField()
    
    class Meta:
        model = StockTransfer
        fields = [
            'id', 'transfer_type', 'transfer_type_display', 'product', 'product_name', 
            'product_sku', 'supplier', 'supplier_name', 'quantity', 'purchase_price', 
            'selling_price', 'reorder_level', 'total_cost', 'batch_number', 'source_warehouse', 'source_warehouse_name', 
            'source_branch', 'source_branch_name', 'destination_warehouse', 
            'destination_warehouse_name', 'destination_branch', 'destination_branch_name', 
            'status', 'status_display', 'reference_number', 'notes', 'items', 'item_count',
            'created_by', 'created_by_email', 'completed_by', 'completed_by_email', 
            'created_at', 'completed_at'
        ]
        read_only_fields = ['id', 'created_at', 'completed_at', 'total_cost', 'status_display', 'transfer_type_display', 'items', 'item_count']
    
    def get_item_count(self, obj):
        """Get the number of items in this transfer"""
        if hasattr(obj, 'items'):
            return obj.items.count()
        return 1 if obj.product else 0


class AddBranchStockSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    branch_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    selling_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    supplier_id = serializers.IntegerField(required=False, allow_null=True)
    batch_number = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class RemoveBranchStockSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    branch_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    reason = serializers.CharField(required=False, allow_blank=True)


class TransferItemSerializer(serializers.Serializer):
    """Serializer for a single item in a transfer"""
    product_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    reorder_level = serializers.IntegerField(required=False, allow_null=True, help_text='Reorder level (optional)')
    selling_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, help_text='Selling price (required when transferring to a branch)')
    supplier_id = serializers.IntegerField(required=False, allow_null=True, help_text='Supplier ID (optional)')
    batch_number = serializers.CharField(required=False, allow_blank=True, help_text='Batch number (optional)')
    notes = serializers.CharField(required=False, allow_blank=True, help_text='Item-specific notes (optional)')


class CreateStockTransferSerializer(serializers.Serializer):
    transfer_type = serializers.ChoiceField(choices=StockTransfer.TRANSFER_TYPES, required=True)
    
    # Single product fields (for backward compatibility)
    product_id = serializers.IntegerField(required=False, allow_null=True, help_text='Product ID (required if items not provided)')
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, help_text='Quantity (required if items not provided)')
    reorder_level = serializers.IntegerField(required=False, allow_null=True, help_text='Reorder level (optional, for single product)')
    selling_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, help_text='Selling price (required when transferring to a branch, for single product)')
    supplier_id = serializers.IntegerField(required=False, allow_null=True, help_text='Supplier ID (optional, for single product)')
    batch_number = serializers.CharField(required=False, allow_blank=True, help_text='Batch number (optional, for single product)')
    
    # Multi-product support
    items = TransferItemSerializer(many=True, required=False, help_text='List of products to transfer (for multi-product transfers)')
    
    # Source fields (one required based on transfer_type)
    source_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    source_branch_id = serializers.IntegerField(required=False, allow_null=True)
    
    # Destination fields (one required based on transfer_type)
    destination_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    destination_branch_id = serializers.IntegerField(required=False, allow_null=True)
    
    reference_number = serializers.CharField(required=False, allow_blank=True,
                                            help_text='Reference number (auto-generated if not provided)')
    notes = serializers.CharField(required=False, allow_blank=True, help_text='Transfer notes')
    
    def validate(self, data):
        """Validate that source and destination match the transfer type, and either items or single product is provided"""
        transfer_type = data.get('transfer_type')
        source_warehouse_id = data.get('source_warehouse_id')
        source_branch_id = data.get('source_branch_id')
        destination_warehouse_id = data.get('destination_warehouse_id')
        destination_branch_id = data.get('destination_branch_id')
        items = data.get('items', [])
        product_id = data.get('product_id')
        quantity = data.get('quantity')
        reorder_level = data.get('reorder_level')
        selling_price = data.get('selling_price')
        
        # Validate source/destination based on transfer type
        if transfer_type == 'warehouse_to_warehouse':
            if not source_warehouse_id or not destination_warehouse_id:
                raise serializers.ValidationError(
                    'Both source_warehouse_id and destination_warehouse_id are required for warehouse to warehouse transfers.'
                )
        elif transfer_type == 'warehouse_to_branch':
            if not source_warehouse_id or not destination_branch_id:
                raise serializers.ValidationError(
                    'source_warehouse_id and destination_branch_id are required for warehouse to branch transfers.'
                )
        elif transfer_type == 'branch_to_branch':
            if not source_branch_id or not destination_branch_id:
                raise serializers.ValidationError(
                    'Both source_branch_id and destination_branch_id are required for branch to branch transfers.'
                )
        elif transfer_type == 'branch_to_warehouse':
            if not source_branch_id or not destination_warehouse_id:
                raise serializers.ValidationError(
                    'source_branch_id and destination_warehouse_id are required for branch to warehouse transfers.'
                )
        
        # Validate that either items or single product is provided
        if items and len(items) > 0:
            # Multi-product transfer
            if product_id or quantity is not None or reorder_level is not None or selling_price is not None:
                raise serializers.ValidationError(
                    'Cannot provide both items and single product fields. Use items for multi-product transfers.'
                )
            
            if transfer_type in ['warehouse_to_branch', 'branch_to_branch']:
                missing_indices = [
                    index for index, item in enumerate(items)
                    if item.get('selling_price') is None
                ]
                if missing_indices:
                    raise serializers.ValidationError(
                        f'Selling price is required for items transferring to a branch (missing for item indices: {missing_indices}).'
                    )
        else:
            # Single product transfer (backward compatibility)
            if not product_id or quantity is None:
                raise serializers.ValidationError(
                    'Either provide items (for multi-product) or product_id and quantity (for single product).'
                )
            if transfer_type in ['warehouse_to_branch', 'branch_to_branch'] and selling_price is None:
                raise serializers.ValidationError('Selling price is required when transferring stock to a branch.')
        
        return data


class BulkCreateStockTransferSerializer(serializers.Serializer):
    """Serializer for bulk stock transfer creation"""
    transfers = CreateStockTransferSerializer(many=True, required=True,
                                            help_text='List of stock transfers to create')
    
    def validate_transfers(self, value):
        """Validate that at least one transfer is provided"""
        if not value or len(value) == 0:
            raise serializers.ValidationError('At least one transfer must be provided.')
        return value

