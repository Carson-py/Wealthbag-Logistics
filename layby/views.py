from rest_framework import status, generics
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta

from .models import Layby
from .serializers import (
    LaybySerializer, 
    CreateLaybySerializer, 
    LaybyPaymentSerializer, 
    LaybyPaymentInputSerializer
)
from . import services

class LaybyListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        laybys = Layby.objects.all()
        # Filter by branch if needed
        branch_id = request.query_params.get('branch_id')
        if branch_id:
            laybys = laybys.filter(branch_id=branch_id)
        
        # Filter by status
        status_param = request.query_params.get('status')
        if status_param:
            laybys = laybys.filter(status=status_param)

        # Filter by period (week or month)
        period = request.query_params.get('period')
        if period == 'week':
            start_date = timezone.now() - timedelta(days=7)
            laybys = laybys.filter(created_at__gte=start_date)
        elif period == 'month':
            start_date = timezone.now() - timedelta(days=30)
            laybys = laybys.filter(created_at__gte=start_date)
            
        serializer = LaybySerializer(laybys, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CreateLaybySerializer(data=request.data)
        if serializer.is_valid():
            try:
                layby = services.create_layby(
                    customer_name=serializer.validated_data['customer_name'],
                    customer_phone=serializer.validated_data['customer_phone'],
                    branch_id=serializer.validated_data['branch_id'],
                    due_date=serializer.validated_data['due_date'],
                    items_data=serializer.validated_data['items'],
                    deposit=serializer.validated_data['deposit'],
                    payment_method=serializer.validated_data['payment_method'],
                    cashier=request.user,
                    notes=serializer.validated_data.get('notes', "")
                )
                return Response(LaybySerializer(layby).data, status=status.HTTP_201_CREATED)
            except (ValidationError, ValueError) as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class LaybyDetailView(generics.RetrieveAPIView):
    queryset = Layby.objects.all()
    serializer_class = LaybySerializer
    permission_classes = [IsAuthenticated]

class LaybyPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        layby = get_object_or_404(Layby, pk=pk)
        serializer = LaybyPaymentInputSerializer(data=request.data)
        if serializer.is_valid():
            try:
                payment = services.add_layby_payment(
                    layby=layby,
                    amount=serializer.validated_data['amount'],
                    payment_method=serializer.validated_data['payment_method'],
                    cashier=request.user,
                    notes=serializer.validated_data.get('notes', "")
                )
                return Response(LaybyPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)
            except (ValidationError, ValueError) as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class LaybyFinalizeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        layby = get_object_or_404(Layby, pk=pk)
        try:
            sale = services.finalize_layby(layby, request.user)
            # Should normally return SaleSerializer, but it's in sales app.
            # We can just return a success message or the layby data.
            return Response({
                'detail': 'Layby finalized successfully.',
                'sale_id': sale.id,
                'sale_number': sale.sale_number
            }, status=status.HTTP_200_OK)
        except (ValidationError, ValueError) as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

class LaybyCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        layby = get_object_or_404(Layby, pk=pk)
        try:
            services.cancel_layby(layby, request.user)
            return Response({'detail': 'Layby cancelled and stock released.'}, status=status.HTTP_200_OK)
        except (ValidationError, ValueError) as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
