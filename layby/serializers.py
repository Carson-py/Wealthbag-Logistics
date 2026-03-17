from rest_framework import serializers
from .models import Layby, LaybyItem, LaybyPayment
from products.models import Product

class LaybyItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)

    class Meta:
        model = LaybyItem
        fields = ['id', 'product', 'product_name', 'product_sku', 'quantity', 'unit_price', 'subtotal']
        read_only_fields = ['id', 'subtotal']

class LaybyPaymentSerializer(serializers.ModelSerializer):
    cashier_email = serializers.CharField(source='cashier.email', read_only=True)
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)

    class Meta:
        model = LaybyPayment
        fields = ['id', 'amount', 'payment_method', 'payment_method_display', 'cashier', 'cashier_email', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']

class LaybySerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    cashier_email = serializers.CharField(source='cashier.email', read_only=True)
    items = LaybyItemSerializer(many=True, read_only=True)
    payments = LaybyPaymentSerializer(many=True, read_only=True)
    total_paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Layby
        fields = [
            'id', 'layby_number', 'customer_name', 'customer_phone',
            'cashier', 'cashier_email', 'branch', 'branch_name',
            'total_amount', 'deposit', 'balance', 'total_paid',
            'status', 'due_date', 'notes', 'items', 'payments',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'layby_number', 'total_amount', 'balance', 'total_paid', 'created_at', 'updated_at']

class CreateLaybySerializer(serializers.Serializer):
    customer_name = serializers.CharField(max_length=255)
    customer_phone = serializers.CharField(max_length=20)
    branch_id = serializers.IntegerField()
    due_date = serializers.DateField()
    notes = serializers.CharField(required=False, allow_blank=True)
    items = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )
    deposit = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_method = serializers.CharField(max_length=30)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        for item in value:
            if 'product_id' not in item or 'quantity' not in item:
                raise serializers.ValidationError("Each item must have a product_id and quantity.")
        return value

class LaybyPaymentInputSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_method = serializers.CharField(max_length=30)
    notes = serializers.CharField(required=False, allow_blank=True)
