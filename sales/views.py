from decimal import Decimal
from multiprocessing import parent_process
from operator import isub
from urllib.parse import parse_qs
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db.models import Q, F, Sum
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .models import Sale, SaleItem, Discount, ProductReturn, CashReceived, ExchangeRate
from .serializers import (
    SaleSerializer,
    ProductReturnSerializer,
    CreateSaleSerializer,
    SaleItemsPayloadSerializer,
    SaleReturnSerializer,
    BarcodeLookupSerializer,
    BulkSaleSerializer,
    SalesHistoryQuerySerializer,
    DiscountSerializer,
    ApplyDiscountSerializer,
    ReturnAuthorizationCodeSerializer,
    ReturnAuthorizationCodeCreateSerializer,
    CashReceivedSerializer,
    CashReceivedCreateSerializer,
    CashReceivedUpdateSerializer,
    ExchangeRateSerializer,
)
from . import services
from shared.audit import log_activity, ActivityType
from products import services as product_services


def _extract_id(value):
    """Extract ID from value if it's a model instance, otherwise return the value."""
    if value is None:
        return None
    if hasattr(value, 'id'):
        return value.id
    return value


class SaleListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('status', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('start_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date'),
            openapi.Parameter('end_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date'),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='Search by sale number'),
            openapi.Parameter('sync_id', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='Filter by sync_id'),
        ],
        responses={200: SaleSerializer(many=True)}
    )
    def get(self, request):
        """List sales with optional filters. Cashiers can only view their own sales."""
        sales = Sale.objects.select_related('branch', 'cashier').prefetch_related('items__product', 'returns__product')

        # Role-based filtering
        user_role = getattr(request.user, 'role', None)
        
        if user_role == 'cashier':
            # Cashiers can only view their own sales
            sales = sales.filter(cashier=request.user)
        elif user_role == 'branch_manager':
            # Branch managers can view all sales in their branch
            profile = getattr(request.user, 'profile', None)
            if profile:
                employee = profile.first()
                if employee and employee.branch:
                    sales = sales.filter(branch=employee.branch)
                else:
                    # If not assigned to a branch, return empty
                    sales = sales.none()
            else:
                sales = sales.none()
        # Admin and owner can view all sales (no additional filter)

        branch_id = request.query_params.get('branch_id')
        status_filter = request.query_params.get('status')
        start_date = parse_date(request.query_params.get('start_date')) if request.query_params.get('start_date') else None
        end_date = parse_date(request.query_params.get('end_date')) if request.query_params.get('end_date') else None
        search = request.query_params.get('search')
        sync_id = request.query_params.get('sync_id')

        # Apply additional filters (respecting role-based restrictions)
        if branch_id:
            # For branch managers, validate they can only filter by their own branch
            if user_role == 'branch_manager':
                profile = getattr(request.user, 'profile', None)
                if profile:
                    employee = profile.first()
                    if employee and employee.branch and int(branch_id) != employee.branch.id:
                        return Response(
                            {'detail': 'You can only view sales for your branch.'},
                            status=status.HTTP_403_FORBIDDEN
                        )
            sales = sales.filter(branch_id=branch_id)
        
        if status_filter:
            sales = sales.filter(status=status_filter)
        if start_date:
            sales = sales.filter(created_at__date__gte=start_date)
        if end_date:
            sales = sales.filter(created_at__date__lte=end_date)
        if search:
            sales = sales.filter(sale_number__icontains=search)
        if sync_id:
            sales = sales.filter(sync_id=sync_id)

        serializer = SaleSerializer(sales, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        request_body=CreateSaleSerializer,
        responses={201: SaleSerializer}
    )
    def post(self, request):
        """Create a new sale (draft/pending)."""
        serializer = CreateSaleSerializer(data=request.data)
        if serializer.is_valid():
            try:
                sale = services.create_sale(
                    branch_id=serializer.validated_data['branch_id'],
                    cashier=request.user,
                    type_of_payment = serializer.validated_data['type_of_payment'],
                    tax=serializer.validated_data.get('tax'),
                    notes=serializer.validated_data.get('notes', ''),
                    items_data=serializer.validated_data.get('items', []),
                    sync_id=serializer.validated_data.get('sync_id'),
                    discount_code=serializer.validated_data.get('discount_code'),
                    discount_id=serializer.validated_data.get('discount_id'),
                )
                if sale.type_of_payment in ['usd_cash', 'zig_cash', 'bank_transfer_zig', 'bank_transfer_usd']:
                    services.complete_sale(sale, completed_by=request.user)
                elif sale.type_of_payment in ['ecocash_usd', 'ecocash_zig']:
                    pass
                else:
                    pass

                log_activity(
                    activity_type=ActivityType.SALE_CREATED,
                    user=request.user,
                    description=f'Created sale {sale.sale_number}',
                    request=request,
                    related_object=sale,
                )
                return Response(SaleSerializer(sale).data, status=status.HTTP_201_CREATED)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BulkSaleCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=BulkSaleSerializer,
        responses={
            201: openapi.Response(
                description='Bulk sales creation result',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'created': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)),
                        'errors': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)),
                        'summary': openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            properties={
                                'total': openapi.Schema(type=openapi.TYPE_INTEGER),
                                'successful': openapi.Schema(type=openapi.TYPE_INTEGER),
                                'failed': openapi.Schema(type=openapi.TYPE_INTEGER),
                            }
                        )
                    }
                )
            )
        }
    )
    def post(self, request):
        """Create multiple pending sales in a single request (for offline cache uploads)."""
        serializer = BulkSaleSerializer(data=request.data)
        if serializer.is_valid():
            created_sales, errors = services.bulk_create_sales(
                sales_data=serializer.validated_data['sales'],
                cashier=request.user,
            )

            for sale in created_sales:
                if sale.type_of_payment in ['usd_cash', 'zig_cash', 'bank_transfer_zig', 'bank_transfer_usd']:
                    services.complete_sale(sale, completed_by=request.user)
                elif sale.type_of_payment in ['ecocash_usd', 'ecocash_zig']:
                    pass
                else:
                    pass

            response_data = {
                'created': SaleSerializer(created_sales, many=True).data,
                'errors': errors,
                'summary': {
                    'total': len(serializer.validated_data['sales']),
                    'successful': len(created_sales),
                    'failed': len(errors),
                }
            }

            if created_sales:
                log_activity(
                    activity_type=ActivityType.SALE_CREATED,
                    user=request.user,
                    description=f'Bulk created {len(created_sales)} sales (total payload {len(serializer.validated_data["sales"])})',
                    request=request,
                )

            return Response(response_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SaleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: SaleSerializer})
    def get(self, request, pk):
        """Retrieve sale details."""
        try:
            sale = Sale.objects.select_related('branch', 'cashier').prefetch_related('items__product', 'returns__product').get(pk=pk)
        except Sale.DoesNotExist:
            return Response({'detail': 'Sale not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = SaleSerializer(sale)
        return Response(serializer.data)


class SaleAddItemView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=SaleItemsPayloadSerializer,
        responses={200: SaleSerializer}
    )
    def post(self, request, pk):
        """Add items to a pending sale."""
        serializer = SaleItemsPayloadSerializer(data=request.data)
        if serializer.is_valid():
            try:
                sale = Sale.objects.select_related('branch').get(pk=pk)
            except Sale.DoesNotExist:
                return Response({'detail': 'Sale not found.'}, status=status.HTTP_404_NOT_FOUND)

            try:
                sale = services.add_items_to_sale(sale, serializer.validated_data['items'])
                log_activity(
                    activity_type=ActivityType.SALE_UPDATED,
                    user=request.user,
                    description=f'Added items to sale {sale.sale_number}',
                    request=request,
                    related_object=sale,
                )
                return Response(SaleSerializer(sale).data, status=status.HTTP_200_OK)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SaleCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: SaleSerializer})
    def post(self, request, pk):
        """Complete a sale and deduct stock."""
        try:
            sale = Sale.objects.select_related('branch').prefetch_related('items').get(pk=pk)
        except Sale.DoesNotExist:
            return Response({'detail': 'Sale not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            sale = services.complete_sale(sale, completed_by=request.user)
            log_activity(
                activity_type=ActivityType.SALE_UPDATED,
                user=request.user,
                description=f'Completed sale {sale.sale_number}',
                request=request,
                related_object=sale,
            )
            return Response(SaleSerializer(sale).data, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class SaleCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: SaleSerializer})
    def post(self, request, pk):
        """Cancel a pending sale."""
        try:
            sale = Sale.objects.get(pk=pk)
        except Sale.DoesNotExist:
            return Response({'detail': 'Sale not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            sale = services.cancel_sale(sale)
            log_activity(
                activity_type=ActivityType.SALE_CANCELLED,
                user=request.user,
                description=f'Cancelled sale {sale.sale_number}',
                request=request,
                related_object=sale,
            )
            return Response(SaleSerializer(sale).data, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class SaleReturnView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=SaleReturnSerializer,
        responses={200: ProductReturnSerializer}
    )
    def post(self, request, pk):
        """Process a product return for a sale."""
        serializer = SaleReturnSerializer(data=request.data)
        if serializer.is_valid():
            try:
                sale = Sale.objects.select_related('branch').prefetch_related('items').get(pk=pk)
            except Sale.DoesNotExist:
                return Response({'detail': 'Sale not found.'}, status=status.HTTP_404_NOT_FOUND)

            user_role = getattr(request.user, 'role', None)
            authorization_code = serializer.validated_data.get('authorization_code')
            if user_role == 'cashier':
                if not authorization_code:
                    return Response(
                        {'authorization_code': ['Authorization code is required for branch manager returns.']},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if not services.validate_return_authorization_code(
                    branch_id=sale.branch_id,
                    code=authorization_code
                ):
                    return Response(
                        {'detail': 'Invalid or expired authorization code.'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            

            try:
                product_return = services.process_sale_return(
                    sale=sale,
                    product_id=serializer.validated_data['product_id'],
                    quantity=serializer.validated_data['quantity'],
                    reason=serializer.validated_data.get('reason', ''),
                    refund_amount=serializer.validated_data.get('refund_amount'),
                    processed_by=request.user,
                )
                log_activity(
                    activity_type=ActivityType.SALE_UPDATED,
                    user=request.user,
                    description=f'Processed return for sale {sale.sale_number}',
                    request=request,
                    related_object=product_return,
                )
                return Response(ProductReturnSerializer(product_return).data, status=status.HTTP_200_OK)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ProductReturnsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        pass

    
class ReturnRateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'start_date',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                format='date',
                description='Filter start date (YYYY-MM-DD); defaults to first day of current month'
            ),
            openapi.Parameter(
                'end_date',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                format='date',
                description='Filter end date (YYYY-MM-DD); defaults to today'
            ),
            openapi.Parameter(
                'branch_id',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                description='Branch ID to scope results (admins/owners/auditors only)'
            ),
        ],
        responses={200: 'Return rate metrics'}
    )
    def get(self, request):
        """Return product return rate metrics with role-aware scoping."""
        today = timezone.now().date()
        default_start = today.replace(day=1)

        start_param = request.query_params.get('start_date')
        end_param = request.query_params.get('end_date')
        branch_param = request.query_params.get('branch_id')

        start_date = parse_date(start_param) if start_param else default_start
        end_date = parse_date(end_param) if end_param else today

        if start_param and start_date is None:
            return Response({'detail': 'Invalid start_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        if end_param and end_date is None:
            return Response({'detail': 'Invalid end_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        if end_date < start_date:
            return Response({'detail': 'end_date must be greater than or equal to start_date.'}, status=status.HTTP_400_BAD_REQUEST)

        user_role = getattr(request.user, 'role', None)
        unrestricted_roles = {'owner', 'admin', 'auditor'}
        branch_scoped_roles = {'branch_manager', 'cashier'}

        resolved_branch_id = None
        if user_role in unrestricted_roles:
            if branch_param:
                try:
                    resolved_branch_id = int(branch_param)
                except ValueError:
                    return Response({'detail': 'branch_id must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
        elif user_role in branch_scoped_roles:
            profile = getattr(request.user, 'profile', None)
            employee = profile.first() if profile else None
            branch = getattr(employee, 'branch', None)
            if not branch:
                return Response({'detail': 'You are not assigned to a branch.'}, status=status.HTTP_400_BAD_REQUEST)
            resolved_branch_id = branch.id
            if branch_param and int(branch_param) != resolved_branch_id:
                return Response({'detail': 'You can only view return rates for your branch.'}, status=status.HTTP_403_FORBIDDEN)
        else:
            return Response({'detail': 'You do not have permission to view return rate analytics.'}, status=status.HTTP_403_FORBIDDEN)

        sale_items = SaleItem.objects.filter(
            sale__status='completed',
            sale__created_at__date__gte=start_date,
            sale__created_at__date__lte=end_date,
        )
        returns = ProductReturn.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        if resolved_branch_id:
            sale_items = sale_items.filter(sale__branch_id=resolved_branch_id)
            returns = returns.filter(sale__branch_id=resolved_branch_id)

        total_sold_quantity = sale_items.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
        total_sales_value = sale_items.aggregate(total=Sum('subtotal'))['total'] or Decimal('0')

        total_returned_quantity = returns.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
        total_refund_amount = returns.aggregate(total=Sum('refund_amount'))['total'] or Decimal('0')

        quantity_return_rate = (
            (total_returned_quantity / total_sold_quantity) * Decimal('100')
            if total_sold_quantity > 0 else Decimal('0')
        )
        value_return_rate = (
            (total_refund_amount / total_sales_value) * Decimal('100')
            if total_sales_value > 0 else Decimal('0')
        )

        top_returned_products_query = returns.values(
            'product_id',
            'product__name',
            'product__sku'
        ).annotate(
            total_returned_quantity=Sum('quantity'),
            total_refund_amount=Sum('refund_amount')
        ).order_by('-total_returned_quantity')[:10]

        top_returned_products = [
            {
                'product_id': item['product_id'],
                'product_name': item['product__name'],
                'product_sku': item['product__sku'],
                'total_returned_quantity': item['total_returned_quantity'] or Decimal('0'),
                'total_refund_amount': item['total_refund_amount'] or Decimal('0'),
            }
            for item in top_returned_products_query
        ]

        data = {
            'filters': {
                'start_date': start_date,
                'end_date': end_date,
                'branch_id': resolved_branch_id,
            },
            'metrics': {
                'total_sold_quantity': total_sold_quantity,
                'total_sales_value': total_sales_value,
                'total_returned_quantity': total_returned_quantity,
                'total_refund_amount': total_refund_amount,
                'quantity_return_rate': quantity_return_rate,
                'value_return_rate': value_return_rate,
            },
            'top_returned_products': top_returned_products,
        }

        return Response(data, status=status.HTTP_200_OK)


class ReturnAuthorizationCodeView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'branch_id',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                description='Optional branch filter (admins/owners/auditors only). Branch managers automatically use their branch.'
            )
        ],
        responses={200: ReturnAuthorizationCodeSerializer(many=True)}
    )
    def get(self, request):
        """Retrieve currently active authorization codes."""
        user_role = getattr(request.user, 'role', None)
        unrestricted_roles = {'owner', 'admin', 'auditor'}

        branch_id = None
        if user_role in unrestricted_roles:
            branch_param = request.query_params.get('branch_id')
            if branch_param:
                try:
                    branch_id = int(branch_param)
                except ValueError:
                    return Response({'detail': 'branch_id must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
        elif user_role == 'branch_manager':
            profile = getattr(request.user, 'profile', None)
            employee = profile.first() if profile else None
            branch = getattr(employee, 'branch', None)
            if not branch:
                return Response({'detail': 'You are not assigned to a branch.'}, status=status.HTTP_400_BAD_REQUEST)
            branch_id = branch.id
        else:
            return Response({'detail': 'You do not have permission to view return authorization codes.'}, status=status.HTTP_403_FORBIDDEN)

        codes = services.get_active_return_authorization_codes(branch_id=branch_id)
        return Response(ReturnAuthorizationCodeSerializer(codes, many=True).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        request_body=ReturnAuthorizationCodeCreateSerializer,
        responses={201: ReturnAuthorizationCodeSerializer}
    )
    def post(self, request):
        """Generate a new authorization code for a branch."""
        if getattr(request.user, 'role', None) not in {'owner', 'admin'}:
            return Response({'detail': 'Only owners or admins can generate authorization codes.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ReturnAuthorizationCodeCreateSerializer(data=request.data)
        if serializer.is_valid():
            expires_in_minutes = serializer.validated_data.get('expires_in_minutes')
            if expires_in_minutes is None:
                expires_in_minutes = serializer.validated_data.get('expires_in_hours', 24) * 60
            auth_code = services.generate_return_authorization_code(
                branch_id=serializer.validated_data['branch_id'],
                expires_in_minutes=expires_in_minutes,
                created_by=request.user,
            )
            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f'Generated return authorization code for branch {auth_code.branch.name}',
                request=request,
                related_object=auth_code,
            )
            return Response(ReturnAuthorizationCodeSerializer(auth_code).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class SaleBarcodeLookupView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=BarcodeLookupSerializer,
        responses={200: 'Product info with stock levels'}
    )
    def post(self, request):
        """Lookup product details via barcode for a given branch."""
        serializer = BarcodeLookupSerializer(data=request.data)
        if serializer.is_valid():
            result = product_services.get_product_with_stock_by_barcode(
                barcode_value=serializer.validated_data['barcode'],
                branch_id=serializer.validated_data['branch_id'],
            )
            if not result:
                return Response({'detail': 'Product not found for provided barcode.'}, status=status.HTTP_404_NOT_FOUND)

            product = result['product']
            response_data = {
                'product_id': product.id,
                'product_name': product.name,
                'product_sku': product.sku,
                'available_stock': result.get('available_stock'),
                'selling_price': result.get('selling_price'),
                'purchase_price': result.get('purchase_price'),
                'barcode': result.get('barcode').barcode if result.get('barcode') else None,
            }
            return Response(response_data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class SalesHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('start_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date', required=True,
                            description='Start date for sales history (YYYY-MM-DD)'),
            openapi.Parameter('end_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date', required=True,
                            description='End date for sales history (YYYY-MM-DD)'),
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                            description='Filter by branch ID (optional)'),
            openapi.Parameter('group_by', openapi.IN_QUERY, type=openapi.TYPE_STRING,
                            description='Group results by: day, month, or all (default: day)'),
            openapi.Parameter('use_stored_reports', openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN,
                            description='Use stored DailySalesReport data when available (default: true)'),
        ],
        responses={200: 'Sales history with summary and breakdown'}
    )
    def get(self, request):
        """Get sales history with aggregations and breakdown."""
        serializer = SalesHistoryQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        
        history = services.get_sales_history(
            start_date=data['start_date'],
            end_date=data['end_date'],
            branch_id=data.get('branch_id'),
            group_by=data.get('group_by', 'day'),
            use_stored_reports=data.get('use_stored_reports', True),
        )
        
        return Response(history, status=status.HTTP_200_OK)


class DiscountListView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('is_active', openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
            openapi.Parameter('code', openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: DiscountSerializer(many=True)}
    )
    def get(self, request):
        """List all discounts with optional filters."""
        discounts = Discount.objects.select_related('product', 'category', 'branch', 'created_by')
        
        branch_id = request.query_params.get('branch_id')
        product_id = request.query_params.get('product_id')
        is_active = request.query_params.get('is_active')
        code = request.query_params.get('code')
        
        if branch_id:
            discounts = discounts.filter(
                Q(branch_id=branch_id) | Q(apply_to__in=['all', 'min_purchase'])
            )
        if product_id:
            from products.models import Product
            product = Product.objects.get(pk=product_id)
            discounts = discounts.filter(
                Q(apply_to='all') |
                Q(apply_to='product', product_id=product_id) |
                Q(apply_to='category', category=product.category) |
                Q(apply_to='min_purchase')
            )
        if is_active is not None:
            discounts = discounts.filter(is_active=is_active.lower() == 'true')
        if code:
            discounts = discounts.filter(code=code)
        
        serializer = DiscountSerializer(discounts, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        request_body=DiscountSerializer,
        responses={201: DiscountSerializer}
    )
    def post(self, request):
        """Create a new discount."""
        serializer = DiscountSerializer(data=request.data)
        if serializer.is_valid():
            try:
                discount = services.create_discount(
                    name=serializer.validated_data['name'],
                    discount_type=serializer.validated_data['discount_type'],
                    discount_value=serializer.validated_data['discount_value'],
                    apply_to=serializer.validated_data.get('apply_to', 'all'),
                    code=serializer.validated_data.get('code'),
                    description=serializer.validated_data.get('description', ''),
                    product_id=_extract_id(serializer.validated_data.get('product')),
                    category_id=_extract_id(serializer.validated_data.get('category')),
                    branch_id=_extract_id(serializer.validated_data.get('branch')),
                    min_purchase_amount=serializer.validated_data.get('min_purchase_amount'),
                    max_discount_amount=serializer.validated_data.get('max_discount_amount'),
                    start_date=serializer.validated_data.get('start_date'),
                    end_date=serializer.validated_data.get('end_date'),
                    usage_limit=serializer.validated_data.get('usage_limit'),
                    created_by=request.user,
                )
                log_activity(
                    activity_type=ActivityType.CUSTOM,
                    user=request.user,
                    description=f'Created discount: {discount.name}',
                    request=request,
                    related_object=discount,
                )
                return Response(DiscountSerializer(discount).data, status=status.HTTP_201_CREATED)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DiscountDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: DiscountSerializer})
    def get(self, request, pk):
        """Retrieve discount details."""
        try:
            discount = Discount.objects.select_related('product', 'category', 'branch', 'created_by').get(pk=pk)
        except Discount.DoesNotExist:
            return Response({'detail': 'Discount not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = DiscountSerializer(discount)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        request_body=DiscountSerializer,
        responses={200: DiscountSerializer}
    )
    def put(self, request, pk):
        """Update a discount."""
        try:
            discount = Discount.objects.get(pk=pk)
        except Discount.DoesNotExist:
            return Response({'detail': 'Discount not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = DiscountSerializer(discount, data=request.data, partial=True)
        if serializer.is_valid():
            try:
                updated_discount = services.update_discount(
                    discount_id=pk,
                    name=serializer.validated_data.get('name'),
                    description=serializer.validated_data.get('description'),
                    discount_type=serializer.validated_data.get('discount_type'),
                    discount_value=serializer.validated_data.get('discount_value'),
                    apply_to=serializer.validated_data.get('apply_to'),
                    code=serializer.validated_data.get('code'),
                    product_id=_extract_id(serializer.validated_data.get('product')),
                    category_id=_extract_id(serializer.validated_data.get('category')),
                    branch_id=_extract_id(serializer.validated_data.get('branch')),
                    min_purchase_amount=serializer.validated_data.get('min_purchase_amount'),
                    max_discount_amount=serializer.validated_data.get('max_discount_amount'),
                    start_date=serializer.validated_data.get('start_date'),
                    end_date=serializer.validated_data.get('end_date'),
                    usage_limit=serializer.validated_data.get('usage_limit'),
                    is_active=serializer.validated_data.get('is_active'),
                )
                log_activity(
                    activity_type=ActivityType.CUSTOM,
                    user=request.user,
                    description=f'Updated discount: {updated_discount.name}',
                    request=request,
                    related_object=updated_discount,
                )
                return Response(DiscountSerializer(updated_discount).data, status=status.HTTP_200_OK)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @swagger_auto_schema(responses={204: 'No Content'})
    def delete(self, request, pk):
        """Delete a discount."""
        try:
            discount = Discount.objects.get(pk=pk)
        except Discount.DoesNotExist:
            return Response({'detail': 'Discount not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        discount_name = discount.name
        discount.delete()
        
        log_activity(
            activity_type=ActivityType.CUSTOM,
            user=request.user,
            description=f'Deleted discount: {discount_name}',
            request=request,
        )
        
        return Response(status=status.HTTP_204_NO_CONTENT)


class SaleApplyDiscountView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=ApplyDiscountSerializer,
        responses={200: SaleSerializer}
    )
    def post(self, request, pk):
        """Apply a discount to a sale."""
        serializer = ApplyDiscountSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            sale = Sale.objects.select_related('branch').prefetch_related('items').get(pk=pk)
        except Sale.DoesNotExist:
            return Response({'detail': 'Sale not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        if sale.status != 'pending':
            return Response(
                {'detail': 'Discount can only be applied to pending sales.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            discount_amount, discount = services.apply_discount_to_sale(
                sale=sale,
                discount_code=serializer.validated_data.get('discount_code'),
                discount_id=serializer.validated_data.get('discount_id'),
            )
            log_activity(
                activity_type=ActivityType.SALE_UPDATED,
                user=request.user,
                description=f'Applied discount {discount.name if discount else "N/A"} to sale {sale.sale_number}',
                request=request,
                related_object=sale,
            )
            return Response({
                'sale': SaleSerializer(sale).data,
                'discount_applied': discount_amount,
                'discount': DiscountSerializer(discount).data if discount else None,
            }, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class GetAvailableDiscountsForSaleView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('product_ids', openapi.IN_QUERY, type=openapi.TYPE_STRING, 
                            description='Comma-separated product IDs (e.g., "1,2,3")'),
            openapi.Parameter('sale_total', openapi.IN_QUERY, type=openapi.TYPE_NUMBER, 
                            description='Total sale amount for min_purchase validation'),
        ],
        responses={200: DiscountSerializer(many=True)}
    )
    def get(self, request):
        """Get available discounts for a sale (for offline caching)."""
        branch_id = request.query_params.get('branch_id')
        product_ids_str = request.query_params.get('product_ids', '')
        sale_total = request.query_params.get('sale_total')
        
        if not branch_id:
            return Response({'detail': 'branch_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Parse product IDs
        product_ids = []
        if product_ids_str:
            try:
                product_ids = [int(pid.strip()) for pid in product_ids_str.split(',') if pid.strip()]
            except ValueError:
                return Response({'detail': 'Invalid product_ids format.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Get available discounts
        discounts = services.get_available_discounts(branch_id=int(branch_id))
        
        # Filter by products if provided
        if product_ids:
            from products.models import Product
            products = Product.objects.filter(id__in=product_ids).select_related('category')
            product_categories = set(p.category_id for p in products if p.category_id)
            
            filtered_discounts = []
            for discount in discounts:
                # Check if discount applies to any of the products
                if discount.apply_to == 'all':
                    filtered_discounts.append(discount)
                elif discount.apply_to == 'product' and discount.product_id in product_ids:
                    filtered_discounts.append(discount)
                elif discount.apply_to == 'category' and discount.category_id in product_categories:
                    filtered_discounts.append(discount)
                elif discount.apply_to == 'min_purchase':
                    # Check if sale_total meets minimum
                    if sale_total:
                        try:
                            total = Decimal(str(sale_total))
                            if discount.min_purchase_amount and total >= discount.min_purchase_amount:
                                filtered_discounts.append(discount)
                        except (ValueError, TypeError):
                            pass
                    else:
                        filtered_discounts.append(discount)  # Include if no total provided
                elif discount.apply_to == 'branch' and discount.branch_id == int(branch_id):
                    filtered_discounts.append(discount)
            
            discounts = filtered_discounts
        
        serializer = DiscountSerializer(discounts, many=True)
        return Response(serializer.data)


class ValidateDiscountCodeView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('code', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('sale_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False),
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False),
        ],
        responses={200: 'Discount validation result'}
    )
    def get(self, request):
        """Validate a discount code."""
        code = request.query_params.get('code')
        sale_id = request.query_params.get('sale_id')
        branch_id = request.query_params.get('branch_id')
        
        if not code:
            return Response({'detail': 'Discount code is required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            discount = Discount.objects.get(code=code)
        except Discount.DoesNotExist:
            return Response({
                'valid': False,
                'message': 'Discount code not found',
            }, status=status.HTTP_200_OK)
        
        # If sale_id provided, validate against that sale
        if sale_id:
            try:
                sale = Sale.objects.select_related('branch').prefetch_related('items').get(pk=sale_id)
                items_data = [
                    {
                        'product_id': item.product.id,
                        'quantity': item.quantity,
                        'unit_price': item.unit_price,
                        'subtotal': item.subtotal,
                    }
                    for item in sale.items.all()
                ]
                can_apply, message = discount.can_apply_to_sale(sale, items_data)
                return Response({
                    'valid': can_apply,
                    'message': message,
                    'discount': DiscountSerializer(discount).data if can_apply else None,
                }, status=status.HTTP_200_OK)
            except Sale.DoesNotExist:
                return Response({'detail': 'Sale not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        # Otherwise, just check if discount is valid
        is_valid = discount.is_valid()
        return Response({
            'valid': is_valid,
            'message': 'Discount is valid' if is_valid else 'Discount is not active or has expired',
            'discount': DiscountSerializer(discount).data if is_valid else None,
        }, status=status.HTTP_200_OK)


class CashReceivedListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('cashier_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                            description='Filter by cashier ID'),
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                            description='Filter by branch ID'),
            openapi.Parameter('date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date',
                            description='Filter by date (YYYY-MM-DD)'),
        ],
        responses={200: CashReceivedSerializer(many=True)}
    )
    def get(self, request):
        """List cash received entries. Managers can view all entries for their branch."""
        cash_received = CashReceived.objects.select_related('cashier', 'branch', 'entered_by').all()
        
        # Filter by role
        if request.user.role in ['branch_manager', 'cashier']:
            profile = getattr(request.user, 'profile', None)
            branch = None
            if profile:
                employee = profile.first()
                if employee:
                    branch = employee.branch
            if branch:
                cash_received = cash_received.filter(branch=branch)
            else:
                cash_received = cash_received.none()
        
        # Apply filters
        cashier_id = request.query_params.get('cashier_id')
        branch_id = request.query_params.get('branch_id')
        date_str = request.query_params.get('date')
        
        if cashier_id:
            cash_received = cash_received.filter(cashier_id=cashier_id)
        if branch_id:
            cash_received = cash_received.filter(branch_id=branch_id)
        if date_str:
            date = parse_date(date_str)
            if date:
                cash_received = cash_received.filter(date=date)
        
        serializer = CashReceivedSerializer(cash_received.order_by('-date', '-created_at'), many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        request_body=CashReceivedCreateSerializer,
        responses={201: CashReceivedSerializer}
    )
    def post(self, request):
        """Create or update a cash received entry. Only managers can create entries."""
        # Check if user is a manager
        if request.user.role not in ['branch_manager', 'admin', 'owner']:
            return Response(
                {'detail': 'Only managers can enter cash received.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = CashReceivedCreateSerializer(data=request.data)
        if serializer.is_valid():
            try:
                from django.utils.dateparse import parse_date as parse_date_func
                from decimal import Decimal
                
                cashier_id = serializer.validated_data['cashier'].id
                branch_id = serializer.validated_data['branch'].id
                date = serializer.validated_data['date']
                total_amount = serializer.validated_data['total_amount']
                type_of_payment = serializer.validated_data.get('type_of_payment', 'usd_cash')
                notes = serializer.validated_data.get('notes', '')
                
                # Verify manager has access to the branch
                if request.user.role == 'branch_manager':
                    profile = getattr(request.user, 'profile', None)
                    if profile:
                        employee = profile.first()
                        if employee and employee.branch_id != branch_id:
                            return Response(
                                {'detail': 'You can only enter cash received for your branch.'},
                                status=status.HTTP_403_FORBIDDEN
                            )
                
                cash_received = services.create_or_update_cash_received(
                    cashier_id=cashier_id,
                    branch_id=branch_id,
                    date=date,
                    total_amount=total_amount,
                    entered_by=request.user,
                    type_of_payment=type_of_payment,
                    notes=notes
                )
                
                log_activity(
                    activity_type=ActivityType.SALE_CREATED,  # Using existing activity type
                    user=request.user,
                    description=f'Entered cash received: ${total_amount} for cashier {cash_received.cashier.email} on {date}',
                    request=request,
                )
                
                return Response(
                    CashReceivedSerializer(cash_received).data,
                    status=status.HTTP_201_CREATED
                )
            except Exception as e:
                return Response(
                    {'detail': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        request_body=CashReceivedUpdateSerializer,
        responses={200: CashReceivedSerializer}
    )
    def put(self, request, pk):
        """Update a cash received entry. Only branch managers can update, and only on the same day."""
        # Check if user is a manager
        if request.user.role not in ['branch_manager', 'admin', 'owner']:
            return Response(
                {'detail': 'Only managers can update cash received entries.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            # Get the cash received entry
            cash_received = CashReceived.objects.select_related('cashier', 'branch', 'entered_by').get(pk=pk)
        except CashReceived.DoesNotExist:
            return Response(
                {'detail': 'Cash received entry not found.'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Security check: Only allow updates on the same day the cash was received
        today = timezone.now().date()
        if cash_received.date != today:
            return Response(
                {'detail': 'Cash received entries can only be updated on the same day they were recorded.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Verify manager has access to the branch
        if request.user.role == 'branch_manager':
            profile = getattr(request.user, 'profile', None)
            if profile:
                employee = profile.first()
                if employee and employee.branch_id != cash_received.branch_id:
                    return Response(
                        {'detail': 'You can only update cash received entries for your branch.'},
                        status=status.HTTP_403_FORBIDDEN
                    )
        
        # Validate and update
        serializer = CashReceivedUpdateSerializer(cash_received, data=request.data, partial=True)
        if serializer.is_valid():
            try:
                # Track changes for audit log
                old_amount = cash_received.total_amount
                old_notes = cash_received.notes
                
                # Update the entry
                serializer.save()
                
                # Refresh from database to get updated values
                cash_received.refresh_from_db()
                
                # Log the activity
                changes = {}
                if old_amount != cash_received.total_amount:
                    changes['total_amount'] = {
                        'old': str(old_amount),
                        'new': str(cash_received.total_amount)
                    }
                if old_notes != cash_received.notes:
                    changes['notes'] = {
                        'old': old_notes,
                        'new': cash_received.notes
                    }
                
                log_activity(
                    activity_type=ActivityType.SALE_UPDATED,  # Using existing activity type
                    user=request.user,
                    description=f'Updated cash received entry (ID: {cash_received.id}) for cashier {cash_received.cashier.email if cash_received.cashier else "Unknown"} on {cash_received.date}',
                    request=request,
                    related_object=cash_received,
                    metadata={'changes': changes} if changes else None
                )
                
                return Response(
                    CashReceivedSerializer(cash_received).data,
                    status=status.HTTP_200_OK
                )
            except Exception as e:
                return Response(
                    {'detail': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CashVarianceView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('cashier_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                            description='Cashier ID to calculate variance for'),
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                            description='Branch ID to filter by'),
            openapi.Parameter('date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date',
                            description='Date to calculate variance for (YYYY-MM-DD). Defaults to today.'),
        ],
        responses={200: 'Cash variance calculation'}
    )
    def get(self, request):
        """Calculate variance between total sales and cash received for a cashier."""
        from django.utils.dateparse import parse_date as parse_date_func
        
        cashier_id = request.query_params.get('cashier_id')
        branch_id = request.query_params.get('branch_id')
        date_str = request.query_params.get('date')
        
        # Parse date
        date = None
        if date_str:
            date = parse_date_func(date_str)
            if not date:
                return Response(
                    {'detail': 'Invalid date format. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # If cashier_id not provided and user is cashier, use their own ID
        if not cashier_id and request.user.role == 'cashier':
            cashier_id = request.user.id
        
        # If branch_id not provided and user is branch_manager, use their branch
        if not branch_id and request.user.role == 'branch_manager':
            profile = getattr(request.user, 'profile', None)
            if profile:
                employee = profile.first()
                if employee and employee.branch:
                    branch_id = employee.branch.id
        
        try:
            variance_data = services.calculate_cash_variance(
                cashier_id=int(cashier_id) if cashier_id else None,
                branch_id=int(branch_id) if branch_id else None,
                date=date
            )
            
            return Response(variance_data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class ExchangeRateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: ExchangeRateSerializer})
    def get(self, request):
        """Retrieve the current exchange rate."""
        exchange_rate = services.get_current_exchange_rate()
        if not exchange_rate:
            return Response({'detail': 'Exchange rate not set.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ExchangeRateSerializer(exchange_rate).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        request_body=ExchangeRateSerializer,
        responses={
            201: ExchangeRateSerializer,
            400: 'Bad Request - Exchange rate already exists or validation error'
        }
    )
    def post(self, request):
        """Create a new exchange rate. Admins/owners only. Use PUT to update existing rate."""
        if getattr(request.user, 'role', None) not in {'admin', 'owner'}:
            return Response(
                {'detail': 'Only admins or owners can create the exchange rate.'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = ExchangeRateSerializer(data=request.data)
        if serializer.is_valid():
            existing = services.get_current_exchange_rate()
            if existing:
                return Response(
                    {'detail': 'Exchange rate already exists. Use PUT method to update it.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            exchange_rate = services.set_exchange_rate(serializer.validated_data['current_rate'])

            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f'Created exchange rate: {exchange_rate.current_rate}',
                request=request,
                related_object=exchange_rate,
            )

            return Response(ExchangeRateSerializer(exchange_rate).data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        request_body=ExchangeRateSerializer,
        responses={
            200: ExchangeRateSerializer,
            404: 'Exchange rate not found',
            400: 'Bad Request - Validation error'
        }
    )
    def put(self, request):
        """Update the current exchange rate. Admins/owners only."""
        if getattr(request.user, 'role', None) not in {'admin', 'owner'}:
            return Response(
                {'detail': 'Only admins or owners can update the exchange rate.'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = ExchangeRateSerializer(data=request.data)
        if serializer.is_valid():
            existing = services.get_current_exchange_rate()
            if not existing:
                return Response(
                    {'detail': 'Exchange rate does not exist. Use POST method to create it.'},
                    status=status.HTTP_404_NOT_FOUND
                )

            old_rate = existing.current_rate
            exchange_rate = services.set_exchange_rate(serializer.validated_data['current_rate'])

            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f'Updated exchange rate from {old_rate} to {exchange_rate.current_rate}',
                request=request,
                related_object=exchange_rate,
            )

            return Response(ExchangeRateSerializer(exchange_rate).data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        request_body=ExchangeRateSerializer,
        responses={
            200: ExchangeRateSerializer,
            201: ExchangeRateSerializer,
            400: 'Bad Request - Validation error'
        }
    )
    def patch(self, request):
        """Create or update the exchange rate. Admins/owners only. Creates if not exists, updates if exists."""
        if getattr(request.user, 'role', None) not in {'admin', 'owner'}:
            return Response(
                {'detail': 'Only admins or owners can create or update the exchange rate.'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = ExchangeRateSerializer(data=request.data)
        if serializer.is_valid():
            existing = services.get_current_exchange_rate()
            old_rate = existing.current_rate if existing else None
            
            exchange_rate = services.set_exchange_rate(serializer.validated_data['current_rate'])

            action = 'Updated' if existing else 'Created'
            description = (
                f'{action} exchange rate'
                + (f' from {old_rate} to {exchange_rate.current_rate}' if old_rate else f': {exchange_rate.current_rate}')
            )

            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=description,
                request=request,
                related_object=exchange_rate,
            )

            status_code = status.HTTP_200_OK if existing else status.HTTP_201_CREATED
            return Response(ExchangeRateSerializer(exchange_rate).data, status=status_code)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
