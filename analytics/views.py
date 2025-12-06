from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView

from accounts.models import User, Employee
from organization.models import Branch, Warehouse

from django.utils import timezone
from datetime import datetime

from .services import (
    get_admin_dashboard_data,
    get_revenue_trends_data,
    get_category_revenue_data,
    get_top_products_data,
    get_branch_warehouse_revenue_data,
    get_cashier_performance_data,
    get_stock_transfer_statistics,
    get_branch_stock_evaluation,
    get_warehouse_stock_evaluation,
    get_sales_trends_charts,
    get_stock_movement_data,
    get_sales_report,
    get_stock_report,
    get_auditor_dashboard_data,
    get_auditor_reports,
    get_product_performance,
)


class AdminDashboard(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get admin dashboard data."""
        dead_stock_period = request.query_params.get('dead_stock_period', '90d')
        slow_stock_period = request.query_params.get('slow_stock_period', '30d')
        
        data = get_admin_dashboard_data(dead_stock_period, slow_stock_period)
        return Response(data)


class ReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get comprehensive analytics reports."""
        # Revenue trends
        revenue_data = get_revenue_trends_data()
        
        # Category revenue
        category_revenue = get_category_revenue_data()
        
        # Top products
        top_products_data = get_top_products_data()
        
        # Branch and warehouse revenue
        branch_warehouse_data = get_branch_warehouse_revenue_data()
        
        # Cashier performance
        cashier_range = request.query_params.get('cashier_range', 'month')
        cashier_start = request.query_params.get('cashier_start')
        cashier_end = request.query_params.get('cashier_end')
        cashier_data = get_cashier_performance_data(cashier_range, cashier_start, cashier_end)
        
        # Stock transfer statistics
        transfer_data = get_stock_transfer_statistics()
        
        data = {
            **revenue_data,
            'category_revenue_trend': category_revenue,
            **top_products_data,
            **branch_warehouse_data,
            'cashier_performance': cashier_data,
            'stock_transfer_statistics': transfer_data,
        }
        
        return Response(data)


class BranchManagerDashboard(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get branch manager dashboard with stock evaluation."""
        # Get the branch associated with the branch manager
        user = request.user
        employee = user.profile.first() if hasattr(user, 'profile') else None
        
        if not employee or not employee.branch:
            return Response(
                {'detail': 'Branch not found for this user. Please ensure you are assigned to a branch.'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        branch = employee.branch
        
        # Get stock evaluation data
        stock_data = get_branch_stock_evaluation(branch)
        
        data = {
            'branch': {
                'id': branch.id,
                'name': branch.name,
                'warehouse_id': branch.warehouse.id if branch.warehouse else None,
                'warehouse_name': branch.warehouse.name if branch.warehouse else None,
            },
            **stock_data,
        }
        
        return Response(data)


class BranchReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get comprehensive branch-level analytics reports."""
        # Get the branch associated with the branch manager
        user = request.user
        employee = user.profile.first() if hasattr(user, 'profile') else None
        
        if not employee or not employee.branch:
            return Response(
                {'detail': 'Branch not found for this user. Please ensure you are assigned to a branch.'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        branch = employee.branch
        branch_id = branch.id
        warehouse_id = branch.warehouse.id if branch.warehouse else None
        
        # Parse date parameters
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        
        start_date = None
        end_date = None
        
        if start_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str)
                if timezone.is_naive(start_date):
                    start_date = timezone.make_aware(start_date)
            except ValueError:
                pass
        
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str)
                if timezone.is_naive(end_date):
                    end_date = timezone.make_aware(end_date)
            except ValueError:
                pass
        
        # Parse other filter parameters
        product_id = request.query_params.get('product_id')
        category_id = request.query_params.get('category_id')
        payment_method = request.query_params.get('payment_method')
        cashier_id = request.query_params.get('cashier_id')
        low_stock_only = request.query_params.get('low_stock_only', 'false').lower() == 'true'
        
        product_id = int(product_id) if product_id else None
        category_id = int(category_id) if category_id else None
        cashier_id = int(cashier_id) if cashier_id else None
        
        # Get all report data
        sales_trends_data = get_sales_trends_charts(start_date, end_date, branch_id)
        stock_movement_data = get_stock_movement_data(start_date, end_date, warehouse_id, branch_id)
        sales_report_data = get_sales_report(start_date, end_date, branch_id, product_id, payment_method, cashier_id)
        stock_report_data = get_stock_report(warehouse_id, branch_id, product_id, category_id, low_stock_only)
        
        data = {
            'branch': {
                'id': branch.id,
                'name': branch.name,
                'warehouse_id': warehouse_id,
                'warehouse_name': branch.warehouse.name if branch.warehouse else None,
            },
            'sales_trends': sales_trends_data,
            'stock_movement': stock_movement_data,
            'sales_report': sales_report_data,
            'stock_report': stock_report_data,
        }
        
        return Response(data)


class CashierPerformanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get detailed cashier performance tracking through their sales."""
        range_key = request.query_params.get('range', 'month')
        start_str = request.query_params.get('start_date')
        end_str = request.query_params.get('end_date')
        cashier_id = request.query_params.get('cashier_id')
        branch_id = request.query_params.get('branch_id')
        
        cashier_id = int(cashier_id) if cashier_id else None
        branch_id = int(branch_id) if branch_id else None
        
        data = get_cashier_performance_data(
            range_key=range_key,
            start_str=start_str,
            end_str=end_str,
            cashier_id=cashier_id,
            branch_id=branch_id
        )
        return Response(data)


class WarehouseManagerDashboard(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get warehouse manager dashboard with stock evaluation."""
        # Get the warehouse associated with the warehouse manager
        user = request.user
        employee = user.profile.first() if hasattr(user, 'profile') else None
        
        if not employee or not employee.warehouse:
            return Response(
                {'detail': 'Warehouse not found for this user. Please ensure you are assigned to a warehouse.'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        warehouse = employee.warehouse
        
        # Get stock evaluation parameters
        dead_stock_period = request.query_params.get('dead_stock_period', '90d')
        slow_stock_period = request.query_params.get('slow_stock_period', '30d')
        
        # Get stock evaluation data
        stock_data = get_warehouse_stock_evaluation(warehouse, dead_stock_period, slow_stock_period)
        
        data = {
            'warehouse': {
                'id': warehouse.id,
                'name': warehouse.name,
                'location': warehouse.location,
                'is_main': warehouse.is_main,
            },
            **stock_data,
        }
        
        return Response(data)


class WarehouseReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get comprehensive warehouse-level analytics reports."""
        # Get the warehouse associated with the warehouse manager
        user = request.user
        employee = user.profile.first() if hasattr(user, 'profile') else None
        
        warehouse_id = None
        if employee and employee.warehouse:
            warehouse_id = employee.warehouse.id
        
        # Revenue trends
        revenue_data = get_revenue_trends_data()
        
        # Category revenue
        category_revenue = get_category_revenue_data()
        
        # Top products
        top_products_data = get_top_products_data()
        
        # Branch and warehouse revenue
        branch_warehouse_data = get_branch_warehouse_revenue_data()
        
        # Stock transfer statistics
        transfer_data = get_stock_transfer_statistics()
        
        data = {
            **revenue_data,
            'category_revenue_trend': category_revenue,
            **top_products_data,
            **branch_warehouse_data,
            'stock_transfer_statistics': transfer_data,
        }
        
        if warehouse_id:
            data['warehouse'] = {
                'id': employee.warehouse.id,
                'name': employee.warehouse.name,
                'location': employee.warehouse.location,
                'is_main': employee.warehouse.is_main,
            }
        
        return Response(data)

class AuditorDashboard(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get auditor dashboard with system-wide overview and audit logs."""
        data = get_auditor_dashboard_data()
        return Response(data)


class AuditorReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get comprehensive auditor reports across all branches and warehouses."""
        # Parse date parameters
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        
        start_date = None
        end_date = None
        
        if start_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str)
                if timezone.is_naive(start_date):
                    start_date = timezone.make_aware(start_date)
            except ValueError:
                pass
        
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str)
                if timezone.is_naive(end_date):
                    end_date = timezone.make_aware(end_date)
            except ValueError:
                pass
        
        # Parse filter parameters
        branch_id = request.query_params.get('branch_id')
        warehouse_id = request.query_params.get('warehouse_id')
        user_id = request.query_params.get('user_id')
        activity_type = request.query_params.get('activity_type')
        
        branch_id = int(branch_id) if branch_id else None
        warehouse_id = int(warehouse_id) if warehouse_id else None
        user_id = int(user_id) if user_id else None
        
        data = get_auditor_reports(
            start_date=start_date,
            end_date=end_date,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            user_id=user_id,
            activity_type=activity_type,
        )
        
        return Response(data)

class ProductPerformanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        Get product performance rankings by sales, category, and supplier.
        
        Query Parameters:
        - start_date: Start date for analysis (ISO 8601 format, optional, defaults to 30 days ago)
        - end_date: End date for analysis (ISO 8601 format, optional, defaults to now)
        - branch_id: Filter by specific branch (optional)
        - limit: Maximum number of products to return per ranking (default: 50)
        
        Returns:
        - overall_rankings: Top products ranked by revenue
        - rankings_by_category: Products ranked within each category
        - rankings_by_supplier: Products ranked by supplier
        """
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        branch_id = request.query_params.get('branch_id')
        limit = int(request.query_params.get('limit', 50))
        
        # Parse dates
        if start_date:
            try:
                start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                return Response(
                    {'detail': 'Invalid start_date format. Use ISO 8601 format.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if end_date:
            try:
                end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                return Response(
                    {'detail': 'Invalid end_date format. Use ISO 8601 format.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        branch_id = int(branch_id) if branch_id else None
        
        data = get_product_performance(
            start_date=start_date,
            end_date=end_date, 
            branch_id=branch_id,
            limit=limit
        )
        
        return Response(data, status=status.HTTP_200_OK)