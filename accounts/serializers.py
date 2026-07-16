from rest_framework import serializers
from django.contrib.auth import get_user_model, authenticate
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
import secrets
import string
from mailjet_rest import Client
from django.template.loader import render_to_string

User = get_user_model()

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data.get('email')
        password = data.get('password')

        if email and password:
            user = authenticate(email=email, password=password)
            if not user:
                raise ValidationError('Unable to log in with provided credentials.')
            if not user.is_active:
                raise ValidationError('User account is disabled.')
        else:
            raise ValidationError('Must include "email" and "password".')

        data['user'] = user
        return data

class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    branch = serializers.SerializerMethodField()
    warehouse = serializers.SerializerMethodField()
    branch_name = serializers.SerializerMethodField()
    branch_phone = serializers.SerializerMethodField()
    branch_address = serializers.SerializerMethodField()
    warehouse_name = serializers.SerializerMethodField()
    employee_name = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = [
            'id', 'email', 'role', 'role_display', 'is_active', 'date_joined', 
            'first_login', 'branch', 'warehouse', 'branch_name', 'branch_phone', 'branch_address', 'warehouse_name', 'employee_name'
        ]
        read_only_fields = ['date_joined']

    def get_employee_name(self, obj):
        """Get employee name from employee profile"""
        try:
            employee = obj.profile.first()
            if employee and employee.first_name and employee.last_name:
                return f'{employee.first_name} {employee.last_name}'
        except:
            pass
        return None

    def get_branch(self, obj):
        """Get branch ID from employee profile"""
        try:
            employee = obj.profile.first()
            if employee and employee.branch:
                return employee.branch.id
        except:
            pass
        return None
    
    def get_warehouse(self, obj):
        """Get warehouse ID from employee profile"""
        try:
            employee = obj.profile.first()
            if employee and employee.warehouse:
                return employee.warehouse.id
        except:
            pass
        return None
    
    def get_branch_name(self, obj):
        """Get branch name from employee profile"""
        try:
            employee = obj.profile.first()
            if employee and employee.branch:
                return employee.branch.name
        except:
            pass
        return None
    
    def get_branch_phone(self, obj):
        """Get branch phone from employee profile"""
        try:
            employee = obj.profile.first()
            if employee and employee.branch:
                return employee.branch.phone
        except:
            pass
        return None
    
    def get_branch_address(self, obj):
        """Get branch address from employee profile"""
        try:
            employee = obj.profile.first()
            if employee and employee.branch:
                return employee.branch.address
        except:
            pass
        return None
    
    def get_warehouse_name(self, obj):
        """Get warehouse name from employee profile"""
        try:
            employee = obj.profile.first()
            if employee and employee.warehouse:
                return employee.warehouse.name
        except:
            pass
        return None


class CreateUserSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = ['email', 'role', 'is_active', 'first_name', 'last_name', 'branch_id', 'warehouse_id']

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('User with this email already exists.')
        return value
    
    def validate(self, data):
        role = data.get('role')
        branch_id = data.get('branch_id')
        warehouse_id = data.get('warehouse_id')
        
        # Branch is required for branch_manager and cashier
        if role in ['branch_manager', 'cashier']:
            if not branch_id:
                raise serializers.ValidationError(f'branch_id is required for role: {role}')
            if warehouse_id:
                raise serializers.ValidationError(f'warehouse_id should not be provided for role: {role}')
        
        # Warehouse is required for warehouse_manager
        elif role == 'warehouse_manager':
            if not warehouse_id:
                raise serializers.ValidationError('warehouse_id is required for role: warehouse_manager')
            if branch_id:
                raise serializers.ValidationError('branch_id should not be provided for role: warehouse_manager')
        
        # Other roles (owner, admin, auditor) don't need branch or warehouse
        else:
            if branch_id or warehouse_id:
                raise serializers.ValidationError(f'branch_id and warehouse_id should not be provided for role: {role}')
        
        return data

class EmployeeSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)

    class Meta:
        from .models import Employee
        model = Employee
        fields = [
            'id', 'code', 'first_name', 'last_name', 'user', 
            'branch', 'branch_name', 'warehouse', 'warehouse_name'
        ]


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True, min_length=8)

    def validate_new_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError('Password must be at least 8 characters long.')
        return value


class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)