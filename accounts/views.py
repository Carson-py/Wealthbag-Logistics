from rest_framework.views import APIView
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework import status
from django.contrib.auth import get_user_model
from .serializers import (
    LoginSerializer, UserSerializer, CreateUserSerializer, EmployeeSerializer,
    ChangePasswordSerializer, ResetPasswordSerializer
)
from .permissions import IsAdminOrOwner
from . import services
from shared.audit import log_activity, ActivityType

User = get_user_model()


# Create your views here.
class LoginView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        request_body=LoginSerializer,  # This ensures parameters show in Swagger
        responses={200: LoginSerializer}
    )

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']
            refresh = RefreshToken.for_user(user)
            
            # Log successful login
            log_activity(
                activity_type=ActivityType.LOGIN,
                user=user,
                description=f"User {user.email} logged in successfully",
                request=request,
            )
            
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user': UserSerializer(user).data
            })
        else:
            # Log failed login attempt
            email = request.data.get('email', 'unknown')
            log_activity(
                activity_type=ActivityType.LOGIN_FAILED,
                description=f"Failed login attempt for email: {email}",
                request=request,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CreateUserView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrOwner]

    @swagger_auto_schema(
        request_body=CreateUserSerializer,
        responses={201: UserSerializer}
    )
    def post(self, request):
        serializer = CreateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data.copy()
        branch_id = data.pop('branch_id', None)
        warehouse_id = data.pop('warehouse_id', None)

        user, employee, password = services.create_user(
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            **data
        )

        # Log user creation
        log_activity(
            activity_type=ActivityType.USER_CREATED,
            user=request.user,
            description=f"Created new user: {user.email} with role {user.role}",
            request=request,
            related_object=user,
            metadata={
                'created_user_email': user.email,
                'created_user_role': user.role,
                'employee_code': employee.code,
            }
        )

        return Response({
            'user': UserSerializer(user).data,
            'employee': {
                'id': employee.id,
                'code': employee.code,
                'first_name': employee.first_name,
                'last_name': employee.last_name,
                'branch': employee.branch.id if employee.branch else None,
                'branch_name': employee.branch.name if employee.branch else None,
                'warehouse': employee.warehouse.id if employee.warehouse else None,
                'warehouse_name': employee.warehouse.name if employee.warehouse else None,
            },
            'password': password
        }, status=status.HTTP_201_CREATED)

class UserListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        role = request.query_params.get("role")  # optional

        users = services.get_all_users(role)
        serializer = EmployeeSerializer(users, many=True)
        return Response(serializer.data)


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: EmployeeSerializer}
    )
    def get(self, request):
        employee = request.user.profile.first()
        serializer = EmployeeSerializer(employee)
        return Response(serializer.data)


class BlockUnblockAccountView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrOwner]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'user_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='ID of the user')
            },
            required=['user_id']
        ),
        responses={200: UserSerializer}
    )
    def patch(self, request):
        user_id = request.data.get('user_id')

        if user_id is None:
            return Response({'detail': 'user_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return Response({'detail': 'user_id must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = services.block_unblock_account(user_id)
        except User.DoesNotExist:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        status_message = 'unblocked' if user.account_status == 'active' else 'blocked'
        activity_type = ActivityType.USER_UNBLOCKED if user.account_status == 'active' else ActivityType.USER_BLOCKED

        # Log account status change
        log_activity(
            activity_type=activity_type,
            user=request.user,
            description=f"Account {status_message} for user: {user.email}",
            request=request,
            related_object=user,
            metadata={
                'target_user_email': user.email,
                'new_status': user.account_status,
            }
        )

        return Response({
            'message': f'User account {status_message}.',
            'user': UserSerializer(user).data
        })


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=ChangePasswordSerializer,
        responses={200: 'Password changed successfully'}
    )
    def post(self, request):
        """Change the authenticated user's password."""
        serializer = ChangePasswordSerializer(data=request.data)
        if serializer.is_valid():
            try:
                user = services.change_password(
                    user=request.user,
                    old_password=serializer.validated_data['old_password'],
                    new_password=serializer.validated_data['new_password']
                )
                
                # Log password change (already logged in service, but adding here for completeness)
                log_activity(
                    activity_type=ActivityType.PASSWORD_CHANGED,
                    user=user,
                    description=f"User {user.email} changed their password",
                    request=request,
                )
                
                return Response({
                    'message': 'Password changed successfully.',
                    'user': UserSerializer(user).data
                })
            except ValueError as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @swagger_auto_schema(
        request_body=ResetPasswordSerializer,
        responses={200: 'Password reset successfully'}
    )
    def post(self, request):
        """Reset a user's password. User submits their email and receives a new password via email."""
        serializer = ResetPasswordSerializer(data=request.data)
        if serializer.is_valid():
            try:
                email = serializer.validated_data.get('email')
                
                user, new_password = services.reset_password(
                    email=email
                )
                
                # Log password reset (handle anonymous users for self-service reset)
                reset_by = request.user if request.user.is_authenticated else None
                reset_by_email = request.user.email if request.user.is_authenticated else 'self-service'
                
                log_activity(
                    activity_type=ActivityType.PASSWORD_RESET,
                    user=reset_by,
                    description=f"Password reset for user: {user.email} (self-service)",
                    request=request,
                    related_object=user,
                    metadata={
                        'target_user_email': user.email,
                        'reset_by': reset_by_email,
                        'reset_type': 'self-service',
                    }
                )
                
                return Response({
                    'message': 'Password reset successfully. A new password has been sent to your email address.',
                }, status=status.HTTP_200_OK)
            except User.DoesNotExist:
                # Don't reveal if user exists or not for security reasons
                return Response({
                    'message': 'If an account with that email exists, a password reset email has been sent.'
                }, status=status.HTTP_200_OK)
            except ValueError as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)