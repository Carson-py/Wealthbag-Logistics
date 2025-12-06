from collections import defaultdict
from calendar import month_abbr
from decimal import Decimal
from datetime import timedelta, datetime
from typing import Dict, List, Optional, Tuple

from django.db.models import Sum, Q, Max, F, DecimalField, ExpressionWrapper, Count, Avg
from django.db.models.functions import Coalesce, TruncDate, TruncWeek, TruncMonth, Extract
from django.utils import timezone

from accounts.models import User, Employee, AuditLog
from organization.models import Branch, Warehouse
from products.models import Product, Category
from sales.models import Sale, SaleItem, ProductReturn
from stock.models import BranchStock, StockEntry, StockTransfer, StockTransferItem, StockAdjustment, Supplier

# Constants
DEAD_STOCK_PERIODS = {
    '7d': timedelta(days=7),
    '30d': timedelta(days=30),
    '60d': timedelta(days=60),
    '90d': timedelta(days=90),
    '6m': timedelta(days=180),
    '1y': timedelta(days=365),
}

DEAD_STOCK_MIN_SALES = {
    '7d': Decimal('1'),
    '30d': Decimal('5'),
    '60d': Decimal('8'),
    '90d': Decimal('10'),
    '6m': Decimal('15'),
    '1y': Decimal('20'),
}

SLOW_STOCK_MAX_SALES = {
    '7d': Decimal('5'),
    '30d': Decimal('20'),
    '60d': Decimal('40'),
    '90d': Decimal('70'),
    '6m': Decimal('120'),
    '1y': Decimal('200'),
}


def get_admin_dashboard_data(dead_stock_period: str = '90d', slow_stock_period: str = '30d') -> Dict:
    """Get admin dashboard data including summaries, dead stock, and slow stock."""
    from django.db.models import Sum, Count, Q
    from decimal import Decimal
    
    # Summary statistics
    total_branches = Branch.objects.count()
    total_warehouses = Warehouse.objects.count()
    branch_stock_count = BranchStock.objects.count()
    warehouse_stock_count = StockEntry.objects.count()
    total_products = Product.objects.count()
    active_users = User.objects.filter(account_status='active').count()
    inactive_users = User.objects.filter(account_status='blocked').count()

    # Recently added products
    recently_added_products = [
        {
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'created_at': product.created_at,
        }
        for product in Product.objects.order_by('-created_at')[:5]
    ]

    # Dead stock analysis
    dead_stock_data = get_dead_stock_analysis(dead_stock_period)
    
    # Slow stock analysis
    slow_stock_data = get_slow_stock_analysis(slow_stock_period)
    
    # Product return rate calculation
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)
    
    # Calculate return rate for last 30 days
    completed_sales_30d = Sale.objects.filter(
        status='completed',
        created_at__gte=thirty_days_ago
    )
    
    total_sold_quantity_30d = SaleItem.objects.filter(
        sale__in=completed_sales_30d
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    
    total_returned_quantity_30d = ProductReturn.objects.filter(
        created_at__gte=thirty_days_ago
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    
    total_sales_amount_30d = completed_sales_30d.aggregate(
        total=Sum('total_amount')
    )['total'] or Decimal('0')
    
    total_refund_amount_30d = ProductReturn.objects.filter(
        created_at__gte=thirty_days_ago
    ).aggregate(total=Sum('refund_amount'))['total'] or Decimal('0')
    
    # Calculate return rate for last 90 days
    completed_sales_90d = Sale.objects.filter(
        status='completed',
        created_at__gte=ninety_days_ago
    )
    
    total_sold_quantity_90d = SaleItem.objects.filter(
        sale__in=completed_sales_90d
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    
    total_returned_quantity_90d = ProductReturn.objects.filter(
        created_at__gte=ninety_days_ago
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    
    total_sales_amount_90d = completed_sales_90d.aggregate(
        total=Sum('total_amount')
    )['total'] or Decimal('0')
    
    total_refund_amount_90d = ProductReturn.objects.filter(
        created_at__gte=ninety_days_ago
    ).aggregate(total=Sum('refund_amount'))['total'] or Decimal('0')
    
    # Calculate return rates (percentage)
    quantity_return_rate_30d = (
        (total_returned_quantity_30d / total_sold_quantity_30d * 100)
        if total_sold_quantity_30d > 0 else Decimal('0')
    )
    
    value_return_rate_30d = (
        (total_refund_amount_30d / total_sales_amount_30d * 100)
        if total_sales_amount_30d > 0 else Decimal('0')
    )
    
    quantity_return_rate_90d = (
        (total_returned_quantity_90d / total_sold_quantity_90d * 100)
        if total_sold_quantity_90d > 0 else Decimal('0')
    )
    
    value_return_rate_90d = (
        (total_refund_amount_90d / total_sales_amount_90d * 100)
        if total_sales_amount_90d > 0 else Decimal('0')
    )
    
    # Get top returned products (last 30 days)
    top_returned_products = ProductReturn.objects.filter(
        created_at__gte=thirty_days_ago
    ).values('product__id', 'product__name', 'product__sku').annotate(
        return_count=Count('id'),
        total_returned_quantity=Sum('quantity'),
        total_refund_amount=Sum('refund_amount')
    ).order_by('-total_returned_quantity')[:10]
    
    top_returned_products_list = [
        {
            'product_id': item['product__id'],
            'product_name': item['product__name'],
            'product_sku': item['product__sku'],
            'return_count': item['return_count'],
            'total_returned_quantity': float(item['total_returned_quantity']),
            'total_refund_amount': float(item['total_refund_amount']),
        }
        for item in top_returned_products
    ]

    return {
        'summary': {
            'total_branches': total_branches,
            'total_warehouses': total_warehouses,
            'total_products': total_products,
        },
        'stock': {
            'branch_stock_count': branch_stock_count,
            'warehouse_stock_count': warehouse_stock_count,
            'total_stock_records': branch_stock_count + warehouse_stock_count,
        },
        'users': {
            'active': active_users,
            'inactive': inactive_users,
        },
        'recently_added_products': recently_added_products,
        'dead_stock': dead_stock_data,
        'slow_stock': slow_stock_data,
        'product_return_rate': {
            'last_30_days': {
                'quantity_return_rate': float(quantity_return_rate_30d),
                'value_return_rate': float(value_return_rate_30d),
                'total_sold_quantity': float(total_sold_quantity_30d),
                'total_returned_quantity': float(total_returned_quantity_30d),
                'total_sales_amount': float(total_sales_amount_30d),
                'total_refund_amount': float(total_refund_amount_30d),
            },
            'last_90_days': {
                'quantity_return_rate': float(quantity_return_rate_90d),
                'value_return_rate': float(value_return_rate_90d),
                'total_sold_quantity': float(total_sold_quantity_90d),
                'total_returned_quantity': float(total_returned_quantity_90d),
                'total_sales_amount': float(total_sales_amount_90d),
                'total_refund_amount': float(total_refund_amount_90d),
            },
            'top_returned_products': top_returned_products_list,
        },
    }


def get_dead_stock_analysis(period: str = '90d') -> Dict:
    """Analyze dead stock (products that undersell for the selected period)."""
    if period not in DEAD_STOCK_PERIODS:
        period = '90d'
    
    period_delta = DEAD_STOCK_PERIODS[period]
    minimum_sales = DEAD_STOCK_MIN_SALES.get(period, Decimal('1'))
    cutoff = timezone.now() - period_delta

    base_queryset = (
        Product.objects.annotate(
            warehouse_qty=Coalesce(Sum('stock_entries__quantity'), Decimal('0')),
            branch_qty=Coalesce(Sum('branch_stock_entries__quantity'), Decimal('0')),
            last_sale=Max('sale_items__sale__created_at'),
        )
        .filter(Q(warehouse_qty__gt=0) | Q(branch_qty__gt=0))
    )

    dead_stock_queryset = (
        base_queryset.annotate(
            dead_period_sales=Coalesce(
                Sum(
                    'sale_items__quantity',
                    filter=Q(sale_items__sale__created_at__gte=cutoff),
                ),
                Decimal('0'),
            )
        )
        .filter(dead_period_sales__lt=minimum_sales)
        .order_by('dead_period_sales', 'name')
    )

    dead_stock_products = list(dead_stock_queryset[:25])
    product_ids = {product.id for product in dead_stock_products}
    
    last_sale_map = _get_last_sale_map(product_ids)
    now = timezone.now()
    
    items = []
    for product in dead_stock_products:
        warehouse_qty = product.warehouse_qty or Decimal('0')
        branch_qty = product.branch_qty or Decimal('0')
        total_qty = warehouse_qty + branch_qty

        last_sale_item = last_sale_map.get(product.id)
        last_sold_at = last_sale_item.sale.created_at if last_sale_item else None
        days_since_last_sale = (now - last_sold_at).days if last_sold_at else None

        last_sale_info = (
            {
                'sale_id': last_sale_item.sale.id,
                'sale_number': last_sale_item.sale.sale_number,
                'sold_at': last_sale_item.sale.created_at,
                'quantity_sold': last_sale_item.quantity,
            }
            if last_sale_item
            else None
        )

        items.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'warehouse_stock': warehouse_qty,
            'branch_stock': branch_qty,
            'total_stock': total_qty,
            'last_sold_at': last_sold_at,
            'days_since_last_sale': days_since_last_sale,
            'last_sale': last_sale_info,
            'sold_in_period': product.dead_period_sales,
        })

    return {
        'selected_period': period,
        'available_periods': list(DEAD_STOCK_PERIODS.keys()),
        'cutoff_datetime': cutoff,
        'minimum_sales_required': minimum_sales,
        'items': items,
    }


def get_slow_stock_analysis(period: str = '30d') -> Dict:
    """Analyze slow-moving stock (products selling but below desired velocity)."""
    if period not in DEAD_STOCK_PERIODS:
        period = '30d'
    
    period_delta = DEAD_STOCK_PERIODS[period]
    min_sales = DEAD_STOCK_MIN_SALES.get(period, Decimal('1'))
    max_sales = SLOW_STOCK_MAX_SALES.get(period, min_sales * 2)
    cutoff = timezone.now() - period_delta

    base_queryset = (
        Product.objects.annotate(
            warehouse_qty=Coalesce(Sum('stock_entries__quantity'), Decimal('0')),
            branch_qty=Coalesce(Sum('branch_stock_entries__quantity'), Decimal('0')),
            last_sale=Max('sale_items__sale__created_at'),
        )
        .filter(Q(warehouse_qty__gt=0) | Q(branch_qty__gt=0))
    )

    slow_stock_queryset = (
        base_queryset.annotate(
            slow_period_sales=Coalesce(
                Sum(
                    'sale_items__quantity',
                    filter=Q(sale_items__sale__created_at__gte=cutoff),
                ),
                Decimal('0'),
            )
        )
        .filter(slow_period_sales__gte=min_sales)
        .filter(slow_period_sales__lt=max_sales)
        .order_by('slow_period_sales', 'name')
    )

    slow_stock_products = list(slow_stock_queryset[:25])
    product_ids = {product.id for product in slow_stock_products}
    
    last_sale_map = _get_last_sale_map(product_ids)
    now = timezone.now()
    
    items = []
    for product in slow_stock_products:
        warehouse_qty = product.warehouse_qty or Decimal('0')
        branch_qty = product.branch_qty or Decimal('0')
        total_qty = warehouse_qty + branch_qty

        last_sale_item = last_sale_map.get(product.id)
        last_sold_at = last_sale_item.sale.created_at if last_sale_item else None
        days_since_last_sale = (now - last_sold_at).days if last_sold_at else None

        last_sale_info = (
            {
                'sale_id': last_sale_item.sale.id,
                'sale_number': last_sale_item.sale.sale_number,
                'sold_at': last_sale_item.sale.created_at,
                'quantity_sold': last_sale_item.quantity,
            }
            if last_sale_item
            else None
        )

        items.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'warehouse_stock': warehouse_qty,
            'branch_stock': branch_qty,
            'total_stock': total_qty,
            'last_sold_at': last_sold_at,
            'days_since_last_sale': days_since_last_sale,
            'last_sale': last_sale_info,
            'sold_in_period': product.slow_period_sales,
        })

    return {
        'selected_period': period,
        'available_periods': list(DEAD_STOCK_PERIODS.keys()),
        'cutoff_datetime': cutoff,
        'minimum_sales_threshold': min_sales,
        'maximum_sales_threshold': max_sales,
        'items': items,
    }


def _get_last_sale_map(product_ids: set, branch_id: Optional[int] = None) -> Dict:
    """Get the last sale item for each product."""
    if not product_ids:
        return {}
    
    last_sale_items = SaleItem.objects.filter(product_id__in=product_ids)
    if branch_id:
        last_sale_items = last_sale_items.filter(sale__branch_id=branch_id)
    last_sale_items = last_sale_items.select_related('sale').order_by('product_id', '-sale__created_at')

    last_sale_map = {}
    for item in last_sale_items:
        if item.product_id not in last_sale_map:
            last_sale_map[item.product_id] = item
    
    return last_sale_map


def resolve_date_range(range_key: str, start_str: str = None, end_str: str = None) -> Tuple[datetime, datetime]:
    """Resolve date range from query parameters."""
    now = timezone.now()
    range_key = (range_key or '').lower()

    if range_key == 'today':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_key == 'week':
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_key == 'month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif range_key == 'custom' and start_str:
        try:
            start_date = datetime.fromisoformat(start_str)
            if timezone.is_naive(start_date):
                start_date = timezone.make_aware(start_date)
            start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(days=30)

    if range_key == 'custom' and end_str:
        try:
            end_date = datetime.fromisoformat(end_str)
            if timezone.is_naive(end_date):
                end_date = timezone.make_aware(end_date)
            end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError:
            end = now
    else:
        end = now

    return start, end


def get_revenue_trends_data() -> Dict:
    """Get revenue trend data for reports."""
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)
    current_year = now.year
    previous_year = current_year - 1

    net_revenue_expr = ExpressionWrapper(
        F('total_amount') - F('discount') + F('tax'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )

    # Daily revenue (last 30 days)
    daily_revenue = list(
        Sale.objects.filter(created_at__gte=thirty_days_ago)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')))
        .order_by('day')
    )

    # Weekly revenue (last 12 weeks)
    weekly_revenue = list(
        Sale.objects.filter(created_at__gte=now - timedelta(weeks=12))
        .annotate(week=TruncWeek('created_at'))
        .values('week')
        .annotate(revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')))
        .order_by('week')
    )

    # Monthly revenue (current year)
    monthly_revenue = list(
        Sale.objects.filter(created_at__year=current_year)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')))
        .order_by('month')
    )

    # Year-over-year comparison
    yoy_current = {entry['month'].month: entry['revenue'] for entry in monthly_revenue}
    yoy_previous = {
        entry['month'].month: entry['revenue']
        for entry in Sale.objects.filter(created_at__year=previous_year)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')))
    }
    yoy_series = []
    for month in range(1, 13):
        yoy_series.append({
            'month': month,
            'month_label': month_abbr[month],
            'current_year': yoy_current.get(month, Decimal('0')),
            'previous_year': yoy_previous.get(month, Decimal('0')),
        })

    return {
        'daily_revenue_trend': daily_revenue,
        'weekly_revenue_trend': weekly_revenue,
        'monthly_revenue_trend': monthly_revenue,
        'year_over_year_revenue': yoy_series,
    }


def get_category_revenue_data() -> List[Dict]:
    """Get category-wise revenue data."""
    current_year = timezone.now().year
    
    category_revenue_queryset = (
        SaleItem.objects.filter(sale__created_at__year=current_year)
        .values('product__category__id', 'product__category__name')
        .annotate(revenue=Coalesce(Sum('subtotal'), Decimal('0')))
        .order_by('-revenue')
    )
    
    return [
        {
            'category_id': entry['product__category__id'],
            'category_name': entry['product__category__name'] or 'Uncategorized',
            'revenue': entry['revenue'],
        }
        for entry in category_revenue_queryset
    ]


def get_top_products_data() -> Dict:
    """Get top revenue-generating products with trends."""
    now = timezone.now()
    ninety_days_ago = now - timedelta(days=90)
    
    top_products_queryset = (
        SaleItem.objects.filter(sale__created_at__gte=ninety_days_ago)
        .values('product_id', 'product__name', 'product__sku')
        .annotate(revenue=Coalesce(Sum('subtotal'), Decimal('0')))
        .order_by('-revenue')[:5]
    )
    
    top_products = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'revenue': entry['revenue'],
        }
        for entry in top_products_queryset
    ]
    
    top_product_ids = [product['product_id'] for product in top_products]
    
    product_trends = defaultdict(list)
    if top_product_ids:
        product_daily = (
            SaleItem.objects.filter(
                product_id__in=top_product_ids,
                sale__created_at__gte=ninety_days_ago,
            )
            .annotate(day=TruncDate('sale__created_at'))
            .values('product_id', 'product__name', 'day')
            .annotate(revenue=Coalesce(Sum('subtotal'), Decimal('0')))
            .order_by('product_id', 'day')
        )

        for entry in product_daily:
            product_trends[entry['product_id']].append({
                'day': entry['day'],
                'revenue': entry['revenue'],
            })

    for product in top_products:
        product['trend'] = product_trends.get(product['product_id'], [])

    return {'top_products': top_products}


def get_fast_moving_products(
    days: int = 30,
    limit: int = 10,
    branch_id: Optional[int] = None,
) -> Dict:
    """Return fast-moving products based on quantity sold within the given window."""
    now = timezone.now()
    start = now - timedelta(days=days)

    sale_items = SaleItem.objects.filter(sale__created_at__gte=start)
    if branch_id:
        sale_items = sale_items.filter(sale__branch_id=branch_id)

    fast_queryset = (
        sale_items
        .values('product_id', 'product__name', 'product__sku')
        .annotate(
            quantity_sold=Coalesce(Sum('quantity'), Decimal('0')),
            revenue=Coalesce(Sum('subtotal'), Decimal('0')),
        )
        .order_by('-quantity_sold')[:limit]
    )

    items = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'quantity_sold': entry['quantity_sold'],
            'revenue': entry['revenue'],
        }
        for entry in fast_queryset
    ]

    return {
        'selected_days': days,
        'branch_id': branch_id,
        'items': items,
    }


def get_branch_warehouse_revenue_data() -> Dict:
    """Get branch and warehouse revenue trends."""
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    
    net_revenue_expr = ExpressionWrapper(
        F('total_amount') - F('discount') + F('tax'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    branch_revenue_queryset = (
        Sale.objects.filter(created_at__gte=thirty_days_ago)
        .values('branch__id', 'branch__name')
        .annotate(revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')))
        .order_by('-revenue')
    )
    
    branch_revenue = [
        {
            'branch_id': entry['branch__id'],
            'branch_name': entry['branch__name'],
            'revenue': entry['revenue'],
        }
        for entry in branch_revenue_queryset
    ]
    
    warehouse_revenue_queryset = (
        Sale.objects.filter(created_at__gte=thirty_days_ago)
        .values('branch__warehouse__id', 'branch__warehouse__name')
        .annotate(revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')))
        .order_by('-revenue')
    )
    
    warehouse_revenue = [
        {
            'warehouse_id': entry['branch__warehouse__id'],
            'warehouse_name': entry['branch__warehouse__name'],
            'revenue': entry['revenue'],
        }
        for entry in warehouse_revenue_queryset
        if entry['branch__warehouse__id'] is not None  # Only include entries with warehouse
    ]
    
    return {
        'branch_revenue_trend': branch_revenue,
        'warehouse_revenue_trend': warehouse_revenue,
    }


def get_cashier_performance_data(range_key: str = 'month', start_str: str = None, end_str: str = None, 
                                cashier_id: int = None, branch_id: int = None) -> Dict:
    """Get cashier performance data with detailed metrics."""
    start_date, end_date = resolve_date_range(range_key, start_str, end_str)
    
    net_revenue_expr = ExpressionWrapper(
        F('total_amount') - F('discount') + F('tax'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    cost_expr = ExpressionWrapper(
        F('items__purchase_price') * F('items__quantity'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    cashier_sales = (
        Sale.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
            cashier__isnull=False,
            status='completed',
        )
    )
    
    if cashier_id:
        cashier_sales = cashier_sales.filter(cashier_id=cashier_id)
    
    if branch_id:
        cashier_sales = cashier_sales.filter(branch_id=branch_id)

    cashier_queryset = (
        cashier_sales.values(
            'cashier_id',
            'cashier__email',
            'cashier__profile__first_name',
            'cashier__profile__last_name',
        )
        .annotate(
            total_sales=Coalesce(Sum(net_revenue_expr), Decimal('0')),
            total_cost=Coalesce(Sum(cost_expr), Decimal('0')),
            transaction_count=Count('id'),
            total_items=Coalesce(Sum('items__quantity'), Decimal('0')),
            total_discount=Coalesce(Sum('discount'), Decimal('0')),
            total_tax=Coalesce(Sum('tax'), Decimal('0')),
            refund_count=Count(
                'returns',
                filter=Q(
                    returns__created_at__gte=start_date,
                    returns__created_at__lte=end_date,
                ),
            ),
            void_count=Count('id', filter=Q(status='cancelled')),
        )
        .order_by('-total_sales')
    )

    cashier_performance = []
    for entry in cashier_queryset:
        transactions = entry['transaction_count'] or 0
        total_sales = entry['total_sales'] or Decimal('0')
        total_cost = entry['total_cost'] or Decimal('0')
        total_items = entry['total_items'] or Decimal('0')
        refund_count = entry['refund_count'] or 0
        void_count = entry['void_count'] or 0
        total_profit = total_sales - total_cost

        avg_sale = total_sales / transactions if transactions else Decimal('0')
        refund_rate = (Decimal(refund_count) / transactions) if transactions else Decimal('0')
        profit_margin = (total_profit / total_sales * 100) if total_sales > 0 else Decimal('0')
        items_per_transaction = total_items / transactions if transactions else Decimal('0')

        first_name = entry.get('cashier__profile__first_name') or ''
        last_name = entry.get('cashier__profile__last_name') or ''
        full_name = f"{first_name} {last_name}".strip()

        cashier_performance.append({
            'cashier_id': entry['cashier_id'],
            'cashier_name': full_name or entry['cashier__email'],
            'cashier_email': entry['cashier__email'],
            'total_sales_amount': total_sales,
            'total_cost': total_cost,
            'total_profit': total_profit,
            'profit_margin_percentage': profit_margin,
            'transaction_count': transactions,
            'average_sale_value': avg_sale,
            'total_items_sold': total_items,
            'items_per_transaction': items_per_transaction,
            'total_discount': entry['total_discount'] or Decimal('0'),
            'total_tax': entry['total_tax'] or Decimal('0'),
            'refund_count': refund_count,
            'void_count': void_count,
            'refund_rate': refund_rate,
        })

    # Get daily trends for cashiers
    daily_trends = []
    if cashier_id:
        daily_cashier_sales = cashier_sales.filter(cashier_id=cashier_id)
        daily_trends = list(
            daily_cashier_sales.annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(
                transaction_count=Count('id'),
                total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
                total_cost=Coalesce(Sum(cost_expr), Decimal('0')),
                total_items=Coalesce(Sum('items__quantity'), Decimal('0')),
            )
            .order_by('day')
        )
        daily_trends = [
            {
                'date': entry['day'],
                'transaction_count': entry['transaction_count'],
                'total_revenue': entry['total_revenue'],
                'total_cost': entry['total_cost'],
                'total_profit': entry['total_revenue'] - entry['total_cost'],
                'total_items_sold': entry['total_items'],
            }
            for entry in daily_trends
        ]
    else:
        # Get daily trends aggregated across all cashiers
        daily_trends = list(
            cashier_sales.annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(
                transaction_count=Count('id'),
                total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
                total_cost=Coalesce(Sum(cost_expr), Decimal('0')),
                total_items=Coalesce(Sum('items__quantity'), Decimal('0')),
            )
            .order_by('day')
        )
        daily_trends = [
            {
                'date': entry['day'],
                'transaction_count': entry['transaction_count'],
                'total_revenue': entry['total_revenue'],
                'total_cost': entry['total_cost'],
                'total_profit': entry['total_revenue'] - entry['total_cost'],
                'total_items_sold': entry['total_items'],
            }
            for entry in daily_trends
        ]

    # Get payment method breakdown
    payment_method_breakdown = []
    if cashier_id:
        payment_method_data = list(
            cashier_sales.filter(cashier_id=cashier_id)
            .values('type_of_payment')
            .annotate(
                transaction_count=Count('id'),
                total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
            )
            .order_by('-total_revenue')
        )
    else:
        payment_method_data = list(
            cashier_sales.values('type_of_payment')
            .annotate(
                transaction_count=Count('id'),
                total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
            )
            .order_by('-total_revenue')
        )
    
    payment_method_breakdown = [
        {
            'payment_method': entry['type_of_payment'],
            'transaction_count': entry['transaction_count'],
            'total_revenue': entry['total_revenue'],
        }
        for entry in payment_method_data
    ]

    # Get branch performance breakdown (if not filtering by branch)
    branch_breakdown = []
    if not branch_id and cashier_id:
        branch_data = list(
            cashier_sales.filter(cashier_id=cashier_id)
            .values('branch_id', 'branch__name')
            .annotate(
                transaction_count=Count('id'),
                total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
            )
            .order_by('-total_revenue')
        )
        branch_breakdown = [
            {
                'branch_id': entry['branch_id'],
                'branch_name': entry['branch__name'],
                'transaction_count': entry['transaction_count'],
                'total_revenue': entry['total_revenue'],
            }
            for entry in branch_data
        ]

    return {
        'selected_range': range_key,
        'range_start': start_date,
        'range_end': end_date,
        'cashiers': cashier_performance,
        'daily_trends': daily_trends,
        'payment_method_breakdown': payment_method_breakdown,
        'branch_breakdown': branch_breakdown,
        'filters': {
            'cashier_id': cashier_id,
            'branch_id': branch_id,
        },
    }


def get_stock_transfer_statistics() -> Dict:
    """Get stock transfer statistics."""
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    current_year = now.year
    
    transfer_base_queryset = StockTransfer.objects.filter(created_at__gte=thirty_days_ago)
    
    transfer_cost_expr = ExpressionWrapper(
        F('items__quantity') * Coalesce(F('items__purchase_price'), Decimal('0')),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    # Transfer statistics by type
    transfer_by_type = list(
        transfer_base_queryset.values('transfer_type')
        .annotate(
            count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('-count')
    )
    
    transfer_type_stats = [
        {
            'transfer_type': entry['transfer_type'],
            'transfer_type_display': dict(StockTransfer.TRANSFER_TYPES).get(entry['transfer_type'], entry['transfer_type']),
            'count': entry['count'],
            'total_cost': entry['total_cost'],
            'total_quantity': entry['total_quantity'],
        }
        for entry in transfer_by_type
    ]
    
    # Transfer statistics by status
    transfer_by_status = list(
        transfer_base_queryset.values('status')
        .annotate(
            count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('-count')
    )
    
    transfer_status_stats = [
        {
            'status': entry['status'],
            'status_display': dict(StockTransfer.STATUS_CHOICES).get(entry['status'], entry['status']),
            'count': entry['count'],
            'total_cost': entry['total_cost'],
            'total_quantity': entry['total_quantity'],
        }
        for entry in transfer_by_status
    ]
    
    # Daily transfer trend
    daily_transfers = list(
        transfer_base_queryset.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(
            count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('day')
    )
    
    # Weekly transfer trend
    weekly_transfers = list(
        StockTransfer.objects.filter(created_at__gte=now - timedelta(weeks=12))
        .annotate(week=TruncWeek('created_at'))
        .values('week')
        .annotate(
            count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('week')
    )
    
    # Monthly transfer trend
    monthly_transfers = list(
        StockTransfer.objects.filter(created_at__year=current_year)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(
            count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('month')
    )
    
    # Top source warehouses
    top_source_warehouses = list(
        transfer_base_queryset.filter(source_warehouse__isnull=False)
        .values('source_warehouse__id', 'source_warehouse__name')
        .annotate(
            transfer_count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('-transfer_count')[:10]
    )
    
    source_warehouse_stats = [
        {
            'warehouse_id': entry['source_warehouse__id'],
            'warehouse_name': entry['source_warehouse__name'],
            'transfer_count': entry['transfer_count'],
            'total_cost': entry['total_cost'],
            'total_quantity': entry['total_quantity'],
        }
        for entry in top_source_warehouses
    ]
    
    # Top source branches
    top_source_branches = list(
        transfer_base_queryset.filter(source_branch__isnull=False)
        .values('source_branch__id', 'source_branch__name')
        .annotate(
            transfer_count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('-transfer_count')[:10]
    )
    
    source_branch_stats = [
        {
            'branch_id': entry['source_branch__id'],
            'branch_name': entry['source_branch__name'],
            'transfer_count': entry['transfer_count'],
            'total_cost': entry['total_cost'],
            'total_quantity': entry['total_quantity'],
        }
        for entry in top_source_branches
    ]
    
    # Top destination warehouses
    top_dest_warehouses = list(
        transfer_base_queryset.filter(destination_warehouse__isnull=False)
        .values('destination_warehouse__id', 'destination_warehouse__name')
        .annotate(
            transfer_count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('-transfer_count')[:10]
    )
    
    dest_warehouse_stats = [
        {
            'warehouse_id': entry['destination_warehouse__id'],
            'warehouse_name': entry['destination_warehouse__name'],
            'transfer_count': entry['transfer_count'],
            'total_cost': entry['total_cost'],
            'total_quantity': entry['total_quantity'],
        }
        for entry in top_dest_warehouses
    ]
    
    # Top destination branches
    top_dest_branches = list(
        transfer_base_queryset.filter(destination_branch__isnull=False)
        .values('destination_branch__id', 'destination_branch__name')
        .annotate(
            transfer_count=Count('id', distinct=True),
            total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('-transfer_count')[:10]
    )
    
    dest_branch_stats = [
        {
            'branch_id': entry['destination_branch__id'],
            'branch_name': entry['destination_branch__name'],
            'transfer_count': entry['transfer_count'],
            'total_cost': entry['total_cost'],
            'total_quantity': entry['total_quantity'],
        }
        for entry in top_dest_branches
    ]
    
    # Transfer summary
    total_transfers = transfer_base_queryset.count()
    completed_transfers = transfer_base_queryset.filter(status='completed').count()
    completion_rate = (Decimal(completed_transfers) / Decimal(total_transfers) * 100) if total_transfers > 0 else Decimal('0')
    
    transfer_totals = transfer_base_queryset.aggregate(
        total_cost=Coalesce(Sum(transfer_cost_expr), Decimal('0')),
        total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
    )
    
    transfer_summary = {
        'total_transfers': total_transfers,
        'completed_transfers': completed_transfers,
        'pending_transfers': transfer_base_queryset.filter(status='pending').count(),
        'in_transit_transfers': transfer_base_queryset.filter(status='in_transit').count(),
        'cancelled_transfers': transfer_base_queryset.filter(status='cancelled').count(),
        'completion_rate': completion_rate,
        'total_cost': transfer_totals['total_cost'],
        'total_quantity': transfer_totals['total_quantity'],
    }
    
    return {
        'summary': transfer_summary,
        'by_type': transfer_type_stats,
        'by_status': transfer_status_stats,
        'daily_trend': daily_transfers,
        'weekly_trend': weekly_transfers,
        'monthly_trend': monthly_transfers,
        'top_source_warehouses': source_warehouse_stats,
        'top_source_branches': source_branch_stats,
        'top_destination_warehouses': dest_warehouse_stats,
        'top_destination_branches': dest_branch_stats,
    }


def get_warehouse_stock_evaluation(warehouse: Warehouse, dead_stock_period: str = '90d', slow_stock_period: str = '30d') -> Dict:
    """Get stock evaluation data for a specific warehouse."""
    # Stock evaluation aggregates
    stock_aggregates = (
        StockEntry.objects.filter(warehouse=warehouse)
        .aggregate(
            total_stock_entries=Count('id'),
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost_value=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            unique_products=Count('product', distinct=True),
        )
    )
    
    # Stock by category
    stock_by_category = list(
        StockEntry.objects.filter(warehouse=warehouse)
        .values('product__category__id', 'product__category__name')
        .annotate(
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            product_count=Count('product', distinct=True),
        )
        .order_by('-total_cost')
    )
    
    category_stock = [
        {
            'category_id': entry['product__category__id'],
            'category_name': entry['product__category__name'] or 'Uncategorized',
            'total_quantity': entry['total_quantity'],
            'total_cost': entry['total_cost'],
            'product_count': entry['product_count'],
        }
        for entry in stock_by_category
    ]
    
    # Low stock items (based on reorder_level)
    low_stock_items = list(
        StockEntry.objects.filter(
            warehouse=warehouse,
            quantity__lte=F('reorder_level')
        )
        .select_related('product', 'product__category')
        .order_by('quantity')[:20]
    )
    
    low_stock = [
        {
            'product_id': item.product.id,
            'product_name': item.product.name,
            'product_sku': item.product.sku,
            'category_name': item.product.category.name if item.product.category else 'Uncategorized',
            'current_quantity': item.quantity,
            'reorder_level': item.reorder_level,
            'purchase_price': item.purchase_price,
            'batch_number': item.batch_number,
        }
        for item in low_stock_items
    ]
    
    # Top products by value
    top_products_by_value = list(
        StockEntry.objects.filter(warehouse=warehouse)
        .values('product_id', 'product__name', 'product__sku')
        .annotate(
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
        )
        .order_by('-total_cost')[:10]
    )
    
    top_products = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'total_quantity': entry['total_quantity'],
            'total_cost': entry['total_cost'],
        }
        for entry in top_products_by_value
    ]
    
    # Recent stock additions
    thirty_days_ago = timezone.now() - timedelta(days=30)
    recent_stock = list(
        StockEntry.objects.filter(
            warehouse=warehouse,
            created_at__gte=thirty_days_ago
        )
        .select_related('product', 'product__category')
        .order_by('-created_at')[:10]
    )
    
    recent_additions = [
        {
            'product_id': item.product.id,
            'product_name': item.product.name,
            'product_sku': item.product.sku,
            'quantity': item.quantity,
            'purchase_price': item.purchase_price,
            'batch_number': item.batch_number,
            'received_date': item.received_date,
            'created_at': item.created_at,
        }
        for item in recent_stock
    ]
    
    # Stock summary by product
    product_stock_summary = list(
        StockEntry.objects.filter(warehouse=warehouse)
        .values('product_id', 'product__name', 'product__sku', 'product__category__name')
        .annotate(
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            batch_count=Count('id', distinct=True),
        )
        .order_by('-total_cost')
    )
    
    stock_summary = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'category_name': entry['product__category__name'] or 'Uncategorized',
            'total_quantity': entry['total_quantity'],
            'total_cost': entry['total_cost'],
            'batch_count': entry['batch_count'],
        }
        for entry in product_stock_summary
    ]
    
    # Dead stock analysis (no movement)
    if dead_stock_period not in DEAD_STOCK_PERIODS:
        dead_stock_period = '90d'
    
    period_delta = DEAD_STOCK_PERIODS[dead_stock_period]
    minimum_sales = DEAD_STOCK_MIN_SALES.get(dead_stock_period, Decimal('1'))
    cutoff = timezone.now() - period_delta
    
    # Get products with stock in this warehouse
    warehouse_products = Product.objects.filter(
        stock_entries__warehouse=warehouse
    ).distinct()
    
    dead_stock_queryset = (
        warehouse_products.annotate(
            warehouse_qty=Coalesce(
                Sum('stock_entries__quantity', filter=Q(stock_entries__warehouse=warehouse)),
                Decimal('0')
            ),
            dead_period_sales=Coalesce(
                Sum(
                    'sale_items__quantity',
                    filter=Q(sale_items__sale__created_at__gte=cutoff),
                ),
                Decimal('0'),
            )
        )
        .filter(warehouse_qty__gt=0)
        .filter(dead_period_sales__lt=minimum_sales)
        .order_by('dead_period_sales', 'name')[:25]
    )
    
    dead_stock_items = []
    last_sale_map = _get_last_sale_map([p.id for p in dead_stock_queryset])
    now = timezone.now()
    
    for product in dead_stock_queryset:
        warehouse_qty = product.warehouse_qty or Decimal('0')
        last_sale_item = last_sale_map.get(product.id)
        last_sold_at = last_sale_item.sale.created_at if last_sale_item else None
        days_since_last_sale = (now - last_sold_at).days if last_sold_at else None
        
        dead_stock_items.append({
            'product_id': product.id,
            'product_name': product.name,
            'product_sku': product.sku,
            'warehouse_stock': warehouse_qty,
            'last_sold_at': last_sold_at,
            'days_since_last_sale': days_since_last_sale,
            'sold_in_period': product.dead_period_sales,
        })
    
    # Slow moving stock analysis
    if slow_stock_period not in DEAD_STOCK_PERIODS:
        slow_stock_period = '30d'
    
    slow_period_delta = DEAD_STOCK_PERIODS[slow_stock_period]
    min_sales = DEAD_STOCK_MIN_SALES.get(slow_stock_period, Decimal('1'))
    max_sales = SLOW_STOCK_MAX_SALES.get(slow_stock_period, min_sales * 2)
    slow_cutoff = timezone.now() - slow_period_delta
    
    slow_stock_queryset = (
        warehouse_products.annotate(
            warehouse_qty=Coalesce(
                Sum('stock_entries__quantity', filter=Q(stock_entries__warehouse=warehouse)),
                Decimal('0')
            ),
            slow_period_sales=Coalesce(
                Sum(
                    'sale_items__quantity',
                    filter=Q(sale_items__sale__created_at__gte=slow_cutoff),
                ),
                Decimal('0'),
            )
        )
        .filter(warehouse_qty__gt=0)
        .filter(slow_period_sales__gte=min_sales)
        .filter(slow_period_sales__lt=max_sales)
        .order_by('slow_period_sales', 'name')[:25]
    )
    
    slow_stock_items = []
    slow_last_sale_map = _get_last_sale_map([p.id for p in slow_stock_queryset])
    
    for product in slow_stock_queryset:
        warehouse_qty = product.warehouse_qty or Decimal('0')
        last_sale_item = slow_last_sale_map.get(product.id)
        last_sold_at = last_sale_item.sale.created_at if last_sale_item else None
        days_since_last_sale = (now - last_sold_at).days if last_sold_at else None
        
        slow_stock_items.append({
            'product_id': product.id,
            'product_name': product.name,
            'product_sku': product.sku,
            'warehouse_stock': warehouse_qty,
            'last_sold_at': last_sold_at,
            'days_since_last_sale': days_since_last_sale,
            'sold_in_period': product.slow_period_sales,
        })
    
    return {
        'stock_evaluation': {
            'total_stock_entries': stock_aggregates['total_stock_entries'],
            'total_quantity': stock_aggregates['total_quantity'],
            'total_cost_value': stock_aggregates['total_cost_value'],
            'unique_products': stock_aggregates['unique_products'],
        },
        'stock_by_category': category_stock,
        'low_stock_items': low_stock,
        'top_products_by_value': top_products,
        'recent_stock_additions': recent_additions,
        'stock_summary': stock_summary,
        'dead_stock': {
            'selected_period': dead_stock_period,
            'cutoff_datetime': cutoff,
            'minimum_sales_required': minimum_sales,
            'items': dead_stock_items,
        },
        'slow_moving_stock': {
            'selected_period': slow_stock_period,
            'cutoff_datetime': slow_cutoff,
            'minimum_sales_threshold': min_sales,
            'maximum_sales_threshold': max_sales,
            'items': slow_stock_items,
        },
    }


def get_branch_stock_evaluation(branch: Branch) -> Dict:
    """Get stock evaluation data for a specific branch."""
    # Stock evaluation aggregates
    stock_aggregates = (
        BranchStock.objects.filter(branch=branch)
        .aggregate(
            total_stock_entries=Count('id'),
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost_value=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            total_selling_value=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * Coalesce(F('selling_price'), Decimal('0')),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            unique_products=Count('product', distinct=True),
        )
    )
    
    potential_profit = stock_aggregates['total_selling_value'] - stock_aggregates['total_cost_value']
    profit_margin = (
        (potential_profit / stock_aggregates['total_selling_value'] * 100)
        if stock_aggregates['total_selling_value'] > 0
        else Decimal('0')
    )
    
    # Stock by category
    stock_by_category = list(
        BranchStock.objects.filter(branch=branch)
        .values('product__category__id', 'product__category__name')
        .annotate(
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            total_selling_value=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * Coalesce(F('selling_price'), Decimal('0')),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            product_count=Count('product', distinct=True),
        )
        .order_by('-total_cost')
    )
    
    category_stock = [
        {
            'category_id': entry['product__category__id'],
            'category_name': entry['product__category__name'] or 'Uncategorized',
            'total_quantity': entry['total_quantity'],
            'total_cost': entry['total_cost'],
            'total_selling_value': entry['total_selling_value'],
            'product_count': entry['product_count'],
        }
        for entry in stock_by_category
    ]
    
    # Low stock items
    low_stock_items = list(
        BranchStock.objects.filter(
            branch=branch,
            quantity__lte=F('reorder_level')
        )
        .select_related('product', 'product__category')
        .order_by('quantity')[:20]
    )
    
    low_stock = [
        {
            'product_id': item.product.id,
            'product_name': item.product.name,
            'product_sku': item.product.sku,
            'category_name': item.product.category.name if item.product.category else 'Uncategorized',
            'current_quantity': item.quantity,
            'reorder_level': item.reorder_level,
            'purchase_price': item.purchase_price,
            'selling_price': item.selling_price,
            'batch_number': item.batch_number,
        }
        for item in low_stock_items
    ]
    
    # Top products by value
    top_products_by_value = list(
        BranchStock.objects.filter(branch=branch)
        .values('product_id', 'product__name', 'product__sku')
        .annotate(
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
        )
        .order_by('-total_cost')[:10]
    )
    
    top_products = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'total_quantity': entry['total_quantity'],
            'total_cost': entry['total_cost'],
        }
        for entry in top_products_by_value
    ]
    
    # Recent stock additions
    thirty_days_ago = timezone.now() - timedelta(days=30)
    recent_stock = list(
        BranchStock.objects.filter(
            branch=branch,
            created_at__gte=thirty_days_ago
        )
        .select_related('product', 'product__category')
        .order_by('-created_at')[:10]
    )
    
    recent_additions = [
        {
            'product_id': item.product.id,
            'product_name': item.product.name,
            'product_sku': item.product.sku,
            'quantity': item.quantity,
            'purchase_price': item.purchase_price,
            'selling_price': item.selling_price,
            'batch_number': item.batch_number,
            'received_date': item.received_date,
            'created_at': item.created_at,
        }
        for item in recent_stock
    ]
    
    # Stock summary by product
    product_stock_summary = list(
        BranchStock.objects.filter(branch=branch)
        .values('product_id', 'product__name', 'product__sku', 'product__category__name')
        .annotate(
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            batch_count=Count('id', distinct=True),
        )
        .order_by('-total_cost')
    )
    
    stock_summary = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'category_name': entry['product__category__name'] or 'Uncategorized',
            'total_quantity': entry['total_quantity'],
            'total_cost': entry['total_cost'],
            'batch_count': entry['batch_count'],
        }
        for entry in product_stock_summary
    ]
    
    # Branch-specific dead and slow stock analysis
    branch_products = Product.objects.filter(
        branch_stock_entries__branch=branch
    ).distinct()

    now = timezone.now()

    dead_period_key = '90d'
    dead_cutoff = now - DEAD_STOCK_PERIODS.get(dead_period_key, timedelta(days=90))
    dead_min_sales = DEAD_STOCK_MIN_SALES.get(dead_period_key, Decimal('1'))

    dead_stock_queryset = (
        branch_products.annotate(
            branch_qty=Coalesce(
                Sum(
                    'branch_stock_entries__quantity',
                    filter=Q(branch_stock_entries__branch=branch)
                ),
                Decimal('0')
            ),
            dead_period_sales=Coalesce(
                Sum(
                    'sale_items__quantity',
                    filter=Q(
                        sale_items__sale__branch=branch,
                        sale_items__sale__created_at__gte=dead_cutoff,
                    ),
                ),
                Decimal('0')
            )
        )
        .filter(branch_qty__gt=0)
        .filter(dead_period_sales__lt=dead_min_sales)
        .order_by('dead_period_sales', 'name')[:25]
    )

    dead_last_sale_map = _get_last_sale_map(
        [product.id for product in dead_stock_queryset],
        branch_id=branch.id
    )
    dead_stock_items = []
    for product in dead_stock_queryset:
        branch_qty = product.branch_qty or Decimal('0')
        last_sale_item = dead_last_sale_map.get(product.id)
        last_sold_at = last_sale_item.sale.created_at if last_sale_item else None
        days_since_last_sale = (now - last_sold_at).days if last_sold_at else None
        dead_stock_items.append({
            'product_id': product.id,
            'product_name': product.name,
            'product_sku': product.sku,
            'branch_stock': branch_qty,
            'last_sold_at': last_sold_at,
            'days_since_last_sale': days_since_last_sale,
            'sold_in_period': product.dead_period_sales,
        })

    slow_period_key = '30d'
    slow_cutoff = now - DEAD_STOCK_PERIODS.get(slow_period_key, timedelta(days=30))
    slow_min_sales = DEAD_STOCK_MIN_SALES.get(slow_period_key, Decimal('1'))
    slow_max_sales = SLOW_STOCK_MAX_SALES.get(slow_period_key, slow_min_sales * 2)

    slow_stock_queryset = (
        branch_products.annotate(
            branch_qty=Coalesce(
                Sum(
                    'branch_stock_entries__quantity',
                    filter=Q(branch_stock_entries__branch=branch)
                ),
                Decimal('0')
            ),
            slow_period_sales=Coalesce(
                Sum(
                    'sale_items__quantity',
                    filter=Q(
                        sale_items__sale__branch=branch,
                        sale_items__sale__created_at__gte=slow_cutoff,
                    ),
                ),
                Decimal('0')
            )
        )
        .filter(branch_qty__gt=0)
        .filter(slow_period_sales__gte=slow_min_sales)
        .filter(slow_period_sales__lt=slow_max_sales)
        .order_by('slow_period_sales', 'name')[:25]
    )

    slow_last_sale_map = _get_last_sale_map(
        [product.id for product in slow_stock_queryset],
        branch_id=branch.id
    )
    slow_stock_items = []
    for product in slow_stock_queryset:
        branch_qty = product.branch_qty or Decimal('0')
        last_sale_item = slow_last_sale_map.get(product.id)
        last_sold_at = last_sale_item.sale.created_at if last_sale_item else None
        days_since_last_sale = (now - last_sold_at).days if last_sold_at else None
        slow_stock_items.append({
            'product_id': product.id,
            'product_name': product.name,
            'product_sku': product.sku,
            'branch_stock': branch_qty,
            'last_sold_at': last_sold_at,
            'days_since_last_sale': days_since_last_sale,
            'sold_in_period': product.slow_period_sales,
        })

    return {
        'stock_evaluation': {
            'total_stock_entries': stock_aggregates['total_stock_entries'],
            'total_quantity': stock_aggregates['total_quantity'],
            'total_cost_value': stock_aggregates['total_cost_value'],
            'total_selling_value': stock_aggregates['total_selling_value'],
            'potential_profit': potential_profit,
            'profit_margin_percentage': profit_margin,
            'unique_products': stock_aggregates['unique_products'],
        },
        'stock_by_category': category_stock,
        'low_stock_items': low_stock,
        'top_products_by_value': top_products,
        'recent_stock_additions': recent_additions,
        'stock_summary': stock_summary,
        'dead_stock': {
            'selected_period': dead_period_key,
            'cutoff_datetime': dead_cutoff,
            'minimum_sales_required': dead_min_sales,
            'items': dead_stock_items,
        },
        'slow_moving_stock': {
            'selected_period': slow_period_key,
            'cutoff_datetime': slow_cutoff,
            'minimum_sales_threshold': slow_min_sales,
            'maximum_sales_threshold': slow_max_sales,
            'items': slow_stock_items,
        },
    }


def get_sales_trends_charts(start_date: datetime = None, end_date: datetime = None, branch_id: int = None) -> Dict:
    """Get sales trends charts data with various metrics."""
    now = timezone.now()
    if not start_date:
        start_date = now - timedelta(days=30)
    if not end_date:
        end_date = now
    
    base_queryset = Sale.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
        status='completed'
    )
    
    if branch_id:
        base_queryset = base_queryset.filter(branch_id=branch_id)
    
    net_revenue_expr = ExpressionWrapper(
        F('total_amount') - F('discount') + F('tax'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    cost_expr = ExpressionWrapper(
        F('items__purchase_price') * F('items__quantity'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    # Daily sales volume and revenue
    daily_sales = list(
        base_queryset.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(
            transaction_count=Count('id'),
            total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
            total_items_sold=Coalesce(Sum('items__quantity'), Decimal('0')),
            total_cost=Coalesce(Sum(cost_expr), Decimal('0')),
        )
        .order_by('day')
    )
    
    # Weekly sales trends
    weekly_sales = list(
        base_queryset.annotate(week=TruncWeek('created_at'))
        .values('week')
        .annotate(
            transaction_count=Count('id'),
            total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
            total_items_sold=Coalesce(Sum('items__quantity'), Decimal('0')),
            total_cost=Coalesce(Sum(cost_expr), Decimal('0')),
        )
        .order_by('week')
    )
    
    # Sales by payment method
    sales_by_payment = list(
        base_queryset.values('type_of_payment')
        .annotate(
            transaction_count=Count('id'),
            total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
        )
        .order_by('-total_revenue')
    )
    
    payment_method_stats = [
        {
            'payment_method': entry['type_of_payment'],
            'transaction_count': entry['transaction_count'],
            'total_revenue': entry['total_revenue'],
        }
        for entry in sales_by_payment
    ]
    
    # Sales by branch
    sales_by_branch = list(
        base_queryset.values('branch__id', 'branch__name')
        .annotate(
            transaction_count=Count('id'),
            total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
            total_items_sold=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('-total_revenue')
    )
    
    branch_sales_stats = [
        {
            'branch_id': entry['branch__id'],
            'branch_name': entry['branch__name'],
            'transaction_count': entry['transaction_count'],
            'total_revenue': entry['total_revenue'],
            'total_items_sold': entry['total_items_sold'],
        }
        for entry in sales_by_branch
    ]
    
    # Top selling products
    product_cost_expr = ExpressionWrapper(
        F('purchase_price') * F('quantity'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    top_selling_products = list(
        SaleItem.objects.filter(
            sale__created_at__gte=start_date,
            sale__created_at__lte=end_date,
            sale__status='completed'
        )
        .values('product_id', 'product__name', 'product__sku')
        .annotate(
            total_quantity_sold=Coalesce(Sum('quantity'), Decimal('0')),
            total_revenue=Coalesce(Sum('subtotal'), Decimal('0')),
            total_cost=Coalesce(Sum(product_cost_expr), Decimal('0')),
        )
        .order_by('-total_quantity_sold')[:20]
    )
    
    top_products = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'total_quantity_sold': entry['total_quantity_sold'],
            'total_revenue': entry['total_revenue'],
            'total_cost': entry['total_cost'],
            'profit': entry['total_revenue'] - entry['total_cost'],
        }
        for entry in top_selling_products
    ]
    
    return {
        'daily_sales_trend': daily_sales,
        'weekly_sales_trend': weekly_sales,
        'sales_by_payment_method': payment_method_stats,
        'sales_by_branch': branch_sales_stats,
        'top_selling_products': top_products,
        'date_range': {
            'start_date': start_date,
            'end_date': end_date,
        },
    }


def get_stock_movement_data(start_date: datetime = None, end_date: datetime = None, warehouse_id: int = None, branch_id: int = None) -> Dict:
    """Get stock movement data including additions, removals, and transfers."""
    now = timezone.now()
    if not start_date:
        start_date = now - timedelta(days=30)
    if not end_date:
        end_date = now
    
    # Stock adjustments (additions, removals, corrections)
    adjustments_queryset = StockAdjustment.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date
    )
    
    if warehouse_id:
        adjustments_queryset = adjustments_queryset.filter(warehouse_id=warehouse_id)
    
    # Adjustments by type
    adjustments_by_type = list(
        adjustments_queryset.values('adjustment_type')
        .annotate(
            count=Count('id'),
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
        )
        .order_by('-count')
    )
    
    adjustment_stats = [
        {
            'adjustment_type': entry['adjustment_type'],
            'count': entry['count'],
            'total_quantity': abs(entry['total_quantity']),  # Absolute value for display
        }
        for entry in adjustments_by_type
    ]
    
    # Daily stock movements
    daily_movements = list(
        adjustments_queryset.annotate(day=TruncDate('created_at'))
        .values('day', 'adjustment_type')
        .annotate(
            count=Count('id'),
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
        )
        .order_by('day', 'adjustment_type')
    )
    
    # Stock entries (warehouse)
    warehouse_entries_queryset = StockEntry.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date
    )
    
    if warehouse_id:
        warehouse_entries_queryset = warehouse_entries_queryset.filter(warehouse_id=warehouse_id)
    
    warehouse_entries = list(
        warehouse_entries_queryset.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(
            entry_count=Count('id'),
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
        )
        .order_by('day')
    )
    
    # Branch stock entries
    branch_entries_queryset = BranchStock.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date
    )
    
    if branch_id:
        branch_entries_queryset = branch_entries_queryset.filter(branch_id=branch_id)
    
    branch_entries = list(
        branch_entries_queryset.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(
            entry_count=Count('id'),
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
        )
        .order_by('day')
    )
    
    # Stock transfers
    transfers_queryset = StockTransfer.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date
    )
    
    if warehouse_id:
        transfers_queryset = transfers_queryset.filter(
            Q(source_warehouse_id=warehouse_id) | Q(destination_warehouse_id=warehouse_id)
        )
    if branch_id:
        transfers_queryset = transfers_queryset.filter(
            Q(source_branch_id=branch_id) | Q(destination_branch_id=branch_id)
        )
    
    transfers_by_type = list(
        transfers_queryset.values('transfer_type', 'status')
        .annotate(
            count=Count('id'),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('-count')
    )
    
    transfer_stats = [
        {
            'transfer_type': entry['transfer_type'],
            'status': entry['status'],
            'count': entry['count'],
            'total_quantity': entry['total_quantity'],
        }
        for entry in transfers_by_type
    ]
    
    # Daily transfers
    daily_transfers = list(
        transfers_queryset.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(
            count=Count('id'),
            total_quantity=Coalesce(Sum('items__quantity'), Decimal('0')),
        )
        .order_by('day')
    )
    
    return {
        'adjustments_by_type': adjustment_stats,
        'daily_adjustments': daily_movements,
        'warehouse_entries': warehouse_entries,
        'branch_entries': branch_entries,
        'transfers_by_type': transfer_stats,
        'daily_transfers': daily_transfers,
        'date_range': {
            'start_date': start_date,
            'end_date': end_date,
        },
    }


def get_sales_report(start_date: datetime = None, end_date: datetime = None, branch_id: int = None, 
                    product_id: int = None, payment_method: str = None, cashier_id: int = None) -> Dict:
    """Get detailed sales report with filters."""
    now = timezone.now()
    if not start_date:
        start_date = now - timedelta(days=30)
    if not end_date:
        end_date = now
    
    base_queryset = Sale.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
        status='completed'
    )
    
    if branch_id:
        base_queryset = base_queryset.filter(branch_id=branch_id)
    
    if payment_method:
        base_queryset = base_queryset.filter(type_of_payment=payment_method)
    
    if cashier_id:
        base_queryset = base_queryset.filter(cashier_id=cashier_id)
    
    net_revenue_expr = ExpressionWrapper(
        F('total_amount') - F('discount') + F('tax'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    # Overall summary
    summary = base_queryset.aggregate(
        total_transactions=Count('id'),
        total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
        total_discount=Coalesce(Sum('discount'), Decimal('0')),
        total_tax=Coalesce(Sum('tax'), Decimal('0')),
        total_items_sold=Coalesce(Sum('items__quantity'), Decimal('0')),
        total_cost=Coalesce(
            Sum(
                ExpressionWrapper(
                    F('items__purchase_price') * F('items__quantity'),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            ),
            Decimal('0')
        ),
    )
    
    total_profit = summary['total_revenue'] - summary['total_cost']
    profit_margin = (
        (total_profit / summary['total_revenue'] * 100)
        if summary['total_revenue'] > 0
        else Decimal('0')
    )
    
    summary['total_profit'] = total_profit
    summary['profit_margin_percentage'] = profit_margin
    
    # Detailed sales list
    sales_list = list(
        base_queryset.select_related('branch', 'cashier')
        .prefetch_related('items')
        .order_by('-created_at')[:100]
    )
    
    detailed_sales = []
    for sale in sales_list:
        items_data = []
        for item in sale.items.all():
            items_data.append({
                'product_id': item.product.id,
                'product_name': item.product.name,
                'product_sku': item.product.sku,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'purchase_price': item.purchase_price,
                'subtotal': item.subtotal,
            })
        
        detailed_sales.append({
            'sale_id': sale.id,
            'sale_number': sale.sale_number,
            'branch_id': sale.branch.id if sale.branch else None,
            'branch_name': sale.branch.name if sale.branch else None,
            'cashier_id': sale.cashier.id if sale.cashier else None,
            'cashier_email': sale.cashier.email if sale.cashier else None,
            'total_amount': sale.total_amount,
            'discount': sale.discount,
            'tax': sale.tax,
            'net_amount': sale.net_amount,
            'type_of_payment': sale.type_of_payment,
            'created_at': sale.created_at,
            'items': items_data,
        })
    
    # Sales by product
    if product_id:
        product_sales = list(
            SaleItem.objects.filter(
                sale__created_at__gte=start_date,
                sale__created_at__lte=end_date,
                sale__status='completed',
                product_id=product_id
            )
            .values('sale__created_at', 'sale__sale_number', 'sale__branch__name')
            .annotate(
                quantity=Sum('quantity'),
                revenue=Sum('subtotal'),
            )
            .order_by('-sale__created_at')[:50]
        )
    else:
        product_sales = []
    
    return {
        'summary': summary,
        'detailed_sales': detailed_sales,
        'product_sales': product_sales,
        'filters': {
            'start_date': start_date,
            'end_date': end_date,
            'branch_id': branch_id,
            'product_id': product_id,
            'payment_method': payment_method,
            'cashier_id': cashier_id,
        },
    }


def get_stock_report(warehouse_id: int = None, branch_id: int = None, product_id: int = None, 
                    category_id: int = None, low_stock_only: bool = False) -> Dict:
    """Get detailed stock report with various filters."""
    # Warehouse stock
    warehouse_stock_queryset = StockEntry.objects.all()
    if warehouse_id:
        warehouse_stock_queryset = warehouse_stock_queryset.filter(warehouse_id=warehouse_id)
    if product_id:
        warehouse_stock_queryset = warehouse_stock_queryset.filter(product_id=product_id)
    if category_id:
        warehouse_stock_queryset = warehouse_stock_queryset.filter(product__category_id=category_id)
    
    warehouse_stock = list(
        warehouse_stock_queryset.values(
            'product_id', 'product__name', 'product__sku', 'product__category__name',
            'warehouse_id', 'warehouse__name'
        )
        .annotate(
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            batch_count=Count('id', distinct=True),
        )
        .order_by('-total_quantity')
    )
    
    warehouse_stock_list = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'category_name': entry['product__category__name'] or 'Uncategorized',
            'warehouse_id': entry['warehouse_id'],
            'warehouse_name': entry['warehouse__name'],
            'total_quantity': entry['total_quantity'],
            'total_cost': entry['total_cost'],
            'batch_count': entry['batch_count'],
        }
        for entry in warehouse_stock
    ]
    
    # Branch stock
    branch_stock_queryset = BranchStock.objects.all()
    if branch_id:
        branch_stock_queryset = branch_stock_queryset.filter(branch_id=branch_id)
    if product_id:
        branch_stock_queryset = branch_stock_queryset.filter(product_id=product_id)
    if category_id:
        branch_stock_queryset = branch_stock_queryset.filter(product__category_id=category_id)
    if low_stock_only:
        branch_stock_queryset = branch_stock_queryset.filter(quantity__lte=F('reorder_level'))
    
    branch_stock = list(
        branch_stock_queryset.values(
            'product_id', 'product__name', 'product__sku', 'product__category__name',
            'branch_id', 'branch__name', 'reorder_level'
        )
        .annotate(
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * F('purchase_price'),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            total_selling_value=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('quantity') * Coalesce(F('selling_price'), Decimal('0')),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                Decimal('0')
            ),
            batch_count=Count('id', distinct=True),
        )
        .order_by('-total_quantity')
    )
    
    branch_stock_list = [
        {
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'category_name': entry['product__category__name'] or 'Uncategorized',
            'branch_id': entry['branch_id'],
            'branch_name': entry['branch__name'],
            'total_quantity': entry['total_quantity'],
            'reorder_level': entry['reorder_level'],
            'total_cost': entry['total_cost'],
            'total_selling_value': entry['total_selling_value'],
            'potential_profit': entry['total_selling_value'] - entry['total_cost'],
            'batch_count': entry['batch_count'],
            'is_low_stock': entry['total_quantity'] <= entry['reorder_level'],
        }
        for entry in branch_stock
    ]
    
    # Summary statistics
    warehouse_summary = {
        'total_products': len(set(entry['product_id'] for entry in warehouse_stock)),
        'total_quantity': sum(entry['total_quantity'] for entry in warehouse_stock_list),
        'total_cost': sum(entry['total_cost'] for entry in warehouse_stock_list),
    }
    
    branch_summary = {
        'total_products': len(set(entry['product_id'] for entry in branch_stock)),
        'total_quantity': sum(entry['total_quantity'] for entry in branch_stock_list),
        'total_cost': sum(entry['total_cost'] for entry in branch_stock_list),
        'total_selling_value': sum(entry['total_selling_value'] for entry in branch_stock_list),
        'low_stock_count': len([entry for entry in branch_stock_list if entry['is_low_stock']]),
    }
    
    return {
        'warehouse_stock': warehouse_stock_list,
        'branch_stock': branch_stock_list,
        'warehouse_summary': warehouse_summary,
        'branch_summary': branch_summary,
        'filters': {
            'warehouse_id': warehouse_id,
            'branch_id': branch_id,
            'product_id': product_id,
            'category_id': category_id,
            'low_stock_only': low_stock_only,
        },
    }


def get_auditor_dashboard_data() -> Dict:
    """Get auditor dashboard data with system-wide overview."""
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    seven_days_ago = now - timedelta(days=7)
    
    # System summary
    total_branches = Branch.objects.count()
    total_warehouses = Warehouse.objects.count()
    total_products = Product.objects.count()
    total_users = User.objects.count()
    active_users = User.objects.filter(account_status='active').count()
    
    # Recent audit logs
    recent_audit_logs = list(
        AuditLog.objects.all()
        .select_related('user')
        .order_by('-timestamp')[:50]
    )
    
    audit_logs_summary = []
    for log in recent_audit_logs:
        audit_logs_summary.append({
            'id': log.id,
            'activity_type': log.activity_type,
            'user_email': log.user.email if log.user else 'System',
            'description': log.description,
            'timestamp': log.timestamp,
            'ip_address': log.ip_address,
            'related_model': log.related_model,
            'related_object_id': log.related_object_id,
        })
    
    # Audit activity by type (last 30 days)
    activity_by_type = list(
        AuditLog.objects.filter(timestamp__gte=thirty_days_ago)
        .values('activity_type')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )
    
    # Recent sales activity
    recent_sales = Sale.objects.filter(created_at__gte=seven_days_ago).count()
    total_sales = Sale.objects.count()
    
    # Recent stock movements
    recent_stock_entries = StockEntry.objects.filter(created_at__gte=seven_days_ago).count()
    recent_stock_transfers = StockTransfer.objects.filter(created_at__gte=seven_days_ago).count()
    
    # User activity (last 7 days)
    active_users_recent = User.objects.filter(
        audit_logs__timestamp__gte=seven_days_ago
    ).distinct().count()
    
    # Financial summary (last 30 days)
    net_revenue_expr = ExpressionWrapper(
        F('total_amount') - F('discount') + F('tax'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    financial_summary = Sale.objects.filter(
        created_at__gte=thirty_days_ago,
        status='completed'
    ).aggregate(
        total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
        total_transactions=Count('id'),
        total_discount=Coalesce(Sum('discount'), Decimal('0')),
        total_tax=Coalesce(Sum('tax'), Decimal('0')),
    )
    
    return {
        'system_summary': {
            'total_branches': total_branches,
            'total_warehouses': total_warehouses,
            'total_products': total_products,
            'total_users': total_users,
            'active_users': active_users,
            'active_users_recent': active_users_recent,
        },
        'recent_activity': {
            'recent_sales': recent_sales,
            'total_sales': total_sales,
            'recent_stock_entries': recent_stock_entries,
            'recent_stock_transfers': recent_stock_transfers,
        },
        'financial_summary_30d': financial_summary,
        'audit_logs': audit_logs_summary,
        'activity_by_type': activity_by_type,
    }


def get_auditor_reports(
    start_date: datetime = None,
    end_date: datetime = None,
    branch_id: int = None,
    warehouse_id: int = None,
    user_id: int = None,
    activity_type: str = None,
) -> Dict:
    """Get comprehensive auditor reports across all branches and warehouses."""
    now = timezone.now()
    if not start_date:
        start_date = now - timedelta(days=30)
    if not end_date:
        end_date = now
    
    # Audit logs with filters
    audit_logs_queryset = AuditLog.objects.filter(
        timestamp__gte=start_date,
        timestamp__lte=end_date,
    )
    
    if user_id:
        audit_logs_queryset = audit_logs_queryset.filter(user_id=user_id)
    if activity_type:
        audit_logs_queryset = audit_logs_queryset.filter(activity_type=activity_type)
    
    audit_logs = list(
        audit_logs_queryset.select_related('user')
        .order_by('-timestamp')[:500]
    )
    
    audit_logs_data = [
        {
            'id': log.id,
            'activity_type': log.activity_type,
            'user_email': log.user.email if log.user else 'System',
            'user_id': log.user.id if log.user else None,
            'description': log.description,
            'metadata': log.metadata,
            'timestamp': log.timestamp,
            'ip_address': log.ip_address,
            'user_agent': log.user_agent,
            'related_model': log.related_model,
            'related_object_id': log.related_object_id,
        }
        for log in audit_logs
    ]
    
    # Sales reports (all branches or filtered)
    sales_report_data = get_sales_report(
        start_date=start_date,
        end_date=end_date,
        branch_id=branch_id,
        product_id=None,
        payment_method=None,
        cashier_id=None,
    )
    
    # Stock reports (all warehouses/branches or filtered)
    stock_report_data = get_stock_report(
        warehouse_id=warehouse_id,
        branch_id=branch_id,
        product_id=None,
        category_id=None,
        low_stock_only=False,
    )
    
    # Stock movement data
    stock_movement_data = get_stock_movement_data(
        start_date=start_date,
        end_date=end_date,
        warehouse_id=warehouse_id,
        branch_id=branch_id,
    )
    
    # Sales trends
    sales_trends_data = get_sales_trends_charts(
        start_date=start_date,
        end_date=end_date,
        branch_id=branch_id,
    )
    
    # User activity summary
    user_activity = list(
        AuditLog.objects.filter(
            timestamp__gte=start_date,
            timestamp__lte=end_date,
            user__isnull=False,
        )
        .values('user_id', 'user__email')
        .annotate(
            activity_count=Count('id'),
            last_activity=Max('timestamp'),
        )
        .order_by('-activity_count')[:20]
    )
    
    user_activity_data = [
        {
            'user_id': entry['user_id'],
            'user_email': entry['user__email'],
            'activity_count': entry['activity_count'],
            'last_activity': entry['last_activity'],
        }
        for entry in user_activity
    ]
    
    # Activity type summary
    activity_type_summary = list(
        audit_logs_queryset.values('activity_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    
    # Sales summary
    sales_queryset = Sale.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
    )
    if branch_id:
        sales_queryset = sales_queryset.filter(branch_id=branch_id)
    
    net_revenue_expr = ExpressionWrapper(
        F('total_amount') - F('discount') + F('tax'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    sales_summary = sales_queryset.aggregate(
        total_sales=Count('id'),
        completed_sales=Count('id', filter=Q(status='completed')),
        cancelled_sales=Count('id', filter=Q(status='cancelled')),
        returned_sales=Count('id', filter=Q(status='returned')),
        pending_sales=Count('id', filter=Q(status='pending')),
        total_revenue=Coalesce(Sum(net_revenue_expr, filter=Q(status='completed')), Decimal('0')),
        total_amount=Coalesce(Sum('total_amount', filter=Q(status='completed')), Decimal('0')),
        total_discount=Coalesce(Sum('discount', filter=Q(status='completed')), Decimal('0')),
        total_tax=Coalesce(Sum('tax', filter=Q(status='completed')), Decimal('0')),
        total_items_sold=Coalesce(Sum('items__quantity', filter=Q(status='completed')), Decimal('0')),
    )
    
    # Payments summary by payment method
    payments_summary = list(
        sales_queryset.filter(status='completed')
        .values('type_of_payment')
        .annotate(
            transaction_count=Count('id'),
            total_revenue=Coalesce(Sum(net_revenue_expr), Decimal('0')),
            total_amount=Coalesce(Sum('total_amount'), Decimal('0')),
            total_discount=Coalesce(Sum('discount'), Decimal('0')),
            total_tax=Coalesce(Sum('tax'), Decimal('0')),
        )
        .order_by('-total_revenue')
    )
    
    payments_summary_data = []
    for entry in payments_summary:
        transaction_count = entry['transaction_count'] or 0
        total_revenue = entry['total_revenue'] or Decimal('0')
        average_transaction = total_revenue / transaction_count if transaction_count > 0 else Decimal('0')
        
        payments_summary_data.append({
            'payment_method': entry['type_of_payment'],
            'transaction_count': transaction_count,
            'total_revenue': total_revenue,
            'total_amount': entry['total_amount'] or Decimal('0'),
            'total_discount': entry['total_discount'] or Decimal('0'),
            'total_tax': entry['total_tax'] or Decimal('0'),
            'average_transaction': average_transaction,
            'percentage_of_total': (
                (total_revenue / sales_summary['total_revenue'] * 100)
                if sales_summary['total_revenue'] > 0 else Decimal('0')
            ),
        })
    
    # Return summary
    returns_queryset = ProductReturn.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
    )
    if branch_id:
        returns_queryset = returns_queryset.filter(sale__branch_id=branch_id)
    
    return_summary = returns_queryset.aggregate(
        total_returns=Count('id'),
        total_quantity_returned=Coalesce(Sum('quantity'), Decimal('0')),
        total_refund_amount=Coalesce(Sum('refund_amount'), Decimal('0')),
        unique_products_returned=Count('product', distinct=True),
        unique_sales_with_returns=Count('sale', distinct=True),
    )
    
    # Return analysis
    return_analysis = list(
        returns_queryset.values(
            'product_id',
            'product__name',
            'product__sku',
            'product__category__name',
        )
        .annotate(
            return_count=Count('id'),
            total_quantity=Coalesce(Sum('quantity'), Decimal('0')),
            total_refund=Coalesce(Sum('refund_amount'), Decimal('0')),
        )
        .order_by('-total_refund')[:20]
    )
    
    return_analysis_data = []
    for entry in return_analysis:
        total_quantity = entry['total_quantity'] or Decimal('0')
        total_refund = entry['total_refund'] or Decimal('0')
        avg_refund_per_unit = total_refund / total_quantity if total_quantity > 0 else Decimal('0')
        
        return_analysis_data.append({
            'product_id': entry['product_id'],
            'product_name': entry['product__name'],
            'product_sku': entry['product__sku'],
            'category_name': entry['product__category__name'] or 'Uncategorized',
            'return_count': entry['return_count'],
            'total_quantity_returned': total_quantity,
            'total_refund_amount': total_refund,
            'average_refund_per_unit': avg_refund_per_unit,
        })
    
    # Return reasons analysis
    return_reasons = list(
        returns_queryset.exclude(reason='')
        .values('reason')
        .annotate(
            count=Count('id'),
            total_refund=Coalesce(Sum('refund_amount'), Decimal('0')),
        )
        .order_by('-count')[:10]
    )
    
    # Full sales audit report - detailed sales with all information
    sales_audit_queryset = sales_queryset.select_related('branch', 'cashier', 'branch__warehouse')
    if branch_id:
        sales_audit_queryset = sales_audit_queryset.filter(branch_id=branch_id)
    
    sales_audit_report = list(
        sales_audit_queryset.prefetch_related('items', 'returns')
        .order_by('-created_at')[:500]
    )
    
    sales_audit_data = []
    for sale in sales_audit_report:
        items_data = []
        for item in sale.items.all():
            items_data.append({
                'product_id': item.product.id,
                'product_name': item.product.name,
                'product_sku': item.product.sku,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'purchase_price': item.purchase_price,
                'discount': item.discount,
                'subtotal': item.subtotal,
                'cost': item.cost,
                'profit': item.profit,
            })
        
        returns_data = []
        for return_item in sale.returns.all():
            returns_data.append({
                'product_id': return_item.product.id,
                'product_name': return_item.product.name,
                'quantity': return_item.quantity,
                'refund_amount': return_item.refund_amount,
                'reason': return_item.reason,
                'processed_by': return_item.processed_by.email if return_item.processed_by else None,
                'created_at': return_item.created_at,
            })
        
        sales_audit_data.append({
            'sale_id': sale.id,
            'sale_number': sale.sale_number,
            'sync_id': sale.sync_id,
            'branch_id': sale.branch.id if sale.branch else None,
            'branch_name': sale.branch.name if sale.branch else None,
            'warehouse_id': sale.branch.warehouse.id if sale.branch and sale.branch.warehouse else None,
            'warehouse_name': sale.branch.warehouse.name if sale.branch and sale.branch.warehouse else None,
            'cashier_id': sale.cashier.id if sale.cashier else None,
            'cashier_email': sale.cashier.email if sale.cashier else None,
            'status': sale.status,
            'total_amount': sale.total_amount,
            'discount': sale.discount,
            'tax': sale.tax,
            'net_amount': sale.net_amount,
            'type_of_payment': sale.type_of_payment,
            'notes': sale.notes,
            'created_at': sale.created_at,
            'updated_at': sale.updated_at,
            'items': items_data,
            'returns': returns_data,
            'has_returns': len(returns_data) > 0,
        })
    
    # Stock audit report - comprehensive stock movements and changes
    stock_audit_data = {
        'stock_entries': [],
        'stock_adjustments': [],
        'stock_transfers': [],
        'branch_stock_entries': [],
    }
    
    # Warehouse stock entries
    stock_entries_queryset = StockEntry.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
    )
    if warehouse_id:
        stock_entries_queryset = stock_entries_queryset.filter(warehouse_id=warehouse_id)
    
    stock_entries = list(
        stock_entries_queryset.select_related('product', 'warehouse', 'supplier', 'created_by')
        .order_by('-created_at')[:500]
    )
    
    stock_audit_data['stock_entries'] = [
        {
            'id': entry.id,
            'product_id': entry.product.id,
            'product_name': entry.product.name,
            'product_sku': entry.product.sku,
            'warehouse_id': entry.warehouse.id,
            'warehouse_name': entry.warehouse.name,
            'supplier_id': entry.supplier.id if entry.supplier else None,
            'supplier_name': entry.supplier.name if entry.supplier else None,
            'quantity': entry.quantity,
            'purchase_price': entry.purchase_price,
            'total_cost': entry.total_cost,
            'batch_number': entry.batch_number,
            'reorder_level': entry.reorder_level,
            'received_date': entry.received_date,
            'is_initial_stock': entry.is_initial_stock,
            'created_by': entry.created_by.email if entry.created_by else None,
            'created_at': entry.created_at,
            'notes': entry.notes,
        }
        for entry in stock_entries
    ]
    
    # Stock adjustments
    adjustments_queryset = StockAdjustment.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
    )
    if warehouse_id:
        adjustments_queryset = adjustments_queryset.filter(warehouse_id=warehouse_id)
    
    stock_adjustments = list(
        adjustments_queryset.select_related('product', 'warehouse', 'created_by')
        .order_by('-created_at')[:500]
    )
    
    stock_audit_data['stock_adjustments'] = [
        {
            'id': adj.id,
            'product_id': adj.product.id,
            'product_name': adj.product.name,
            'product_sku': adj.product.sku,
            'warehouse_id': adj.warehouse.id if adj.warehouse else None,
            'warehouse_name': adj.warehouse.name if adj.warehouse else None,
            'adjustment_type': adj.adjustment_type,
            'quantity': adj.quantity,
            'reason': adj.reason,
            'created_by': adj.created_by.email if adj.created_by else None,
            'created_at': adj.created_at,
        }
        for adj in stock_adjustments
    ]
    
    # Stock transfers
    transfers_queryset = StockTransfer.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
    )
    if warehouse_id:
        transfers_queryset = transfers_queryset.filter(
            Q(source_warehouse_id=warehouse_id) | Q(destination_warehouse_id=warehouse_id)
        )
    if branch_id:
        transfers_queryset = transfers_queryset.filter(
            Q(source_branch_id=branch_id) | Q(destination_branch_id=branch_id)
        )
    
    stock_transfers = list(
        transfers_queryset.select_related(
            'source_warehouse', 'destination_warehouse',
            'source_branch', 'destination_branch',
            'created_by'
        )
        .prefetch_related('items')
        .order_by('-created_at')[:500]
    )
    
    stock_audit_data['stock_transfers'] = []
    for transfer in stock_transfers:
        items_data = []
        for item in transfer.items.all():
            items_data.append({
                'product_id': item.product.id,
                'product_name': item.product.name,
                'product_sku': item.product.sku,
                'quantity': item.quantity,
                'purchase_price': item.purchase_price,
                'selling_price': item.selling_price,
            })
        
        stock_audit_data['stock_transfers'].append({
            'id': transfer.id,
            'reference_number': transfer.reference_number,
            'transfer_type': transfer.transfer_type,
            'status': transfer.status,
            'source_warehouse_id': transfer.source_warehouse.id if transfer.source_warehouse else None,
            'source_warehouse_name': transfer.source_warehouse.name if transfer.source_warehouse else None,
            'destination_warehouse_id': transfer.destination_warehouse.id if transfer.destination_warehouse else None,
            'destination_warehouse_name': transfer.destination_warehouse.name if transfer.destination_warehouse else None,
            'source_branch_id': transfer.source_branch.id if transfer.source_branch else None,
            'source_branch_name': transfer.source_branch.name if transfer.source_branch else None,
            'destination_branch_id': transfer.destination_branch.id if transfer.destination_branch else None,
            'destination_branch_name': transfer.destination_branch.name if transfer.destination_branch else None,
            'created_by': transfer.created_by.email if transfer.created_by else None,
            'created_at': transfer.created_at,
            'items': items_data,
        })
    
    # Branch stock entries
    branch_stock_queryset = BranchStock.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
    )
    if branch_id:
        branch_stock_queryset = branch_stock_queryset.filter(branch_id=branch_id)
    
    branch_stock_entries = list(
        branch_stock_queryset.select_related('product', 'branch', 'supplier', 'created_by')
        .order_by('-created_at')[:500]
    )
    
    stock_audit_data['branch_stock_entries'] = [
        {
            'id': entry.id,
            'product_id': entry.product.id,
            'product_name': entry.product.name,
            'product_sku': entry.product.sku,
            'branch_id': entry.branch.id,
            'branch_name': entry.branch.name,
            'supplier_id': entry.supplier.id if entry.supplier else None,
            'supplier_name': entry.supplier.name if entry.supplier else None,
            'quantity': entry.quantity,
            'purchase_price': entry.purchase_price,
            'selling_price': entry.selling_price,
            'total_cost': entry.quantity * entry.purchase_price,
            'batch_number': entry.batch_number,
            'reorder_level': entry.reorder_level,
            'received_date': entry.received_date,
            'is_initial_stock': entry.is_initial_stock,
            'created_by': entry.created_by.email if entry.created_by else None,
            'created_at': entry.created_at,
            'notes': entry.notes,
        }
        for entry in branch_stock_entries
    ]
    
    return {
        'date_range': {
            'start_date': start_date,
            'end_date': end_date,
        },
        'filters': {
            'branch_id': branch_id,
            'warehouse_id': warehouse_id,
            'user_id': user_id,
            'activity_type': activity_type,
        },
        'audit_logs': audit_logs_data,
        'audit_summary': {
            'total_logs': len(audit_logs_data),
            'activity_type_summary': activity_type_summary,
            'user_activity': user_activity_data,
        },
        'sales_summary': sales_summary,
        'payments_summary': payments_summary_data,
        'return_summary': return_summary,
        'return_analysis': {
            'by_product': return_analysis_data,
            'by_reason': return_reasons,
        },
        'sales_audit_report': sales_audit_data,
        'stock_audit_report': stock_audit_data,
        'sales_report': sales_report_data,
        'stock_report': stock_report_data,
        'stock_movement': stock_movement_data,
        'sales_trends': sales_trends_data,
    }


def get_product_performance(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    branch_id: Optional[int] = None,
    limit: int = 50
) -> Dict:
    """
    Get product performance rankings by sales, category, and supplier.
    
    Returns:
    - Overall product rankings
    - Rankings by category
    - Rankings by supplier
    """
    now = timezone.now()
    if not start_date:
        start_date = now - timedelta(days=30)
    if not end_date:
        end_date = now
    
    # Base queryset for sale items
    base_queryset = SaleItem.objects.filter(
        sale__created_at__gte=start_date,
        sale__created_at__lte=end_date,
        sale__status='completed'
    )
    
    if branch_id:
        base_queryset = base_queryset.filter(sale__branch_id=branch_id)
    
    # Calculate product cost expression
    product_cost_expr = ExpressionWrapper(
        F('purchase_price') * F('quantity'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    
    # Overall Product Rankings
    overall_products = list(
        base_queryset.values(
            'product_id',
            'product__name',
            'product__sku',
            'product__category_id',
            'product__category__name'
        )
        .annotate(
            total_quantity_sold=Coalesce(Sum('quantity'), Decimal('0')),
            total_revenue=Coalesce(Sum('subtotal'), Decimal('0')),
            total_cost=Coalesce(Sum(product_cost_expr), Decimal('0')),
            transaction_count=Count('sale_id', distinct=True),
        )
        .annotate(
            total_profit=ExpressionWrapper(
                F('total_revenue') - F('total_cost'),
                output_field=DecimalField(max_digits=14, decimal_places=2)
            )
        )
        .order_by('-total_revenue')[:limit]
    )
    
    # Format overall rankings
    overall_rankings = []
    for rank, product in enumerate(overall_products, 1):
        profit_margin = (
            (product['total_profit'] / product['total_revenue'] * 100)
            if product['total_revenue'] > 0
            else Decimal('0')
        )
        overall_rankings.append({
            'rank': rank,
            'product_id': product['product_id'],
            'product_name': product['product__name'],
            'product_sku': product['product__sku'],
            'category_id': product['product__category_id'],
            'category_name': product['product__category__name'],
            'total_quantity_sold': float(product['total_quantity_sold']),
            'total_revenue': float(product['total_revenue']),
            'total_cost': float(product['total_cost']),
            'total_profit': float(product['total_profit']),
            'profit_margin': float(profit_margin),
            'transaction_count': product['transaction_count'],
        })
    
    # Rankings by Category
    category_rankings = {}
    categories = Category.objects.all()
    
    for category in categories:
        category_products = list(
            base_queryset.filter(product__category=category)
            .values(
                'product_id',
                'product__name',
                'product__sku'
            )
            .annotate(
                total_quantity_sold=Coalesce(Sum('quantity'), Decimal('0')),
                total_revenue=Coalesce(Sum('subtotal'), Decimal('0')),
                total_cost=Coalesce(Sum(product_cost_expr), Decimal('0')),
                transaction_count=Count('sale_id', distinct=True),
            )
            .annotate(
                total_profit=ExpressionWrapper(
                    F('total_revenue') - F('total_cost'),
                    output_field=DecimalField(max_digits=14, decimal_places=2)
                )
            )
            .order_by('-total_revenue')[:limit]
        )
        
        if category_products:
            category_list = []
            for rank, product in enumerate(category_products, 1):
                profit_margin = (
                    (product['total_profit'] / product['total_revenue'] * 100)
                    if product['total_revenue'] > 0
                    else Decimal('0')
                )
                category_list.append({
                    'rank': rank,
                    'product_id': product['product_id'],
                    'product_name': product['product__name'],
                    'product_sku': product['product__sku'],
                    'total_quantity_sold': float(product['total_quantity_sold']),
                    'total_revenue': float(product['total_revenue']),
                    'total_cost': float(product['total_cost']),
                    'total_profit': float(product['total_profit']),
                    'profit_margin': float(profit_margin),
                    'transaction_count': product['transaction_count'],
                })
            
            category_rankings[category.name] = {
                'category_id': category.id,
                'category_name': category.name,
                'products': category_list,
                'total_products': len(category_list),
            }
    
    # Rankings by Supplier
    # Get products with their most recent supplier from stock entries
    supplier_rankings = {}
    suppliers = Supplier.objects.all()
    
    for supplier in suppliers:
        # Get products that have been purchased from this supplier
        supplier_products = StockEntry.objects.filter(
            supplier=supplier
        ).values_list('product_id', flat=True).distinct()
        
        if supplier_products:
            supplier_sales = list(
                base_queryset.filter(product_id__in=supplier_products)
                .values(
                    'product_id',
                    'product__name',
                    'product__sku',
                    'product__category_id',
                    'product__category__name'
                )
                .annotate(
                    total_quantity_sold=Coalesce(Sum('quantity'), Decimal('0')),
                    total_revenue=Coalesce(Sum('subtotal'), Decimal('0')),
                    total_cost=Coalesce(Sum(product_cost_expr), Decimal('0')),
                    transaction_count=Count('sale_id', distinct=True),
                )
                .annotate(
                    total_profit=ExpressionWrapper(
                        F('total_revenue') - F('total_cost'),
                        output_field=DecimalField(max_digits=14, decimal_places=2)
                    )
                )
                .order_by('-total_revenue')[:limit]
            )
            
            if supplier_sales:
                supplier_list = []
                for rank, product in enumerate(supplier_sales, 1):
                    profit_margin = (
                        (product['total_profit'] / product['total_revenue'] * 100)
                        if product['total_revenue'] > 0
                        else Decimal('0')
                    )
                    supplier_list.append({
                        'rank': rank,
                        'product_id': product['product_id'],
                        'product_name': product['product__name'],
                        'product_sku': product['product__sku'],
                        'category_id': product['product__category_id'],
                        'category_name': product['product__category__name'],
                        'total_quantity_sold': float(product['total_quantity_sold']),
                        'total_revenue': float(product['total_revenue']),
                        'total_cost': float(product['total_cost']),
                        'total_profit': float(product['total_profit']),
                        'profit_margin': float(profit_margin),
                        'transaction_count': product['transaction_count'],
                    })
                
                supplier_rankings[supplier.name] = {
                    'supplier_id': supplier.id,
                    'supplier_name': supplier.name,
                    'products': supplier_list,
                    'total_products': len(supplier_list),
                }
    
    # Heat Map of Selling Times
    # Base queryset for sales (not sale items, for transaction-level analysis)
    sales_base_queryset = Sale.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
        status='completed'
    )
    
    if branch_id:
        sales_base_queryset = sales_base_queryset.filter(branch_id=branch_id)
    
    # Initialize heat maps
    hour_heatmap = {}
    for hour in range(24):
        hour_heatmap[hour] = {
            'hour': hour,
            'hour_label': f"{hour:02d}:00",
            'transaction_count': 0,
            'total_revenue': Decimal('0'),
            'total_items_sold': Decimal('0'),
        }
    
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    # Map: 0=Monday, 1=Tuesday, ..., 6=Sunday (Python weekday)
    
    day_heatmap = {}
    for day_num in range(7):
        day_heatmap[day_num] = {
            'day_of_week': day_num,
            'day_name': day_names[day_num],
            'transaction_count': 0,
            'total_revenue': Decimal('0'),
            'total_items_sold': Decimal('0'),
        }
    
    # Initialize heat map matrix
    heatmap_matrix = {}
    for day_num in range(7):
        day_name = day_names[day_num]
        heatmap_matrix[day_name] = {}
        for hour in range(24):
            heatmap_matrix[day_name][hour] = {
                'hour': hour,
                'day_of_week': day_num,
                'transaction_count': 0,
                'total_revenue': Decimal('0'),
                'total_items_sold': Decimal('0'),
            }
    
    # Get all sales and process in Python (database-agnostic approach)
    all_sales = sales_base_queryset.select_related('branch', 'cashier').prefetch_related('items')
    
    # Process each sale once for all heat map calculations
    for sale in all_sales:
        # Extract time components
        hour = sale.created_at.hour
        day_num = sale.created_at.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
        
        # Calculate metrics
        net_revenue = sale.total_amount - sale.discount + sale.tax
        total_items = sum(item.quantity for item in sale.items.all())
        
        # Update hour heatmap
        hour_heatmap[hour]['transaction_count'] += 1
        hour_heatmap[hour]['total_revenue'] += net_revenue
        hour_heatmap[hour]['total_items_sold'] += total_items
        
        # Update day heatmap
        day_heatmap[day_num]['transaction_count'] += 1
        day_heatmap[day_num]['total_revenue'] += net_revenue
        day_heatmap[day_num]['total_items_sold'] += total_items
        
        # Update heat map matrix
        day_name = day_names[day_num]
        heatmap_matrix[day_name][hour]['transaction_count'] += 1
        heatmap_matrix[day_name][hour]['total_revenue'] += net_revenue
        heatmap_matrix[day_name][hour]['total_items_sold'] += total_items
    
    # Convert to float for JSON serialization
    for hour in range(24):
        hour_heatmap[hour]['total_revenue'] = float(hour_heatmap[hour]['total_revenue'])
        hour_heatmap[hour]['total_items_sold'] = float(hour_heatmap[hour]['total_items_sold'])
    
    for day_num in range(7):
        day_heatmap[day_num]['total_revenue'] = float(day_heatmap[day_num]['total_revenue'])
        day_heatmap[day_num]['total_items_sold'] = float(day_heatmap[day_num]['total_items_sold'])
        
        day_name = day_names[day_num]
        for hour in range(24):
            heatmap_matrix[day_name][hour]['total_revenue'] = float(heatmap_matrix[day_name][hour]['total_revenue'])
            heatmap_matrix[day_name][hour]['total_items_sold'] = float(heatmap_matrix[day_name][hour]['total_items_sold'])
    
    
    # Format heat map as array for easier frontend consumption
    heatmap_data = []
    for day_num, day_name in enumerate(day_names):
        for hour in range(24):
            heatmap_data.append({
                'day_of_week': day_num,
                'day_name': day_name,
                'hour': hour,
                'hour_label': f"{hour:02d}:00",
                'transaction_count': heatmap_matrix[day_name][hour]['transaction_count'],
                'total_revenue': float(heatmap_matrix[day_name][hour]['total_revenue']),
                'total_items_sold': float(heatmap_matrix[day_name][hour]['total_items_sold']),
            })
    
    # Calculate peak times
    max_transactions = max([h['transaction_count'] for h in hour_heatmap.values()]) if hour_heatmap else 0
    peak_hours = [
        {'hour': h, 'hour_label': hour_heatmap[h]['hour_label'], 'transaction_count': hour_heatmap[h]['transaction_count']}
        for h in range(24) if hour_heatmap[h]['transaction_count'] == max_transactions and max_transactions > 0
    ]
    
    max_day_transactions = max([d['transaction_count'] for d in day_heatmap.values()]) if day_heatmap else 0
    peak_days = [
        {'day_of_week': d, 'day_name': day_heatmap[d]['day_name'], 'transaction_count': day_heatmap[d]['transaction_count']}
        for d in range(7) if day_heatmap[d]['transaction_count'] == max_day_transactions and max_day_transactions > 0
    ]
    
    return {
        'overall_rankings': overall_rankings,
        'rankings_by_category': category_rankings,
        'rankings_by_supplier': supplier_rankings,
        'selling_times_heatmap': {
            'by_hour': list(hour_heatmap.values()),
            'by_day_of_week': list(day_heatmap.values()),
            'heatmap_matrix': heatmap_data,
            'peak_times': {
                'peak_hours': peak_hours,
                'peak_days': peak_days,
            },
            'summary': {
                'busiest_hour': peak_hours[0]['hour_label'] if peak_hours else None,
                'busiest_day': peak_days[0]['day_name'] if peak_days else None,
                'total_hours_analyzed': 24,
                'total_days_analyzed': 7,
            }
        },
        'filters': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
            'branch_id': branch_id,
            'limit': limit,
        },
        'summary': {
            'total_products_ranked': len(overall_rankings),
            'total_categories': len(category_rankings),
            'total_suppliers': len(supplier_rankings),
        }
    }

