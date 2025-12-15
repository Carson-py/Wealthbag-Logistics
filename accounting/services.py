from __future__ import annotations

from decimal import Decimal
from typing import Optional, Dict, Any, List

from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Q
from django.db.models.functions import TruncDate, TruncMonth

from organization.models import Branch
from sales.models import Sale, SaleItem
from sales import services as sales_services
from .models import Expense, ExpenseCategory, ProfitLossReport


def create_expense(*, data, created_by):
    expense = Expense.objects.create(
        category=data.get('category'),
        branch=data.get('branch'),
        warehouse=data.get('warehouse'),
        description=data.get('description', ''),
        amount=data['amount'],
        incurred_on=data['incurred_on'],
        attachment=data.get('attachment'),
        created_by=created_by,
    )
    return expense


def update_expense(expense: Expense, *, data):
    for field in ['category', 'branch', 'warehouse', 'description', 'amount', 'incurred_on', 'attachment']:
        if field in data:
            setattr(expense, field, data[field])
    expense.save()
    return expense


def generate_profit_loss_report(*, start_date, end_date, branch_id: Optional[int], generated_by, persist: bool = False) -> Dict[str, Any]:
    sale_filter = {
        'status': 'completed',
        'created_at__date__gte': start_date,
        'created_at__date__lte': end_date,
    }
    if branch_id:
        sale_filter['branch_id'] = branch_id

    sale_items = SaleItem.objects.filter(sale__status='completed',
                                         sale__created_at__date__gte=start_date,
                                         sale__created_at__date__lte=end_date).select_related('sale')
    if branch_id:
        sale_items = sale_items.filter(sale__branch_id=branch_id)

    # Calculate revenue and cost, converting ZIG amounts to USD
    revenue = Decimal('0')
    cost_of_goods = Decimal('0')
    
    for item in sale_items:
        payment_method = item.sale.type_of_payment
        # Convert subtotal (revenue) from ZIG to USD if needed
        item_revenue = sales_services.convert_zig_to_usd(item.subtotal, payment_method)
        revenue += item_revenue
        
        # Convert cost from ZIG to USD if needed
        item_cost = item.purchase_price * item.quantity
        item_cost_usd = sales_services.convert_zig_to_usd(item_cost, payment_method)
        cost_of_goods += item_cost_usd

    expense_queryset = Expense.objects.filter(
        Q(incurred_on__gte=start_date, incurred_on__lte=end_date) |
        Q(created_at__date__gte=start_date, created_at__date__lte=end_date)
    )
    if branch_id:
        expense_queryset = expense_queryset.filter(branch_id=branch_id)

    total_expenses = expense_queryset.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    grosss_profit = revenue - cost_of_goods
    net_profit = grosss_profit - total_expenses

    report_data = {
        'start_date': start_date,
        'end_date': end_date,
        'branch_id': branch_id,
        'total_revenue': revenue,
        'total_cost_of_goods': cost_of_goods,
        'total_expenses': total_expenses,
        'gross_profit': grosss_profit,
        'net_profit': net_profit,
    }

    if persist:
        ProfitLossReport.objects.create(
            start_date=start_date,
            end_date=end_date,
            branch_id=branch_id,
            total_revenue=revenue,
            total_cost_of_goods=cost_of_goods,
            total_expenses=total_expenses,
            net_profit=net_profit,
            generated_by=generated_by,
        )

    return report_data


def get_sales_report(*, start_date, end_date, branch_id: Optional[int], group_by: str) -> Dict[str, Any]:
    sales = Sale.objects.filter(
        status='completed',
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )
    if branch_id:
        sales = sales.filter(branch_id=branch_id)

    sale_items = SaleItem.objects.filter(sale__in=sales).select_related('sale')
    
    # Calculate total sales converting ZIG amounts to USD
    total_sales = Decimal('0')
    for item in sale_items:
        payment_method = item.sale.type_of_payment
        item_sales = sales_services.convert_zig_to_usd(item.subtotal, payment_method)
        total_sales += item_sales
    
    summary = {
        'total_orders': sales.count(),
        'total_units_sold': sale_items.aggregate(total=Sum('quantity'))['total'] or Decimal('0'),
        'total_sales': total_sales,
    }
    summary['average_order_value'] = (summary['total_sales'] / summary['total_orders']) if summary['total_orders'] else Decimal('0')

    breakdown = []
    if group_by == 'month':
        # Group by month and convert ZIG amounts
        from collections import defaultdict
        month_data = defaultdict(lambda: {'total_sales': Decimal('0'), 'total_units': Decimal('0')})
        for item in sale_items:
            period_key = item.sale.created_at.strftime('%Y-%m')
            payment_method = item.sale.type_of_payment
            item_sales = sales_services.convert_zig_to_usd(item.subtotal, payment_method)
            month_data[period_key]['total_sales'] += item_sales
            month_data[period_key]['total_units'] += item.quantity
        
        for period_key in sorted(month_data.keys()):
            breakdown.append({
                'label': period_key,
                'total_sales': month_data[period_key]['total_sales'],
                'total_units': month_data[period_key]['total_units'],
            })
    elif group_by == 'branch':
        # Group by branch and convert ZIG amounts
        from collections import defaultdict
        branch_data = defaultdict(lambda: {'total_sales': Decimal('0'), 'total_units': Decimal('0'), 'branch_name': ''})
        for item in sale_items:
            branch_id = item.sale.branch_id
            branch_name = item.sale.branch.name if item.sale.branch else ''
            payment_method = item.sale.type_of_payment
            item_sales = sales_services.convert_zig_to_usd(item.subtotal, payment_method)
            branch_data[branch_id]['total_sales'] += item_sales
            branch_data[branch_id]['total_units'] += item.quantity
            branch_data[branch_id]['branch_name'] = branch_name
        
        for branch_id in sorted(branch_data.keys()):
            breakdown.append({
                'branch_id': branch_id,
                'branch_name': branch_data[branch_id]['branch_name'],
                'total_sales': branch_data[branch_id]['total_sales'],
                'total_units': branch_data[branch_id]['total_units'],
            })
    elif group_by == 'product':
        # Group by product and convert ZIG amounts
        from collections import defaultdict
        product_data = defaultdict(lambda: {'total_sales': Decimal('0'), 'total_units': Decimal('0'), 'product_name': ''})
        for item in sale_items:
            product_id = item.product_id
            product_name = item.product.name if item.product else ''
            payment_method = item.sale.type_of_payment
            item_sales = sales_services.convert_zig_to_usd(item.subtotal, payment_method)
            product_data[product_id]['total_sales'] += item_sales
            product_data[product_id]['total_units'] += item.quantity
            product_data[product_id]['product_name'] = product_name
        
        # Sort by total_sales descending
        sorted_products = sorted(product_data.items(), key=lambda x: x[1]['total_sales'], reverse=True)
        for product_id, data in sorted_products:
            breakdown.append({
                'product_id': product_id,
                'product_name': data['product_name'],
                'total_sales': data['total_sales'],
                'total_units': data['total_units'],
            })
    else:  # day
        # Group by day and convert ZIG amounts
        from collections import defaultdict
        day_data = defaultdict(lambda: {'total_sales': Decimal('0'), 'total_units': Decimal('0')})
        for item in sale_items:
            period_key = item.sale.created_at.date().isoformat()
            payment_method = item.sale.type_of_payment
            item_sales = sales_services.convert_zig_to_usd(item.subtotal, payment_method)
            day_data[period_key]['total_sales'] += item_sales
            day_data[period_key]['total_units'] += item.quantity
        
        for period_key in sorted(day_data.keys()):
            breakdown.append({
                'label': period_key,
                'total_sales': day_data[period_key]['total_sales'],
                'total_units': day_data[period_key]['total_units'],
            })

    return {
        'summary': summary,
        'breakdown': breakdown,
    }

