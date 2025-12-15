from decimal import Decimal
from django.utils import timezone
from rest_framework import serializers

from .models import ExchangeRate, Sale, SaleItem, ProductReturn, Discount, ReturnAuthorizationCode, CashReceived


class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    profit = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    profit_margin = serializers.DecimalField(max_digits=7, decimal_places=2, read_only=True)

    class Meta:
        model = SaleItem
        fields = [
            'id', 'product', 'product_name', 'product_sku',
            'quantity', 'unit_price', 'purchase_price',
            'discount', 'subtotal', 'profit', 'profit_margin',
        ]
        read_only_fields = ['id', 'subtotal', 'profit', 'profit_margin']


class ProductReturnSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    processed_by_email = serializers.CharField(source='processed_by.email', read_only=True)

    class Meta:
        model = ProductReturn
        fields = [
            'id', 'sale', 'product', 'product_name', 'product_sku',
            'quantity', 'reason', 'refund_amount',
            'processed_by', 'processed_by_email', 'created_at',
        ]
        read_only_fields = ['id', 'sale', 'processed_by_email', 'created_at']


class SaleSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    cashier_email = serializers.CharField(source='cashier.email', read_only=True)
    items = SaleItemSerializer(many=True, read_only=True)
    returns = ProductReturnSerializer(many=True, read_only=True)
    net_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    discount_info = serializers.SerializerMethodField()
    receipt_calculation = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = [
            'id', 'sync_id', 'sale_number', 'branch', 'branch_name', 'cashier', 'cashier_email',
            'total_amount', 'discount', 'tax', 'net_amount','type_of_payment',
            'status', 'notes', 'items', 'returns', 'discount_info', 'receipt_calculation',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'sale_number', 'total_amount', 'net_amount', 'status', 'created_at', 'updated_at', 'items', 'returns', 'discount_info', 'receipt_calculation']
    
    def get_discount_info(self, obj):
        """Get discount information if available"""
        return getattr(obj, '_discount_info', None)
    
    def get_receipt_calculation(self, obj):
        """Get detailed receipt calculation breakdown"""
        from . import services
        return services.get_receipt_calculation(obj)


class SaleItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0'))

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError('Quantity must be greater than zero.')
        return value

    def validate_unit_price(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('Unit price cannot be negative.')
        return value

    def validate_discount(self, value):
        if value < 0:
            raise serializers.ValidationError('Discount cannot be negative.')
        return value


class CreateSaleSerializer(serializers.Serializer):
    sync_id = serializers.CharField(max_length=50, required=False, allow_null=True, allow_blank=True)
    branch_id = serializers.IntegerField(required=True)
    discount_code = serializers.CharField(max_length=50, required=False, allow_null=True, allow_blank=True)
    discount_id = serializers.IntegerField(required=False, allow_null=True)
    tax = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0'))
    notes = serializers.CharField(required=False, allow_blank=True)
    type_of_payment = serializers.CharField(required=True)
    items = SaleItemInputSerializer(many=True, required=True)
    
    def validate(self, data):
        """Validate that either discount_code or discount_id is provided, not both"""
        discount_code = data.get('discount_code')
        discount_id = data.get('discount_id')
        
        if discount_code and discount_id:
            raise serializers.ValidationError('Provide either discount_code or discount_id, not both.')
        
        return data

    def validate_sync_id(self, value):
        if value:
            value = value.strip()
            if not value:
                return None
            # Check if sync_id already exists
            if Sale.objects.filter(sync_id=value).exists():
                raise serializers.ValidationError('A sale with this sync_id already exists.')
        return value

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError('At least one sale item is required.')
        return value


class SaleItemAddSerializer(SaleItemInputSerializer):
    pass


class SaleItemsPayloadSerializer(serializers.Serializer):
    items = SaleItemAddSerializer(many=True, required=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError('At least one sale item is required.')
        return value


class BulkSaleSerializer(serializers.Serializer):
    sales = CreateSaleSerializer(many=True, required=True)

    def validate_sales(self, value):
        if not value:
            raise serializers.ValidationError('At least one sale payload is required.')
        return value


class SaleReturnSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    refund_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    authorization_code = serializers.CharField(required=False, allow_blank=True)

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError('Return quantity must be greater than zero.')
        return value


class BarcodeLookupSerializer(serializers.Serializer):
    branch_id = serializers.IntegerField(required=True)
    barcode = serializers.CharField(required=True, max_length=128)


class SalesHistoryQuerySerializer(serializers.Serializer):
    start_date = serializers.DateField(required=True)
    end_date = serializers.DateField(required=True)
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    group_by = serializers.ChoiceField(
        choices=['day', 'month', 'all'],
        default='day',
        required=False
    )
    use_stored_reports = serializers.BooleanField(default=True, required=False)
    
    def validate(self, data):
        if data['end_date'] < data['start_date']:
            raise serializers.ValidationError('end_date must be greater than or equal to start_date.')
        return data


class ReturnAuthorizationCodeSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = ReturnAuthorizationCode
        fields = [
            'id', 'branch', 'branch_name', 'code', 'expires_at',
            'is_active', 'is_expired', 'created_by', 'created_by_email', 'created_at'
        ]
        read_only_fields = fields

    def get_is_expired(self, obj):
        return obj.expires_at < timezone.now()


class ReturnAuthorizationCodeCreateSerializer(serializers.Serializer):
    branch_id = serializers.IntegerField(required=True)
    expires_in_hours = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=168,
        help_text='Number of hours before the code expires (default: 24 if minutes not provided).'
    )
    expires_in_minutes = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=1440,
        help_text='Number of minutes before the code expires (overrides hours when provided).'
    )
    notify = serializers.BooleanField(
        required=False,
        default=False,
        help_text='If true, emails the generated code to the branch manager(s).'
    )


class DiscountSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    is_valid = serializers.SerializerMethodField()
    
    class Meta:
        model = Discount
        fields = [
            'id', 'name', 'code', 'description',
            'discount_type', 'discount_value',
            'apply_to', 'product', 'product_name',
            'category', 'category_name',
            'branch', 'branch_name',
            'min_purchase_amount', 'max_discount_amount',
            'start_date', 'end_date',
            'is_active', 'usage_limit', 'usage_count',
            'created_by', 'created_by_email',
            'created_at', 'updated_at', 'is_valid',
        ]
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at', 'is_valid']
    
    def get_is_valid(self, obj):
        """Check if discount is currently valid"""
        return obj.is_valid()
    
    def to_internal_value(self, data):
        """Convert model instances to IDs if they are passed"""
        # Handle product field
        if 'product' in data and data['product'] is not None:
            if hasattr(data['product'], 'id'):
                data['product'] = data['product'].id
            elif not isinstance(data['product'], (int, type(None))):
                try:
                    data['product'] = int(data['product'])
                except (ValueError, TypeError):
                    pass
        
        # Handle category field
        if 'category' in data and data['category'] is not None:
            if hasattr(data['category'], 'id'):
                data['category'] = data['category'].id
            elif not isinstance(data['category'], (int, type(None))):
                try:
                    data['category'] = int(data['category'])
                except (ValueError, TypeError):
                    pass
        
        # Handle branch field
        if 'branch' in data and data['branch'] is not None:
            if hasattr(data['branch'], 'id'):
                data['branch'] = data['branch'].id
            elif not isinstance(data['branch'], (int, type(None))):
                try:
                    data['branch'] = int(data['branch'])
                except (ValueError, TypeError):
                    pass
        
        return super().to_internal_value(data)
    
    def validate_discount_value(self, value):
        """Validate discount value based on type"""
        discount_type = self.initial_data.get('discount_type', 'percentage')
        if discount_type == 'percentage' and (value < 0 or value > 100):
            raise serializers.ValidationError('Percentage discount must be between 0 and 100.')
        if discount_type == 'fixed' and value < 0:
            raise serializers.ValidationError('Fixed discount cannot be negative.')
        return value
    
    def validate(self, data):
        """Validate discount rules"""
        apply_to = data.get('apply_to', 'all')
        
        if apply_to == 'product' and not data.get('product'):
            raise serializers.ValidationError('Product is required when apply_to is "product".')
        if apply_to == 'category' and not data.get('category'):
            raise serializers.ValidationError('Category is required when apply_to is "category".')
        if apply_to == 'branch' and not data.get('branch'):
            raise serializers.ValidationError('Branch is required when apply_to is "branch".')
        if apply_to == 'min_purchase' and not data.get('min_purchase_amount'):
            raise serializers.ValidationError('Minimum purchase amount is required when apply_to is "min_purchase".')
        
        if data.get('end_date') and data.get('start_date'):
            if data['end_date'] < data['start_date']:
                raise serializers.ValidationError('End date must be after start date.')
        
        return data


class ApplyDiscountSerializer(serializers.Serializer):
    discount_code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    discount_id = serializers.IntegerField(required=False, allow_null=True)
    
    def validate(self, data):
        if not data.get('discount_code') and not data.get('discount_id'):
            raise serializers.ValidationError('Either discount_code or discount_id must be provided.')
        if data.get('discount_code') and data.get('discount_id'):
            raise serializers.ValidationError('Provide either discount_code or discount_id, not both.')
        return data


class CashReceivedSerializer(serializers.ModelSerializer):
    cashier_email = serializers.CharField(source='cashier.email', read_only=True)
    cashier_name = serializers.SerializerMethodField()
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    entered_by_email = serializers.CharField(source='entered_by.email', read_only=True)
    type_of_payment_display = serializers.CharField(source='get_type_of_payment_display', read_only=True)
    
    class Meta:
        model = CashReceived
        fields = [
            'id', 'cashier', 'cashier_email', 'cashier_name', 'branch', 'branch_name',
            'date', 'total_amount', 'type_of_payment', 'type_of_payment_display',
            'entered_by', 'entered_by_email', 'notes',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'entered_by']
    
    def get_cashier_name(self, obj):
        """Get cashier's full name from employee profile"""
        if obj.cashier:
            profile = getattr(obj.cashier, 'profile', None)
            if profile:
                employee = profile.first()
                if employee:
                    return f"{employee.first_name} {employee.last_name}".strip()
        return None


class CashReceivedCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashReceived
        fields = ['cashier', 'branch', 'date', 'total_amount', 'type_of_payment', 'notes']
    
    def validate(self, data):
        """Validate cash received data"""
        cashier = data.get('cashier')
        branch = data.get('branch')
        date = data.get('date')
        
        # Check if cashier belongs to the branch
        if cashier and branch:
            profile = getattr(cashier, 'profile', None)
            if profile:
                employee = profile.first()
                if employee and employee.branch != branch:
                    raise serializers.ValidationError(
                        'Cashier does not belong to the specified branch.'
                    )
        
        return data


class CashReceivedUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating cash received entries. Only allows updating amount and notes."""
    class Meta:
        model = CashReceived
        fields = ['total_amount', 'notes']
    
    def validate_total_amount(self, value):
        """Validate that total amount is not negative"""
        if value < 0:
            raise serializers.ValidationError('Total amount cannot be negative.')
        return value


class ExchangeRateSerializer(serializers.ModelSerializer):
    current_rate = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        help_text='Current exchange rate (must be greater than zero)'
    )

    class Meta:
        model = ExchangeRate
        fields = ['current_rate']

    def validate_current_rate(self, value):
        """Validate that the exchange rate is positive."""
        if value <= 0:
            raise serializers.ValidationError('Exchange rate must be greater than zero.')
        return value


