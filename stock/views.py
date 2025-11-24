from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from decimal import Decimal
from django.db.models import Q

from .serializers import (
    SupplierSerializer,
    StockEntrySerializer, StockEntryGroupSerializer, StockAdjustmentSerializer,
    AddStockSerializer, RemoveStockSerializer, BulkAddStockSerializer,
    BranchStockSerializer, AddBranchStockSerializer, RemoveBranchStockSerializer,
    StockTransferSerializer, CreateStockTransferSerializer, BulkCreateStockTransferSerializer
)
from .models import Supplier, StockEntry, StockAdjustment, BranchStock, StockTransfer
from . import services
from shared.audit import log_activity, ActivityType
from accounts.permissions import IsAdminOrOwner



class ListCreateSupplierView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING, 
                            description='Search by name, email, or phone'),
        ],
        responses={200: SupplierSerializer(many=True)}
    )
    def get(self, request):
        """List all suppliers"""
        search = request.query_params.get('search')
        
        suppliers = Supplier.objects.all()
        
        if search:
            suppliers = suppliers.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search)
            )
        
        serializer = SupplierSerializer(suppliers, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        request_body=SupplierSerializer,
        responses={201: SupplierSerializer}
    )
    def post(self, request):
        """Create a new supplier"""
        serializer = SupplierSerializer(data=request.data)
        if serializer.is_valid():
            supplier = serializer.save()
            
            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f"Created supplier: {supplier.name}",
                request=request,
                related_object=supplier,
            )
            
            return Response(SupplierSerializer(supplier).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SupplierDetailView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        responses={200: SupplierSerializer}
    )
    def get(self, request, pk):
        """Get supplier details"""
        try:
            supplier = Supplier.objects.get(pk=pk)
            serializer = SupplierSerializer(supplier)
            return Response(serializer.data)
        except Supplier.DoesNotExist:
            return Response({'detail': 'Supplier not found.'}, status=status.HTTP_404_NOT_FOUND)
    
    @swagger_auto_schema(
        request_body=SupplierSerializer,
        responses={200: SupplierSerializer}
    )
    def put(self, request, pk):
        """Update supplier (full update)"""
        try:
            supplier = Supplier.objects.get(pk=pk)
            serializer = SupplierSerializer(supplier, data=request.data)
            if serializer.is_valid():
                serializer.save()
                
                log_activity(
                    activity_type=ActivityType.CUSTOM,
                    user=request.user,
                    description=f"Updated supplier: {supplier.name}",
                    request=request,
                    related_object=supplier,
                )
                
                return Response(SupplierSerializer(supplier).data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Supplier.DoesNotExist:
            return Response({'detail': 'Supplier not found.'}, status=status.HTTP_404_NOT_FOUND)
    
    @swagger_auto_schema(
        request_body=SupplierSerializer,
        responses={200: SupplierSerializer}
    )
    def patch(self, request, pk):
        """Update supplier (partial update)"""
        try:
            supplier = Supplier.objects.get(pk=pk)
            serializer = SupplierSerializer(supplier, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                
                log_activity(
                    activity_type=ActivityType.CUSTOM,
                    user=request.user,
                    description=f"Updated supplier: {supplier.name}",
                    request=request,
                    related_object=supplier,
                )
                
                return Response(SupplierSerializer(supplier).data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Supplier.DoesNotExist:
            return Response({'detail': 'Supplier not found.'}, status=status.HTTP_404_NOT_FOUND)
    
    @swagger_auto_schema(
        responses={204: 'Supplier deleted successfully'}
    )
    def delete(self, request, pk):
        """Delete supplier"""
        try:
            supplier = Supplier.objects.get(pk=pk)
            supplier_name = supplier.name
            
            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f"Deleted supplier: {supplier_name}",
                request=request,
            )
            
            supplier.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Supplier.DoesNotExist:
            return Response({'detail': 'Supplier not found.'}, status=status.HTTP_404_NOT_FOUND)
class StockEntryListView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('warehouse_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: StockEntrySerializer(many=True)}
    )
    def get(self, request):
        """List all stock entries"""
        warehouse_id = request.query_params.get('warehouse_id')
        product_id = request.query_params.get('product_id')
        
        entries = StockEntry.objects.all()
        
        if warehouse_id:
            entries = entries.filter(warehouse_id=warehouse_id)
        if product_id:
            entries = entries.filter(product_id=product_id)
        
        serializer = StockEntrySerializer(entries, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        request_body=AddStockSerializer,
        responses={201: StockEntrySerializer}
    )
    def post(self, request):
        """Add new stock to warehouse with purchase price (supports both single and multi-product)"""
        serializer = AddStockSerializer(data=request.data)
        if serializer.is_valid():
            try:
                items = serializer.validated_data.get('items', [])
                
                # Check if this is a multi-product entry
                if items and len(items) > 0:
                    # Multi-product entry
                    entry_group = services.add_multi_product_stock_to_warehouse(
                        warehouse_id=serializer.validated_data['warehouse_id'],
                        items_data=items,
                        reference_number=serializer.validated_data.get('reference_number') or None,
                        group_notes=serializer.validated_data.get('group_notes', ''),
                        created_by=request.user
                    )
                    
                    item_count = len(items)
                    total_quantity = sum(item['quantity'] for item in items)
                    log_activity(
                        activity_type=ActivityType.STOCK_ADDED,
                        user=request.user,
                        description=f"Added {item_count} products ({total_quantity} total units) to {entry_group.warehouse.name}",
                        request=request,
                    )
                    
                    return Response(StockEntryGroupSerializer(entry_group).data, status=status.HTTP_201_CREATED)
                else:
                    # Single product entry (backward compatible)
                    stock_entry = services.add_stock_to_warehouse(
                        product_id=serializer.validated_data['product_id'],
                        warehouse_id=serializer.validated_data['warehouse_id'],
                        quantity=serializer.validated_data['quantity'],
                        reorder_level=serializer.validated_data.get('reorder_level', 0),
                        purchase_price=serializer.validated_data['purchase_price'],
                        supplier_id=serializer.validated_data.get('supplier_id'),
                        batch_number=serializer.validated_data.get('batch_number') or None,
                        notes=serializer.validated_data.get('notes', ''),
                        created_by=request.user
                    )
                    
                    log_activity(
                        activity_type=ActivityType.STOCK_ADDED,
                        user=request.user,
                        description=f"Added {stock_entry.quantity} units of {stock_entry.product.name} to {stock_entry.warehouse.name} at ${stock_entry.purchase_price} per unit",
                        request=request,
                        related_object=stock_entry,
                    )
                    
                    return Response(StockEntrySerializer(stock_entry).data, status=status.HTTP_201_CREATED)
            except Exception as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BulkAddStockView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        request_body=BulkAddStockSerializer,
        responses={
            201: openapi.Response(
                description='Stock entries created',
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
        """Bulk add multiple stock entries to warehouse"""
        serializer = BulkAddStockSerializer(data=request.data)
        if serializer.is_valid():
            stock_entries_data = serializer.validated_data['stock_entries']
            created_entries, errors = services.bulk_add_stock_to_warehouse(
                stock_entries_data=stock_entries_data,
                created_by=request.user
            )
            
            # Log activity
            total_quantity = sum(entry.quantity for entry in created_entries)
            log_activity(
                activity_type=ActivityType.STOCK_ADDED,
                user=request.user,
                description=f"Bulk added {len(created_entries)} stock entries ({total_quantity} total units)",
                request=request,
            )
            
            # Serialize created entries
            created_serializer = StockEntrySerializer(created_entries, many=True)
            
            return Response({
                'created': created_serializer.data,
                'errors': errors,
                'summary': {
                    'total': len(stock_entries_data),
                    'successful': len(created_entries),
                    'failed': len(errors)
                }
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class StockAdjustmentListView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('warehouse_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('adjustment_type', openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: StockAdjustmentSerializer(many=True)}
    )
    def get(self, request):
        """List all stock adjustments"""
        warehouse_id = request.query_params.get('warehouse_id')
        product_id = request.query_params.get('product_id')
        adjustment_type = request.query_params.get('adjustment_type')
        
        adjustments = StockAdjustment.objects.all()
        
        if warehouse_id:
            adjustments = adjustments.filter(warehouse_id=warehouse_id)
        if product_id:
            adjustments = adjustments.filter(product_id=product_id)
        if adjustment_type:
            adjustments = adjustments.filter(adjustment_type=adjustment_type)
        
        serializer = StockAdjustmentSerializer(adjustments, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        request_body=RemoveStockSerializer,
        responses={201: StockAdjustmentSerializer}
    )
    def post(self, request):
        """Remove stock from warehouse"""
        serializer = RemoveStockSerializer(data=request.data)
        if serializer.is_valid():
            try:
                adjustment = services.remove_stock_from_warehouse(
                    product_id=serializer.validated_data['product_id'],
                    warehouse_id=serializer.validated_data['warehouse_id'],
                    quantity=serializer.validated_data['quantity'],
                    reason=serializer.validated_data.get('reason', ''),
                    adjustment_type=serializer.validated_data.get('adjustment_type', 'removal'),
                    created_by=request.user
                )
                
                log_activity(
                    activity_type=ActivityType.STOCK_REMOVED,
                    user=request.user,
                    description=f"Removed {abs(adjustment.quantity)} units of {adjustment.product.name} from {adjustment.warehouse.name}",
                    request=request,
                    related_object=adjustment,
                )
                
                return Response(StockAdjustmentSerializer(adjustment).data, status=status.HTTP_201_CREATED)
            except ValueError as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class StockSummaryView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('warehouse_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: 'Stock summary with batches'}
    )
    def get(self, request):
        """Get stock summary showing batches with different purchase prices"""
        warehouse_id = request.query_params.get('warehouse_id')
        product_id = request.query_params.get('product_id')
        
        if not warehouse_id:
            return Response(
                {'detail': 'warehouse_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            summary = services.get_warehouse_stock_summary(
                int(warehouse_id),
                int(product_id) if product_id else None
            )
            return Response(summary)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class StockValueView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('warehouse_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: 'Stock value information'}
    )
    def get(self, request):
        """Get stock value for warehouse"""
        warehouse_id = request.query_params.get('warehouse_id')
        product_id = request.query_params.get('product_id')
        
        if not warehouse_id:
            return Response(
                {'detail': 'warehouse_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            value_info = services.get_stock_value(
                int(warehouse_id),
                int(product_id) if product_id else None
            )
            return Response(value_info)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ========== Branch Stock Views ==========

class BranchStockListView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: BranchStockSerializer(many=True)}
    )
    def get(self, request):
        """List all branch stock entries
        
        Note: Stock can only be added to branches through transfers, not directly.
        Use the stock transfer API to move stock from warehouse to branch.
        """
        branch_id = request.query_params.get('branch_id')
        product_id = request.query_params.get('product_id')
        
        entries = BranchStock.objects.all()
        
        if branch_id:
            entries = entries.filter(branch_id=branch_id)
        if product_id:
            entries = entries.filter(product_id=product_id)
        
        serializer = BranchStockSerializer(entries, many=True)
        return Response(serializer.data)
    
    def post(self, request):
        """Direct stock addition to branch is not allowed.
        
        Stock can only be added to branches through stock transfers.
        Use POST /api/stock/transfers/ with transfer_type='warehouse_to_branch' or 'branch_to_branch'
        to add stock to a branch.
        """
        return Response(
            {
                'detail': 'Direct stock addition to branch is not allowed. '
                         'Please use stock transfers to move stock from warehouse to branch. '
                         'Use POST /api/stock/transfers/ with transfer_type="warehouse_to_branch"'
            },
            status=status.HTTP_403_FORBIDDEN
        )


class RemoveBranchStockView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        request_body=RemoveBranchStockSerializer,
        responses={200: 'Stock removed successfully'}
    )
    def post(self, request):
        """Remove stock from branch"""
        serializer = RemoveBranchStockSerializer(data=request.data)
        if serializer.is_valid():
            try:
                services.remove_stock_from_branch(
                    product_id=serializer.validated_data['product_id'],
                    branch_id=serializer.validated_data['branch_id'],
                    quantity=serializer.validated_data['quantity'],
                    reason=serializer.validated_data.get('reason', ''),
                    created_by=request.user
                )
                
                log_activity(
                    activity_type=ActivityType.STOCK_REMOVED,
                    user=request.user,
                    description=f"Removed {serializer.validated_data['quantity']} units of product {serializer.validated_data['product_id']} from branch {serializer.validated_data['branch_id']}",
                    request=request,
                )
                
                return Response({'detail': 'Stock removed successfully'}, status=status.HTTP_200_OK)
            except ValueError as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BranchStockSummaryView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: 'Branch stock summary with batches'}
    )
    def get(self, request):
        """Get branch stock summary showing batches with different purchase prices"""
        branch_id = request.query_params.get('branch_id')
        product_id = request.query_params.get('product_id')
        
        if not branch_id:
            return Response(
                {'detail': 'branch_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            summary = services.get_branch_stock_summary(
                int(branch_id),
                int(product_id) if product_id else None
            )
            return Response(summary)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class BranchStockValueView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: 'Branch stock value information'}
    )
    def get(self, request):
        """Get stock value for branch"""
        branch_id = request.query_params.get('branch_id')
        product_id = request.query_params.get('product_id')
        
        if not branch_id:
            return Response(
                {'detail': 'branch_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            value_info = services.get_branch_stock_value(
                int(branch_id),
                int(product_id) if product_id else None
            )
            return Response(value_info)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


# ========== Stock Transfer Views ==========

class StockTransferListView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('transfer_type', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('status', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('product_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('source_warehouse_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('source_branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('destination_warehouse_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('destination_branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: StockTransferSerializer(many=True)}
    )
    def get(self, request):
        """List all stock transfers"""
        transfer_type = request.query_params.get('transfer_type')
        status_filter = request.query_params.get('status')
        product_id = request.query_params.get('product_id')
        source_warehouse_id = request.query_params.get('source_warehouse_id')
        source_branch_id = request.query_params.get('source_branch_id')
        destination_warehouse_id = request.query_params.get('destination_warehouse_id')
        destination_branch_id = request.query_params.get('destination_branch_id')
        
        transfers = StockTransfer.objects.all()
        
        if transfer_type:
            transfers = transfers.filter(transfer_type=transfer_type)
        if status_filter:
            transfers = transfers.filter(status=status_filter)
        if product_id:
            transfers = transfers.filter(product_id=product_id)
        if source_warehouse_id:
            transfers = transfers.filter(source_warehouse_id=source_warehouse_id)
        if source_branch_id:
            transfers = transfers.filter(source_branch_id=source_branch_id)
        if destination_warehouse_id:
            transfers = transfers.filter(destination_warehouse_id=destination_warehouse_id)
        if destination_branch_id:
            transfers = transfers.filter(destination_branch_id=destination_branch_id)
        
        serializer = StockTransferSerializer(transfers, many=True)
        return Response(serializer.data)
    
    @swagger_auto_schema(
        request_body=CreateStockTransferSerializer,
        responses={201: StockTransferSerializer}
    )
    def post(self, request):
        """Create a new stock transfer (supports both single and multi-product)"""
        serializer = CreateStockTransferSerializer(data=request.data)
        if serializer.is_valid():
            try:
                items = serializer.validated_data.get('items', [])
                
                # Check if this is a multi-product transfer
                if items and len(items) > 0:
                    # Multi-product transfer
                    transfer = services.create_multi_product_stock_transfer(
                        transfer_type=serializer.validated_data['transfer_type'],
                        items_data=items,
                        source_warehouse_id=serializer.validated_data.get('source_warehouse_id'),
                        source_branch_id=serializer.validated_data.get('source_branch_id'),
                        destination_warehouse_id=serializer.validated_data.get('destination_warehouse_id'),
                        destination_branch_id=serializer.validated_data.get('destination_branch_id'),
                        reference_number=serializer.validated_data.get('reference_number') or None,
                        notes=serializer.validated_data.get('notes', ''),
                        created_by=request.user
                    )
                    
                    item_count = len(items)
                    log_activity(
                        activity_type=ActivityType.CUSTOM,
                        user=request.user,
                        description=f"Created multi-product stock transfer: {item_count} products ({transfer.get_transfer_type_display()})",
                        request=request,
                        related_object=transfer,
                    )
                else:
                    # Single product transfer (backward compatible)
                    transfer = services.create_stock_transfer(
                        transfer_type=serializer.validated_data['transfer_type'],
                        product_id=serializer.validated_data['product_id'],
                        quantity=serializer.validated_data['quantity'],
                        reorder_level=serializer.validated_data.get('reorder_level'),
                        source_warehouse_id=serializer.validated_data.get('source_warehouse_id'),
                        source_branch_id=serializer.validated_data.get('source_branch_id'),
                        destination_warehouse_id=serializer.validated_data.get('destination_warehouse_id'),
                        destination_branch_id=serializer.validated_data.get('destination_branch_id'),
                        supplier_id=serializer.validated_data.get('supplier_id'),
                        batch_number=serializer.validated_data.get('batch_number') or None,
                        reference_number=serializer.validated_data.get('reference_number') or None,
                        notes=serializer.validated_data.get('notes', ''),
                        selling_price=serializer.validated_data.get('selling_price'),
                        created_by=request.user
                    )
                    
                    log_activity(
                        activity_type=ActivityType.CUSTOM,
                        user=request.user,
                        description=f"Created stock transfer: {transfer.quantity} units of {transfer.product.name} ({transfer.get_transfer_type_display()})",
                        request=request,
                        related_object=transfer,
                    )
                
                return Response(StockTransferSerializer(transfer).data, status=status.HTTP_201_CREATED)
            except Exception as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BulkCreateStockTransferView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        request_body=BulkCreateStockTransferSerializer,
        responses={
            201: openapi.Response(
                description='Stock transfers created',
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
        """Bulk create multiple stock transfers"""
        serializer = BulkCreateStockTransferSerializer(data=request.data)
        if serializer.is_valid():
            transfers_data = serializer.validated_data['transfers']
            created_transfers, errors = services.bulk_create_stock_transfers(
                transfers_data=transfers_data,
                created_by=request.user
            )
            
            # Log activity
            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f"Bulk created {len(created_transfers)} stock transfers",
                request=request,
            )
            
            # Serialize created transfers
            created_serializer = StockTransferSerializer(created_transfers, many=True)
            
            return Response({
                'created': created_serializer.data,
                'errors': errors,
                'summary': {
                    'total': len(transfers_data),
                    'successful': len(created_transfers),
                    'failed': len(errors)
                }
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class StockTransferDetailView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        responses={200: StockTransferSerializer}
    )
    def get(self, request, pk):
        """Get stock transfer details"""
        try:
            transfer = StockTransfer.objects.get(pk=pk)
            serializer = StockTransferSerializer(transfer)
            return Response(serializer.data)
        except StockTransfer.DoesNotExist:
            return Response({'detail': 'Transfer not found.'}, status=status.HTTP_404_NOT_FOUND)


class CompleteStockTransferView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        responses={200: StockTransferSerializer}
    )
    def post(self, request, pk):
        """Complete a stock transfer"""
        try:
            transfer = services.complete_stock_transfer(
                transfer_id=pk,
                completed_by=request.user
            )
            
            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f"Completed stock transfer: {transfer.id}",
                request=request,
                related_object=transfer,
            )
            
            serializer = StockTransferSerializer(transfer)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except StockTransfer.DoesNotExist:
            return Response({'detail': 'Transfer not found.'}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CancelStockTransferView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        responses={200: StockTransferSerializer}
    )
    def post(self, request, pk):
        """Cancel a stock transfer"""
        try:
            transfer = services.cancel_stock_transfer(
                transfer_id=pk,
                cancelled_by=request.user
            )
            
            log_activity(
                activity_type=ActivityType.CUSTOM,
                user=request.user,
                description=f"Cancelled stock transfer: {transfer.id}",
                request=request,
                related_object=transfer,
            )
            
            serializer = StockTransferSerializer(transfer)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except StockTransfer.DoesNotExist:
            return Response({'detail': 'Transfer not found.'}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)