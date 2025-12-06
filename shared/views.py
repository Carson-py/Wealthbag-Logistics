from django.utils.dateparse import parse_datetime, parse_date
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .audit import get_audit_logs
from .serializers import AuditLogSerializer


class AuditLogListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        Return audit logs (admin only) with optional filters:
        - activity_type
        - user_id
        - start
        - end
        """
        activity_type = request.query_params.get('activity_type')
        user_id = request.query_params.get('user_id')
        start = request.query_params.get('start')
        end = request.query_params.get('end')

        filters = {}
        if activity_type:
            filters['activity_type'] = activity_type
        if user_id:
            filters['user_id'] = user_id
        if start:
            filters['timestamp__gte'] = parse_datetime(start) or parse_date(start)
        if end:
            filters['timestamp__lte'] = parse_datetime(end) or parse_date(end)

        logs = get_audit_logs(request.user, **filters)
        serializer = AuditLogSerializer(logs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

