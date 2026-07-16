from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import io

from .serializers import (
    ProductSerializer, CategorySerializer, UnitSerializer, CreateProductSerializer,
    BulkCreateProductSerializer, BarcodeSerializer, ImportProductsFromStockSheetSerializer
)
from .models import Barcode
from django.db import models
from .models import Product, Category, Unit
from organization.models import Branch, Warehouse
from . import services
from shared.audit import log_activity, ActivityType
from accounts.permissions import IsAdminOrOwner

# Try to import openpyxl for Excel file reading
try:
    from openpyxl import load_workbook
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False


class CategoryListView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """List all categories"""
        categories = Category.objects.all()
        serializer = CategorySerializer(categories, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(request_body=CategorySerializer, responses={201: CategorySerializer})
    def post(self, request):
        """Create a new category"""
        serializer = CategorySerializer(data=request.data)
        if serializer.is_valid():
            category = serializer.save()
            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f"Created category: {category.name}",
                request=request,
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ImportProductsFromStockSheetView(APIView):
    """
    Import products (and suppliers) from an Excel sheet using stock columns:
    product name, description, selling prices, cost per unit, total quantity, supplier name, supplier email address.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=ImportProductsFromStockSheetSerializer,
        responses={201: openapi.Response(description='Products imported')}
    )
    def post(self, request):
        serializer = ImportProductsFromStockSheetSerializer(data=request.data)
        if serializer.is_valid():
            try:
                result = services.import_products_from_stock_sheet(
                    file_obj=serializer.validated_data['file'],
                    notes=serializer.validated_data.get('notes', ''),
                    created_by=request.user
                )

                log_activity(
                    activity_type=ActivityType.CUSTOM,
                    user=request.user,
                    description=f"Imported products from stock sheet: {result['summary']['successful']} succeeded, {result['summary']['failed']} failed",
                    request=request,
                )

                return Response(result, status=status.HTTP_201_CREATED)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UnitListView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """List all units"""
        units = Unit.objects.all()
        serializer = UnitSerializer(units, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(request_body=UnitSerializer, responses={201: UnitSerializer})
    def post(self, request):
        """Create a new unit"""
        serializer = UnitSerializer(data=request.data)
        if serializer.is_valid():
            unit = serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ProductListView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """List all products"""
        branch_id = request.query_params.get('branch_id')
        products = Product.objects.all()
        
        serializer = ProductSerializer(products, many=True, context={'request': request})
        return Response(serializer.data)
    
    @swagger_auto_schema(request_body=CreateProductSerializer, responses={201: ProductSerializer})
    def post(self, request):
        """Create a new product and add to warehouse with stock entry. Uses main warehouse if initial_warehouse_id not provided."""
        serializer = CreateProductSerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.validated_data.copy()
            initial_warehouse_id = data.pop('initial_warehouse_id', None)
            initial_quantity = data.pop('initial_quantity')
            initial_purchase_price = data.pop('initial_purchase_price', None)
            supplier_id = data.pop('supplier_id', None)
            batch_number = data.pop('batch_number', None)  # Auto-generated if None
            image = data.pop('image', None)  # Extract image if provided
            
            # Convert category and unit to IDs if they are instances
            if 'category' in data and data['category'] is not None:
                if hasattr(data['category'], 'id'):
                    data['category'] = data['category'].id
                elif not isinstance(data['category'], int):
                    data['category'] = None
            
            if 'unit' in data and data['unit'] is not None:
                if hasattr(data['unit'], 'id'):
                    data['unit'] = data['unit'].id
                elif not isinstance(data['unit'], int):
                    data['unit'] = None
            
            # If no warehouse specified, use main warehouse
            if not initial_warehouse_id:
                try:
                    main_warehouse = services.get_main_warehouse()
                    initial_warehouse_id = main_warehouse.id
                except Warehouse.DoesNotExist:
                    return Response(
                        {'detail': 'No main warehouse found. Please mark a warehouse as main (is_main=True) or provide initial_warehouse_id.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Create product with image if provided
            product = services.create_product(image=image, created_by=request.user, **data)
            # Use purchase_price from product if not provided
            purchase_price = initial_purchase_price
            
            services.add_product_to_main_warehouse(
                product=product,
                warehouse_id=initial_warehouse_id,
                quantity=initial_quantity,
                purchase_price=purchase_price,
                supplier_id=supplier_id,
                batch_number=batch_number,
                created_by=request.user
            )
            
            log_activity(
                activity_type=ActivityType.PRODUCT_CREATED,
                user=request.user,
                description=f"Created product: {product.name} (SKU: {product.sku}) with {initial_quantity} units at ${purchase_price} per unit",
                request=request,
                related_object=product,
            )
            
            return Response(ProductSerializer(product, context={'request': request}).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ProductDetailView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, pk):
        """Get product details with stock information"""
        try:
            product = Product.objects.get(pk=pk)
            branch_id = request.query_params.get('branch_id')
            warehouse_id = request.query_params.get('warehouse_id')
            
            serializer = ProductSerializer(product, context={'request': request, 'warehouse_id': warehouse_id})
            return Response(serializer.data)
        except Product.DoesNotExist:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
    
    @swagger_auto_schema(request_body=ProductSerializer, responses={200: ProductSerializer})
    def put(self, request, pk):
        """Update a product"""
        try:
            product = Product.objects.get(pk=pk)
            data = request.data.copy()
            
            # Convert category and unit to IDs if they are instances
            if 'category' in data and data['category'] is not None:
                if hasattr(data['category'], 'id'):
                    data['category'] = data['category'].id
                elif not isinstance(data['category'], (int, str)):
                    data['category'] = None
            
            if 'unit' in data and data['unit'] is not None:
                if hasattr(data['unit'], 'id'):
                    data['unit'] = data['unit'].id
                elif not isinstance(data['unit'], (int, str)):
                    data['unit'] = None
            
            serializer = ProductSerializer(product, data=data, partial=False)
            if serializer.is_valid():
                serializer.save()
                log_activity(
                    activity_type=ActivityType.PRODUCT_UPDATED,
                    user=request.user,
                    description=f"Updated product: {product.name}",
                    request=request,
                    related_object=product,
                )
                return Response(ProductSerializer(product, context={'request': request}).data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Product.DoesNotExist:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
    
    def delete(self, request, pk):
        """Delete a product"""
        try:
            product = Product.objects.get(pk=pk)
            product.delete()
            log_activity(
                activity_type=ActivityType.PRODUCT_DELETED,
                user=request.user,
                description=f"Deleted product: {product.name}",
                request=request,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Product.DoesNotExist:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)


class BulkCreateProductView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        request_body=BulkCreateProductSerializer,
        responses={
            201: openapi.Response(
                description='Products created',
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
                                'failed': openapi.Schema(type=openapi.TYPE_INTEGER)
                            }
                        )
                    }
                )
            )
        }
    )
    def post(self, request):
        """Bulk create multiple products with their initial stock entries"""
        serializer = BulkCreateProductSerializer(data=request.data)
        if serializer.is_valid():
            products_data = serializer.validated_data['products']
            created_products, errors = services.bulk_create_products(
                products_data=products_data,
                created_by=request.user
            )
            
            # Log activity
            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f"Bulk created {len(created_products)} products",
                request=request,
            )
            
            # Serialize created products
            created_serializer = ProductSerializer(created_products, many=True)
            
            return Response({
                'created': created_serializer.data,
                'errors': errors,
                'summary': {
                    'total': len(products_data),
                    'successful': len(created_products),
                    'failed': len(errors)
                }
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BarcodeLookupView(APIView):
    """
    Lookup product by barcode value (scanned from barcode image).
    Returns product details with stock information.
    """
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'barcode',
                openapi.IN_QUERY,
                description='Barcode value (scanned from barcode image, typically product SKU)',
                type=openapi.TYPE_STRING,
                required=True
            ),
            openapi.Parameter(
                'branch_id',
                openapi.IN_QUERY,
                description='Branch ID to get stock information for that branch',
                type=openapi.TYPE_INTEGER,
                required=False
            ),
            openapi.Parameter(
                'warehouse_id',
                openapi.IN_QUERY,
                description='Warehouse ID to get stock information for that warehouse',
                type=openapi.TYPE_INTEGER,
                required=False
            ),
        ],
        responses={
            200: openapi.Response('Product found with stock information'),
            404: openapi.Response('Product not found'),
        }
    )
    def get(self, request):
        """Lookup product by barcode value"""
        barcode_value = request.query_params.get('barcode')
        branch_id = request.query_params.get('branch_id')
        warehouse_id = request.query_params.get('warehouse_id')
        
        if not barcode_value:
            return Response(
                {'error': 'Barcode parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Convert to int if provided
        branch_id = int(branch_id) if branch_id else None
        warehouse_id = int(warehouse_id) if warehouse_id else None
        
        # Get product with stock info
        product_info = services.get_product_with_stock_by_barcode(
            barcode_value=barcode_value,
            branch_id=branch_id,
            warehouse_id=warehouse_id
        )
        
        if not product_info:
            return Response(
                {'error': 'Product not found or inactive'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Serialize response
        product_serializer = ProductSerializer(product_info['product'])
        
        response_data = {
            'product': product_serializer.data,
            'barcode': {
                'id': product_info['barcode'].id,
                'barcode': product_info['barcode'].barcode,
                'barcode_image': product_info['barcode'].barcode_image.url if product_info['barcode'].barcode_image else None,
                'is_primary': product_info['barcode'].is_primary,
            }
        }
        
        # Add stock information if available
        if 'available_stock' in product_info:
            response_data['available_stock'] = str(product_info['available_stock'])
        
        if 'selling_price' in product_info and product_info['selling_price']:
            response_data['selling_price'] = str(product_info['selling_price'])
        
        if 'purchase_price' in product_info and product_info['purchase_price']:
            response_data['purchase_price'] = str(product_info['purchase_price'])
        
        return Response(response_data, status=status.HTTP_200_OK)


class BarcodeListView(APIView):
    """
    List all barcodes with their product details for display/printing.
    """
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'product_id',
                openapi.IN_QUERY,
                description='Filter by product ID',
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                'is_primary',
                openapi.IN_QUERY,
                description='Filter by primary barcodes only (true/false)',
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
        ],
        responses={200: BarcodeSerializer(many=True)}
    )
    def get(self, request):
        """
        Return all barcodes with associated product details and stock price information.
        This is ideal for frontend barcode lists and printing labels.
        Includes purchase_price and selling_price from the most recent stock entry.
        """
        from stock.models import StockEntry, BranchStock
        from django.db.models import Prefetch
        
        barcodes = Barcode.objects.select_related('product').all()
        
        product_id = request.query_params.get('product_id')
        is_primary = request.query_params.get('is_primary')
        
        if product_id:
            barcodes = barcodes.filter(product_id=product_id)
        
        if is_primary is not None:
            # Accept 'true'/'false' strings from query params
            val = str(is_primary).lower()
            if val in ['true', '1', 'yes']:
                barcodes = barcodes.filter(is_primary=True)
            elif val in ['false', '0', 'no']:
                barcodes = barcodes.filter(is_primary=False)
        
        # Prefetch stock entries to optimize queries for price information
        # Get the most recent stock entry for each product
        warehouse_stock_prefetch = Prefetch(
            'product__stock_entries',
            queryset=StockEntry.objects.filter(quantity__gt=0)
                .select_related('warehouse')
                .order_by('-received_date', '-created_at'),
            to_attr='recent_warehouse_stock'
        )
        
        branch_stock_prefetch = Prefetch(
            'product__branch_stock_entries',
            queryset=BranchStock.objects.filter(quantity__gt=0)
                .select_related('branch')
                .order_by('-received_date', '-created_at'),
            to_attr='recent_branch_stock'
        )
        
        barcodes = barcodes.prefetch_related(
            warehouse_stock_prefetch,
            branch_stock_prefetch
        ).order_by('-is_primary', 'product__name', 'barcode')
        
        serializer = BarcodeSerializer(barcodes, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class RegenerateBarcodeView(APIView):
    """
    Regenerate barcode images with current selling prices from the logged-in user's branch stock.
    Supports regenerating a single barcode, all barcodes for a product, or all barcodes.
    """
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'barcode_id',
                openapi.IN_QUERY,
                description='Regenerate specific barcode by ID',
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                'product_id',
                openapi.IN_QUERY,
                description='Regenerate all barcodes for a specific product',
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                'all',
                openapi.IN_QUERY,
                description='Regenerate all barcodes (use with caution)',
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
        ],
        responses={
            200: openapi.Response(
                description='Barcode regeneration results',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'success': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                        'regenerated_count': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'failed_count': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'regenerated_barcodes': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_INTEGER)
                        ),
                        'failed_barcodes': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_INTEGER)
                        ),
                    }
                )
            ),
            400: 'Bad request - invalid parameters',
            404: 'Barcode or product not found',
        }
    )
    def post(self, request):
        """
        Regenerate barcode images with current prices.
        
        Query parameters:
        - barcode_id: Regenerate specific barcode by ID
        - product_id: Regenerate all barcodes for a product
        - all: Regenerate all barcodes (requires admin permission)
        
        At least one parameter must be provided.
        """
        barcode_id = request.query_params.get('barcode_id')
        product_id = request.query_params.get('product_id')
        regenerate_all = request.query_params.get('all', '').lower() in ['true', '1', 'yes']
        
        # Validate that at least one parameter is provided
        if not any([barcode_id, product_id, regenerate_all]):
            return Response(
                {'error': 'At least one parameter (barcode_id, product_id, or all) must be provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check permission for bulk regeneration
        if regenerate_all:
            if request.user.role not in ['admin', 'owner', 'cashier', 'branch_manager']:
                return Response(
                    {'error': 'Admin permission required to regenerate all barcodes'},
                    status=status.HTTP_403_FORBIDDEN
                )
        
        regenerated_count = 0
        failed_count = 0
        regenerated_barcodes = []
        failed_barcodes = []
        
        try:
            if barcode_id:
                # Regenerate single barcode
                try:
                    barcode = Barcode.objects.get(pk=barcode_id)
                    if services.regenerate_barcode_image(barcode, user=request.user):
                        regenerated_count += 1
                        regenerated_barcodes.append(barcode.id)
                        
                    else:
                        failed_count += 1
                        failed_barcodes.append(barcode.id)
                except Barcode.DoesNotExist:
                    return Response(
                        {'error': f'Barcode with ID {barcode_id} not found'},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
            elif product_id:
                # Regenerate all barcodes for a product
                try:
                    product = Product.objects.get(pk=product_id)
                    barcodes = Barcode.objects.filter(product=product)
                    
                    if not barcodes.exists():
                        return Response(
                            {'error': f'No barcodes found for product ID {product_id}'},
                            status=status.HTTP_404_NOT_FOUND
                        )
                    
                    for barcode in barcodes:
                        if services.regenerate_barcode_image(barcode, user=request.user):
                            regenerated_count += 1
                            regenerated_barcodes.append(barcode.id)
                        else:
                            failed_count += 1
                            failed_barcodes.append(barcode.id)
                    
                except Product.DoesNotExist:
                    return Response(
                        {'error': f'Product with ID {product_id} not found'},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
            elif regenerate_all:
                # Regenerate all barcodes
                barcodes = Barcode.objects.all()
                total_count = barcodes.count()
                
                if total_count == 0:
                    return Response(
                        {'error': 'No barcodes found'},
                        status=status.HTTP_404_NOT_FOUND
                    )
                
                for barcode in barcodes:
                    if services.regenerate_barcode_image(barcode, user=request.user):
                        regenerated_count += 1
                        regenerated_barcodes.append(barcode.id)
                    else:
                        failed_count += 1
                        failed_barcodes.append(barcode.id)
                
            
            message = f'Successfully regenerated {regenerated_count} barcode(s)'
            if failed_count > 0:
                message += f', {failed_count} failed'
            
            return Response({
                'success': True,
                'regenerated_count': regenerated_count,
                'failed_count': failed_count,
                'message': message,
                'regenerated_barcodes': regenerated_barcodes,
                'failed_barcodes': failed_barcodes,
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {'error': f'Error regenerating barcodes: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ExcelProductUploadView(APIView):
    """
    Upload products from Excel file.
    Expected columns (case-insensitive):
      - product name
      - description
      - selling prices
      - cost per unit
      - total quantity
      - supplier name
      - supplier email address
    If supplier does not exist it will be created; products are created if missing (description treated as unique key).
    """
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'file',
                openapi.IN_FORM,
                description='Excel file (.xlsx) containing product data',
                type=openapi.TYPE_FILE,
                required=True
            ),
            openapi.Parameter(
                'notes',
                openapi.IN_FORM,
                description='Optional notes for traceability',
                type=openapi.TYPE_STRING,
                required=False
            )
        ],
        responses={
            200: openapi.Response(
                description='Products imported successfully',
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
                                'failed': openapi.Schema(type=openapi.TYPE_INTEGER)
                            }
                        )
                    }
                )
            ),
            400: openapi.Response('Bad request - invalid file or format'),
        },
        consumes=['multipart/form-data']
    )
    def post(self, request):
        """Upload products from Excel file"""
        if not OPENPYXL_AVAILABLE:
            return Response(
                {'detail': 'openpyxl library is required for Excel upload. Install with: pip install openpyxl'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get uploaded file
        excel_file = request.FILES.get('file')
        if not excel_file:
            return Response(
                {'detail': 'Excel file is required. Please upload a .xlsx file.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate file extension
        if not excel_file.name.endswith(('.xlsx', '.xls')):
            return Response(
                {'detail': 'Invalid file format. Please upload a .xlsx or .xls file.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            result = services.import_products_from_stock_sheet(
                file_obj=excel_file,
                notes=request.data.get('notes', ''),
                created_by=request.user
            )

            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f"Imported products from Excel: {result['summary']['successful']} succeeded, {result['summary']['failed']} failed",
                request=request,
            )

            return Response(result, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'detail': f'Error processing Excel file: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
