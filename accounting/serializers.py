from django.utils import timezone
from rest_framework import serializers

from .models import ExpenseCategory, Expense, ProfitLossReport


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = ['id', 'name', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)

    class Meta:
        model = Expense
        fields = [
            'id', 'category', 'category_name', 'branch', 'branch_name',
            'warehouse', 'warehouse_name', 'description', 'amount',
            'incurred_on', 'attachment', 'created_by', 'created_by_email',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_by', 'created_by_email', 'created_at', 'updated_at']


class ProfitLossReportSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    generated_by_email = serializers.CharField(source='generated_by.email', read_only=True)

    class Meta:
        model = ProfitLossReport
        fields = [
            'id', 'start_date', 'end_date', 'branch', 'branch_name',
            'total_revenue', 'total_cost_of_goods', 'total_expenses',
            'net_profit', 'generated_by', 'generated_by_email', 'generated_at'
        ]
        read_only_fields = fields


class ProfitLossQuerySerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    persist = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        today = timezone.now().date()
        default_start = today.replace(day=1)

        start_date = data.get('start_date', default_start)
        end_date = data.get('end_date', today)

        if end_date < start_date:
            raise serializers.ValidationError('end_date must be greater than or equal to start_date.')

        data['start_date'] = start_date
        data['end_date'] = end_date
        return data


class SalesReportQuerySerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    group_by = serializers.ChoiceField(choices=['day', 'month', 'branch', 'product'], default='day')

    def validate(self, data):
        if data['end_date'] < data['start_date']:
            raise serializers.ValidationError('end_date must be greater than or equal to start_date.')
        return data

