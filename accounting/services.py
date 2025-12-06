from __future__ import annotations

from decimal import Decimal
from typing import Optional, Dict, Any, List

from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Q
from django.db.models.functions import TruncDate, TruncMonth

from organization.models import Branch
from sales.models import Sale, SaleItem
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
                                         sale__created_at__date__lte=end_date)
    if branch_id:
        sale_items = sale_items.filter(sale__branch_id=branch_id)

    revenue = sale_items.aggregate(total=Sum('subtotal'))['total'] or Decimal('0')
    cost_expr = ExpressionWrapper(F('purchase_price') * F('quantity'), output_field=DecimalField(max_digits=14, decimal_places=2))
    cost_of_goods = sale_items.aggregate(total=Sum(cost_expr))['total'] or Decimal('0')

    expense_queryset = Expense.objects.filter(
        Q(incurred_on__gte=start_date, incurred_on__lte=end_date) |
        Q(created_at__date__gte=start_date, created_at__date__lte=end_date)
    )
    if branch_id:
        expense_queryset = expense_queryset.filter(branch_id=branch_id)

    total_expenses = expense_queryset.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    print(total_expenses)
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

    sale_items = SaleItem.objects.filter(sale__in=sales)
    summary = {
        'total_orders': sales.count(),
        'total_units_sold': sale_items.aggregate(total=Sum('quantity'))['total'] or Decimal('0'),
        'total_sales': sale_items.aggregate(total=Sum('subtotal'))['total'] or Decimal('0'),
    }
    summary['average_order_value'] = (summary['total_sales'] / summary['total_orders']) if summary['total_orders'] else Decimal('0')

    breakdown = []
    if group_by == 'month':
        grouped = sale_items.annotate(period=TruncMonth('sale__created_at')).values('period').annotate(
            total_sales=Sum('subtotal'),
            total_units=Sum('quantity')
        ).order_by('period')
        for row in grouped:
            breakdown.append({
                'label': row['period'].strftime('%Y-%m'),
                'total_sales': row['total_sales'] or Decimal('0'),
                'total_units': row['total_units'] or Decimal('0'),
            })
    elif group_by == 'branch':
        grouped = sale_items.values('sale__branch_id', 'sale__branch__name').annotate(
            total_sales=Sum('subtotal'),
            total_units=Sum('quantity')
        ).order_by('sale__branch__name')
        for row in grouped:
            breakdown.append({
                'branch_id': row['sale__branch_id'],
                'branch_name': row['sale__branch__name'],
                'total_sales': row['total_sales'] or Decimal('0'),
                'total_units': row['total_units'] or Decimal('0'),
            })
    elif group_by == 'product':
        grouped = sale_items.values('product_id', 'product__name').annotate(
            total_sales=Sum('subtotal'),
            total_units=Sum('quantity')
        ).order_by('-total_sales')
        for row in grouped:
            breakdown.append({
                'product_id': row['product_id'],
                'product_name': row['product__name'],
                'total_sales': row['total_sales'] or Decimal('0'),
                'total_units': row['total_units'] or Decimal('0'),
            })
    else:  # day
        grouped = sale_items.annotate(period=TruncDate('sale__created_at')).values('period').annotate(
            total_sales=Sum('subtotal'),
            total_units=Sum('quantity')
        ).order_by('period')
        for row in grouped:
            breakdown.append({
                'label': row['period'].strftime('%Y-%m-%d'),
                'total_sales': row['total_sales'] or Decimal('0'),
                'total_units': row['total_units'] or Decimal('0'),
            })

    return {
        'summary': summary,
        'breakdown': breakdown,
    }

