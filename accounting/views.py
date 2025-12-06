from django.utils.dateparse import parse_date
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .models import Expense, ExpenseCategory, ProfitLossReport
from .serializers import (
    ExpenseCategorySerializer,
    ExpenseSerializer,
    ProfitLossReportSerializer,
    ProfitLossQuerySerializer,
    SalesReportQuerySerializer,
)
from . import services


class ExpenseCategoryListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: ExpenseCategorySerializer(many=True)})
    def get(self, request):
        categories = ExpenseCategory.objects.all()
        serializer = ExpenseCategorySerializer(categories, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(request_body=ExpenseCategorySerializer, responses={201: ExpenseCategorySerializer})
    def post(self, request):
        serializer = ExpenseCategorySerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ExpenseListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('warehouse_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('category_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('start_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date'),
            openapi.Parameter('end_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date'),
        ],
        responses={200: ExpenseSerializer(many=True)}
    )
    def get(self, request):
        queryset = Expense.objects.select_related('category', 'branch', 'warehouse', 'created_by')

        branch_id = request.query_params.get('branch_id')
        warehouse_id = request.query_params.get('warehouse_id')
        category_id = request.query_params.get('category_id')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        if branch_id:
            queryset = queryset.filter(branch_id=branch_id)
        if warehouse_id:
            queryset = queryset.filter(warehouse_id=warehouse_id)
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        if start_date:
            queryset = queryset.filter(incurred_on__gte=parse_date(start_date))
        if end_date:
            queryset = queryset.filter(incurred_on__lte=parse_date(end_date))

        serializer = ExpenseSerializer(queryset, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(request_body=ExpenseSerializer, responses={201: ExpenseSerializer})
    def post(self, request):
        serializer = ExpenseSerializer(data=request.data)
        if serializer.is_valid():
            expense = services.create_expense(data=serializer.validated_data, created_by=request.user)
            return Response(ExpenseSerializer(expense).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ExpenseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: ExpenseSerializer})
    def get(self, request, pk):
        try:
            expense = Expense.objects.get(pk=pk)
        except Expense.DoesNotExist:
            return Response({'detail': 'Expense not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ExpenseSerializer(expense).data)

    @swagger_auto_schema(request_body=ExpenseSerializer, responses={200: ExpenseSerializer})
    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    @swagger_auto_schema(request_body=ExpenseSerializer, responses={200: ExpenseSerializer})
    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        try:
            expense = Expense.objects.get(pk=pk)
        except Expense.DoesNotExist:
            return Response({'detail': 'Expense not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = ExpenseSerializer(expense, data=request.data, partial=partial)
        if serializer.is_valid():
            expense = services.update_expense(expense, data=serializer.validated_data)
            return Response(ExpenseSerializer(expense).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(responses={204: 'Deleted'})
    def delete(self, request, pk):
        try:
            expense = Expense.objects.get(pk=pk)
        except Expense.DoesNotExist:
            return Response({'detail': 'Expense not found.'}, status=status.HTTP_404_NOT_FOUND)
        expense.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfitLossReportView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('start_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date', required=True),
            openapi.Parameter('end_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date', required=True),
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('persist', openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
        ],
        responses={200: ProfitLossReportSerializer}
    )
    def get(self, request):
        serializer = ProfitLossQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        report = services.generate_profit_loss_report(
            start_date=data['start_date'],
            end_date=data['end_date'],
            branch_id=data.get('branch_id'),
            generated_by=request.user,
            persist=data.get('persist', False),
        )
        return Response(report, status=status.HTTP_200_OK)


class SalesReportView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('start_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date', required=True),
            openapi.Parameter('end_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, format='date', required=True),
            openapi.Parameter('branch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('group_by', openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              description='day, month, branch, product'),
        ],
        responses={200: 'Sales report summary'}
    )
    def get(self, request):
        serializer = SalesReportQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        report = services.get_sales_report(
            start_date=data['start_date'],
            end_date=data['end_date'],
            branch_id=data.get('branch_id'),
            group_by=data['group_by'],
        )
        return Response(report, status=status.HTTP_200_OK)
