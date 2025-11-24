from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from .serializers import (
    ProductSerializer, CategorySerializer, UnitSerializer, CreateProductSerializer,
    BulkCreateProductSerializer
)
from .models import Barcode
from django.db import models
from .models import Product, Category, Unit
from organization.models import Branch, Warehouse
from . import services
from shared.audit import log_activity, ActivityType
from accounts.permissions import IsAdminOrOwner


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
        
        serializer = ProductSerializer(products, many=True)
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
            
            product = services.create_product(**data)
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
            
            return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ProductDetailView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, pk):
        """Get product details with stock information"""
        try:
            product = Product.objects.get(pk=pk)
            branch_id = request.query_params.get('branch_id')
            warehouse_id = request.query_params.get('warehouse_id')
            
            serializer = ProductSerializer(product, context={'warehouse_id': warehouse_id})
            return Response(serializer.data)
        except Product.DoesNotExist:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
    
    @swagger_auto_schema(request_body=ProductSerializer, responses={200: ProductSerializer})
    def put(self, request, pk):
        """Update a product"""
        try:
            product = Product.objects.get(pk=pk)
            serializer = ProductSerializer(product, data=request.data)
            if serializer.is_valid():
                serializer.save()
                log_activity(
                    activity_type=ActivityType.PRODUCT_UPDATED,
                    user=request.user,
                    description=f"Updated product: {product.name}",
                    request=request,
                    related_object=product,
                )
                return Response(serializer.data)
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
