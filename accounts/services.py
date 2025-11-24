import secrets
import string
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from mailjet_rest import Client

from .models import Employee

User = get_user_model()

ALPHABET = string.ascii_uppercase + string.digits
EMPLOYEE_CODE_PREFIX = 'WBL'


def _generate_code(length: int = 8) -> str:
    return ''.join(secrets.choice(ALPHABET) for _ in range(length))


def _generate_unique_employee_code() -> str:
    """
    Generate sequential employee codes like WBL0001, WBL0002, ...
    Falls back gracefully if existing codes are malformed.
    """
    last_code = (
        Employee.objects.filter(code__startswith=EMPLOYEE_CODE_PREFIX)
        .order_by('-code')
        .values_list('code', flat=True)
        .first()
    )

    if last_code:
        numeric_part = last_code.replace(EMPLOYEE_CODE_PREFIX, '')
        try:
            next_number = int(numeric_part) + 1
        except ValueError:
            next_number = Employee.objects.count() + 1
    else:
        next_number = 1

    while True:
        code = f'{EMPLOYEE_CODE_PREFIX}{next_number:04d}'
        if not Employee.objects.filter(code=code).exists():
            return code
        next_number += 1


def create_user(email: str, role: str, first_name: str, last_name: str, 
                is_active: bool = True, branch_id: int = None, warehouse_id: int = None):
    """
    Create a new user together with the corresponding employee profile.
    Returns the created user instance, employee instance, and the generated password.
    
    Args:
        email: User email
        role: User role
        first_name: Employee first name
        last_name: Employee last name
        is_active: Whether user is active
        branch_id: Branch ID (required for branch_manager and cashier roles)
        warehouse_id: Warehouse ID (required for warehouse_manager role)
    """
    password = _generate_code(10)
    user = User.objects.create_user(
        email=email,
        password=password,
        role=role,
        is_active=True,
        first_login=True
    )
    
    # Validate branch/warehouse assignment based on role
    if role in ['branch_manager', 'cashier']:
        if not branch_id:
            raise ValueError(f'branch_id is required for role: {role}')
        warehouse_id = None  # Ensure warehouse is not set
    elif role == 'warehouse_manager':
        if not warehouse_id:
            raise ValueError('warehouse_id is required for role: warehouse_manager')
        branch_id = None  # Ensure branch is not set
    else:
        # For owner, admin, auditor - no branch or warehouse
        branch_id = None
        warehouse_id = None
    
    employee = Employee.objects.create(
        user=user,
        code=_generate_unique_employee_code(),
        first_name=first_name,
        last_name=last_name,
        branch_id=branch_id,
        warehouse_id=warehouse_id,
    )

    send_new_account_email(user, password, employee)

    return user, employee, password


def send_new_account_email(user: User, password: str, employee: Employee) -> None:
    """
    Send a welcome email to the newly created user containing their temporary password via Mailjet.
    """
    subject = 'Welcome to WealthBag Logistics'
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@wealthbaglogistics.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'WealthBag Logistics')
    logo_url = getattr(settings, 'COMPANY_LOGO_URL')
    support_email = getattr(settings, 'SUPPORT_EMAIL', 'support@wealthbaglogistics.com')

    context = {
        'user': user,
        'employee': employee,
        'temporary_password': password,
        'company_name': company_name,
        'company_logo_url': logo_url,
        'support_email': support_email,
    }

    html_content = render_to_string('accounts/emails/new_user_credentials.html', context)
    text_content = strip_tags(html_content)

    _send_mailjet_email(
        subject=subject,
        from_email=from_email,
        from_name=company_name,
        to_email=user.email,
        to_name=employee.get_full_name() or user.email,
        text_content=text_content,
        html_content=html_content
    )


def _send_mailjet_email(subject: str, from_email: str, from_name: str, to_email: str, to_name: str,
                        text_content: str, html_content: str) -> None:
    api_key = getattr(settings, 'MAILJET_API_KEY', '')
    api_secret = getattr(settings, 'MAILJET_API_SECRET', '')

    if not api_key or not api_secret:
        raise RuntimeError('Mailjet credentials are not configured. Set MAILJET_API_KEY and MAILJET_API_SECRET.')

    mailjet = Client(auth=(api_key, api_secret), version='v3.1')
    data = {
        'Messages': [
            {
                'From': {
                    'Email': from_email,
                    'Name': from_name
                },
                'To': [
                    {
                        'Email': to_email,
                        'Name': to_name
                    }
                ],
                'Subject': subject,
                'TextPart': text_content,
                'HTMLPart': html_content
            }
        ]
    }

    result = mailjet.send.create(data=data)
    if result.status_code not in (200, 201):
        raise RuntimeError(f'Mailjet email failed with status {result.status_code}: {result.json()}')

def get_all_users(role):
    queryset = Employee.objects.select_related('user').all()

    if role:
        queryset = queryset.filter(user__role=role)

    return queryset


def block_unblock_account(pk: int) -> User:
    """
    Toggle a user's `account_status` between 'active' and 'blocked'.
    Also sync the `is_active` flag accordingly.
    """
    user = User.objects.get(pk=pk)
    if user.account_status == 'blocked':
        user.account_status = 'active'
        user.is_active = True
        send_account_unblocked_email(user)
    else:
        user.account_status = 'blocked'
        user.is_active = False
        send_account_blocked_email(user)

    user.save(update_fields=['account_status', 'is_active'])
    return user


def send_account_blocked_email(user: User) -> None:
    """
    Notify the user that their account has been blocked.
    """
    subject = 'Your WealthBag Logistics account has been blocked'
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@wealthbaglogistics.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'WealthBag Logistics')
    logo_url = getattr(settings, 'COMPANY_LOGO_URL')
    support_email = getattr(settings, 'SUPPORT_EMAIL', 'support@wealthbaglogistics.com')

    employee = user.profile.first() if hasattr(user, 'profile') else None
    full_name = employee.get_full_name() if employee else user.email

    context = {
        'user': user,
        'full_name': full_name,
        'company_name': company_name,
        'company_logo_url': logo_url,
        'support_email': support_email,
    }

    html_content = render_to_string('accounts/emails/account_blocked.html', context)
    text_content = strip_tags(html_content)

    _send_mailjet_email(
        subject=subject,
        from_email=from_email,
        from_name=company_name,
        to_email=user.email,
        to_name=full_name,
        text_content=text_content,
        html_content=html_content
    )


def send_account_unblocked_email(user: User) -> None:
    """
    Notify the user that their account has been reinstated.
    """
    subject = 'Your WealthBag Logistics account has been reinstated'
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@wealthbaglogistics.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'WealthBag Logistics')
    logo_url = getattr(settings, 'COMPANY_LOGO_URL')
    support_email = getattr(settings, 'SUPPORT_EMAIL', 'support@wealthbaglogistics.com')

    employee = user.profile.first() if hasattr(user, 'profile') else None
    full_name = employee.get_full_name() if employee else user.email

    context = {
        'user': user,
        'full_name': full_name,
        'company_name': company_name,
        'company_logo_url': logo_url,
        'support_email': support_email,
    }

    html_content = render_to_string('accounts/emails/account_unblocked.html', context)
    text_content = strip_tags(html_content)

    _send_mailjet_email(
        subject=subject,
        from_email=from_email,
        from_name=company_name,
        to_email=user.email,
        to_name=full_name,
        text_content=text_content,
        html_content=html_content
    )


def change_password(user: User, old_password: str, new_password: str) -> User:
    """
    Change a user's password after verifying the old password.
    Returns the updated user instance.
    """
    if not user.check_password(old_password):
        raise ValueError('Current password is incorrect.')
    
    user.set_password(new_password)
    if user.first_login:
        user.first_login = False
    user.save(update_fields=['password', 'first_login'])
    
    send_password_changed_email(user)
    return user


def reset_password(user_id: int = None, email: str = None):
    """
    Reset a user's password by generating a new temporary password.
    Can be called with either user_id or email.
    Returns the user instance and the new password.
    """
    if user_id:
        user = User.objects.get(pk=user_id)
    elif email:
        user = User.objects.get(email=email)
    else:
        raise ValueError('Either user_id or email must be provided.')
    
    new_password = _generate_code(10)
    user.set_password(new_password)
    if user.first_login:
        user.first_login = False
    user.save(update_fields=['password', 'first_login'])
    
    send_password_reset_email(user, new_password)
    return user, new_password


def send_password_changed_email(user: User) -> None:
    """
    Notify the user that their password has been successfully changed.
    """
    subject = 'Your password has been changed'
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@wealthbaglogistics.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'WealthBag Logistics')
    logo_url = getattr(settings, 'COMPANY_LOGO_URL')
    support_email = getattr(settings, 'SUPPORT_EMAIL', 'support@wealthbaglogistics.com')

    employee = user.profile.first() if hasattr(user, 'profile') else None
    full_name = employee.get_full_name() if employee else user.email

    context = {
        'user': user,
        'full_name': full_name,
        'company_name': company_name,
        'company_logo_url': logo_url,
        'support_email': support_email,
    }

    html_content = render_to_string('accounts/emails/password_changed.html', context)
    text_content = strip_tags(html_content)

    _send_mailjet_email(
        subject=subject,
        from_email=from_email,
        from_name=company_name,
        to_email=user.email,
        to_name=full_name,
        text_content=text_content,
        html_content=html_content
    )


def send_password_reset_email(user: User, new_password: str) -> None:
    """
    Send the user their new temporary password after reset.
    """
    subject = 'Your password has been reset'
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@wealthbaglogistics.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'WealthBag Logistics')
    logo_url = getattr(settings, 'COMPANY_LOGO_URL')
    support_email = getattr(settings, 'SUPPORT_EMAIL', 'support@wealthbaglogistics.com')

    employee = user.profile.first() if hasattr(user, 'profile') else None
    full_name = employee.get_full_name() if employee else user.email

    context = {
        'user': user,
        'employee': employee,
        'new_password': new_password,
        'full_name': full_name,
        'company_name': company_name,
        'company_logo_url': logo_url,
        'support_email': support_email,
    }

    html_content = render_to_string('accounts/emails/password_reset.html', context)
    text_content = strip_tags(html_content)

    _send_mailjet_email(
        subject=subject,
        from_email=from_email,
        from_name=company_name,
        to_email=user.email,
        to_name=full_name,
        text_content=text_content,
        html_content=html_content
    )