from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.core.exceptions import ValidationError
from .serializers import BranchSerializer, WarehouseSerializer
from . import services
from .models import Branch, Warehouse


class ListCreateBranchView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: BranchSerializer(many=True)}
    )
    def get(self, request):
        """List all branches."""
        branches = Branch.objects.all()
        serializer = BranchSerializer(branches, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        request_body=BranchSerializer,
        responses={201: BranchSerializer}
    )
    def post(self, request):
        """Create a new branch."""
        serializer = BranchSerializer(data=request.data)
        if serializer.is_valid():
            try:
                warehouse = serializer.validated_data.get('warehouse')
                warehouse_id = None
                if warehouse:
                    warehouse_id = warehouse.id if hasattr(warehouse, 'id') else warehouse
                
                branch = services.create_branch(
                    name=serializer.validated_data['name'],
                    address=serializer.validated_data.get('address', ''),
                    warehouse_id=warehouse_id
                )
                return Response(BranchSerializer(branch).data, status=status.HTTP_201_CREATED)
            except Warehouse.DoesNotExist:
                return Response({'detail': 'Warehouse not found.'}, status=status.HTTP_404_NOT_FOUND)
            except Exception as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BranchDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: BranchSerializer}
    )
    def get(self, request, pk):
        """Retrieve a specific branch."""
        try:
            branch = Branch.objects.get(pk=pk)
            serializer = BranchSerializer(branch)
            return Response(serializer.data)
        except Branch.DoesNotExist:
            return Response({'detail': 'Branch not found.'}, status=status.HTTP_404_NOT_FOUND)

    @swagger_auto_schema(
        request_body=BranchSerializer,
        responses={200: BranchSerializer}
    )
    def put(self, request, pk):
        """Update a branch."""
        try:
            serializer = BranchSerializer(data=request.data)
            if serializer.is_valid():
                branch = Branch.objects.get(pk=pk)
                
                # Handle warehouse update
                if 'warehouse' in serializer.validated_data:
                    warehouse = serializer.validated_data['warehouse']
                    if warehouse is None:
                        branch.warehouse = None
                    else:
                        warehouse_id = warehouse.id if hasattr(warehouse, 'id') else warehouse
                        branch.warehouse = Warehouse.objects.get(pk=warehouse_id)
                
                # Update other fields
                if 'name' in serializer.validated_data:
                    branch.name = serializer.validated_data['name']
                if 'address' in serializer.validated_data:
                    branch.address = serializer.validated_data['address']
                
                branch.save()
                return Response(BranchSerializer(branch).data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Branch.DoesNotExist:
            return Response({'detail': 'Branch not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Warehouse.DoesNotExist:
            return Response({'detail': 'Warehouse not found.'}, status=status.HTTP_404_NOT_FOUND)

    @swagger_auto_schema(
        request_body=BranchSerializer,
        responses={200: BranchSerializer}
    )
    def patch(self, request, pk):
        """Partially update a branch."""
        try:
            serializer = BranchSerializer(data=request.data, partial=True)
            if serializer.is_valid():
                branch = Branch.objects.get(pk=pk)
                
                # Handle warehouse update
                if 'warehouse' in serializer.validated_data:
                    warehouse = serializer.validated_data['warehouse']
                    if warehouse is None:
                        branch.warehouse = None
                    else:
                        warehouse_id = warehouse.id if hasattr(warehouse, 'id') else warehouse
                        branch.warehouse = Warehouse.objects.get(pk=warehouse_id)
                
                # Update other fields
                if 'name' in serializer.validated_data:
                    branch.name = serializer.validated_data['name']
                if 'address' in serializer.validated_data:
                    branch.address = serializer.validated_data['address']
                
                branch.save()
                return Response(BranchSerializer(branch).data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Branch.DoesNotExist:
            return Response({'detail': 'Branch not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Warehouse.DoesNotExist:
            return Response({'detail': 'Warehouse not found.'}, status=status.HTTP_404_NOT_FOUND)
    
    @swagger_auto_schema(
        responses={204: 'No Content'}
    )
    def delete(self, request, pk):
        """Delete a branch."""
        try:
            services.delete_branch(pk)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Branch.DoesNotExist:
            return Response({'detail': 'Branch not found.'}, status=status.HTTP_404_NOT_FOUND)


class ListCreateWarehouseView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: WarehouseSerializer(many=True)}
    )
    def get(self, request):
        """List all warehouses."""
        warehouses = Warehouse.objects.all()
        serializer = WarehouseSerializer(warehouses, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        request_body=WarehouseSerializer,
        responses={201: WarehouseSerializer}
    )
    def post(self, request):
        """Create a new warehouse."""
        serializer = WarehouseSerializer(data=request.data)
        if serializer.is_valid():
            try:
                warehouse = services.create_warehouse(
                    name=serializer.validated_data['name'],
                    location=serializer.validated_data.get('location', ''),
                    is_main=serializer.validated_data.get('is_main', False)
                )
                return Response(WarehouseSerializer(warehouse).data, status=status.HTTP_201_CREATED)
            except ValidationError as e:
                # Extract message from ValidationError
                if hasattr(e, 'messages') and e.messages:
                    error_message = e.messages[0] if isinstance(e.messages, list) else str(e.messages)
                else:
                    error_message = str(e)
                return Response({'detail': error_message}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WarehouseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: WarehouseSerializer}
    )
    def get(self, request, pk):
        """Retrieve a specific warehouse."""
        try:
            warehouse = Warehouse.objects.select_related().get(pk=pk)
            serializer = WarehouseSerializer(warehouse)
            return Response(serializer.data)
        except Warehouse.DoesNotExist:
            return Response({'detail': 'Warehouse not found.'}, status=status.HTTP_404_NOT_FOUND)

    @swagger_auto_schema(
        request_body=WarehouseSerializer,
        responses={200: WarehouseSerializer}
    )
    def put(self, request, pk):
        """Update a warehouse."""
        try:
            serializer = WarehouseSerializer(data=request.data)
            if serializer.is_valid():
                try:
                    warehouse = services.edit_warehouse(
                        pk=pk,
                        name=serializer.validated_data.get('name'),
                        location=serializer.validated_data.get('location'),
                        is_main=serializer.validated_data.get('is_main')
                    )
                    return Response(WarehouseSerializer(warehouse).data)
                except Exception as e:
                    return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Warehouse.DoesNotExist:
            return Response({'detail': 'Warehouse not found.'}, status=status.HTTP_404_NOT_FOUND)

    @swagger_auto_schema(
        request_body=WarehouseSerializer,
        responses={200: WarehouseSerializer}
    )
    def patch(self, request, pk):
        """Partially update a warehouse."""
        try:
            serializer = WarehouseSerializer(data=request.data, partial=True)
            if serializer.is_valid():
                try:
                    warehouse = services.edit_warehouse(
                        pk=pk,
                        name=serializer.validated_data.get('name'),
                        location=serializer.validated_data.get('location'),
                        is_main=serializer.validated_data.get('is_main')
                    )
                    return Response(WarehouseSerializer(warehouse).data)
                except Exception as e:
                    return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Warehouse.DoesNotExist:
            return Response({'detail': 'Warehouse not found.'}, status=status.HTTP_404_NOT_FOUND)
    
    @swagger_auto_schema(
        responses={204: 'No Content'}
    )
    def delete(self, request, pk):
        """Delete a warehouse."""
        try:
            services.delete_warehouse(pk)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Warehouse.DoesNotExist:
            return Response({'detail': 'Warehouse not found.'}, status=status.HTTP_404_NOT_FOUND)
