from __future__ import annotations

import uuid
import io
import base64
import random
import string
from decimal import Decimal
from typing import List, Dict, Optional, Tuple
from datetime import date, timedelta, datetime
import calendar

from django.db import transaction
from django.db.models import Sum, Count, Q, Avg, F
from django.utils import timezone
from django.conf import settings
import pytz
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from mailjet_rest import Client

from organization.models import Branch, Warehouse
from products.models import Product
from stock.models import BranchStock
from stock import services as stock_services
from accounts.models import User, Employee
from analytics import services as analytics_services
from accounting import services as accounting_services

from django.db.models.functions import TruncDate, TruncMonth
from .models import (
    Sale,
    SaleItem,
    ProductReturn,
    DailySalesReport,
    Discount,
    ReturnAuthorizationCode,
    CashReceived,
    ExchangeRate,
)


def _make_json_serializable(obj):
    """
    Convert an object to JSON-serializable format.
    Recursively converts Decimal values to float for JSON storage.
    """
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {key: _make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [_make_json_serializable(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    else:
        return str(obj)


def _generate_sale_number() -> str:
    """Generate a unique sale number."""
    date_prefix = timezone.now().strftime('%Y%m%d')
    unique_part = uuid.uuid4().hex[:6].upper()
    sale_number = f'SALE-{date_prefix}-{unique_part}'
    while Sale.objects.filter(sale_number=sale_number).exists():
        unique_part = uuid.uuid4().hex[:6].upper()
        sale_number = f'SALE-{date_prefix}-{unique_part}'
    return sale_number


def _generate_random_code(length: int = 6) -> str:
    """Generate a numeric authorization code."""
    return ''.join(random.choices(string.digits, k=length))


def _get_company_timezone():
    tz_name = getattr(settings, 'COMPANY_TIME_ZONE', getattr(settings, 'TIME_ZONE', 'UTC'))
    try:
        return pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        return timezone.get_current_timezone()


def _get_localized_datetime(dt: datetime) -> datetime:
    return dt.astimezone(_get_company_timezone())


def generate_return_authorization_code(
    *,
    branch_id: int,
    expires_in_minutes: int = 1440,
    created_by: Optional[User] = None,
) -> ReturnAuthorizationCode:
    """Create or refresh the authorization code for a branch."""
    from organization.models import Branch

    branch = Branch.objects.get(pk=branch_id)
    expires_at = timezone.now() + timedelta(minutes=expires_in_minutes)
    code = _generate_random_code()

    with transaction.atomic():
        ReturnAuthorizationCode.objects.filter(branch=branch, is_active=True).update(is_active=False)
        auth_code = ReturnAuthorizationCode.objects.create(
            branch=branch,
            code=code,
            expires_at=expires_at,
            created_by=created_by,
        )
    return auth_code


def validate_return_authorization_code(*, branch_id: int, code: str) -> bool:
    """Validate that the provided code is active and not expired."""
    now = timezone.now()
    return ReturnAuthorizationCode.objects.filter(
        branch_id=branch_id,
        code=code,
        is_active=True,
        expires_at__gte=now,
    ).exists()


def get_active_return_authorization_codes(branch_id: Optional[int] = None):
    """Retrieve active (non-expired) authorization codes."""
    now = timezone.now()
    queryset = ReturnAuthorizationCode.objects.filter(is_active=True, expires_at__gte=now)
    if branch_id:
        queryset = queryset.filter(branch_id=branch_id)
    return queryset.order_by('-created_at')


def notify_branch_managers_of_return_code(auth_code: ReturnAuthorizationCode) -> Dict[str, object]:
    """Send the active authorization code to all branch managers assigned to the branch."""
    branch = auth_code.branch
    managers = User.objects.filter(
        role='branch_manager',
        is_active=True,
        account_status='active',
        profile__branch=branch,
    ).distinct()

    if not managers.exists():
        return {'success': False, 'message': f'No branch manager assigned to {branch.name}'}

    api_key = getattr(settings, 'MAILJET_API_KEY', '')
    api_secret = getattr(settings, 'MAILJET_API_SECRET', '')
    if not api_key or not api_secret:
        return {'success': False, 'message': 'Mailjet credentials are not configured'}

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@decormasters.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    expires_at_local = _get_localized_datetime(auth_code.expires_at)
    expires_str = expires_at_local.strftime('%B %d, %Y %H:%M %Z')

    subject = f'Return Authorization Code - {branch.name}'
    html_content = f"""
    <html>
        <body style="font-family: Arial, sans-serif;">
            <p>Hello,</p>
            <p>The current return authorization code for <strong>{branch.name}</strong> is:</p>
            <p style="font-size: 24px; font-weight: bold; letter-spacing: 4px;">{auth_code.code}</p>
            <p>This code expires on <strong>{expires_str}</strong>.</p>
            <p>Please use it to authorize customer product returns within your branch.</p>
            <p>Regards,<br>{company_name}</p>
        </body>
    </html>
    """
    text_content = (
        f"Hello,\n\n"
        f"The current return authorization code for {branch.name} is: {auth_code.code}\n"
        f"This code expires on {expires_str}.\n\n"
        f"Regards,\n{company_name}"
    )

    mailjet = Client(auth=(api_key, api_secret), version='v3.1')
    sent = 0
    for manager in managers:
        data = {
            'Messages': [
                {
                    'From': {'Email': from_email, 'Name': company_name},
                    'To': [{'Email': manager.email, 'Name': manager.email}],
                    'Subject': subject,
                    'TextPart': text_content,
                    'HTMLPart': html_content,
                }
            ]
        }
        result = mailjet.send.create(data=data)
        if result.status_code == 200:
            sent += 1

    return {'success': sent > 0, 'sent_count': sent, 'branch': branch.name}


def _format_quantity(value) -> str:
    if value is None:
        return '0'
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _format_currency(value) -> str:
    if value is None:
        return '$0.00'
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _format_fast_items_html(items):
    if not items:
        return '<p>No fast-moving products found.</p>'
    rows = ''.join(
        f"<li><strong>{item.get('product_name', 'Unknown')} ({item.get('product_sku', 'N/A')})</strong> "
        f"&mdash; { _format_quantity(item.get('quantity_sold')) } units sold, "
        f"revenue {_format_currency(item.get('revenue'))}</li>"
        for item in items
    )
    return f'<ul>{rows}</ul>'


def _format_fast_items_text(items):
    if not items:
        return 'No fast-moving products found.'
    lines = [
        f"- {item.get('product_name', 'Unknown')} ({item.get('product_sku', 'N/A')}): "
        f"{_format_quantity(item.get('quantity_sold'))} units, revenue {_format_currency(item.get('revenue'))}"
        for item in items
    ]
    return '\n'.join(lines)


def _format_stock_health_items_html(items, empty_message):
    if not items:
        return f'<p>{empty_message}</p>'
    rows = []
    for item in items:
        stock = (
            item.get('total_stock')
            or item.get('branch_stock')
            or item.get('warehouse_stock')
            or item.get('current_quantity')
            or Decimal('0')
        )
        sold = (
            item.get('sold_in_period')
            or item.get('slow_period_sales')
            or item.get('dead_period_sales')
            or item.get('quantity_sold')
            or Decimal('0')
        )
        days_since = item.get('days_since_last_sale')
        last_sale_text = (
            f"{int(days_since)} days ago" if days_since is not None else 'No recorded sale'
        )
        rows.append(
            f"<li><strong>{item.get('product_name', 'Unknown')} ({item.get('product_sku', 'N/A')})</strong> "
            f"&mdash; Stock {_format_quantity(stock)}, sold {_format_quantity(sold)} in period, "
            f"last sale {last_sale_text}</li>"
        )
    return f'<ul>{rows}</ul>'


def _format_stock_health_items_text(items, empty_message):
    if not items:
        return empty_message
    lines = []
    for item in items:
        stock = (
            item.get('total_stock')
            or item.get('branch_stock')
            or item.get('warehouse_stock')
            or item.get('current_quantity')
            or Decimal('0')
        )
        sold = (
            item.get('sold_in_period')
            or item.get('slow_period_sales')
            or item.get('dead_period_sales')
            or item.get('quantity_sold')
            or Decimal('0')
        )
        days_since = item.get('days_since_last_sale')
        last_sale_text = (
            f"{int(days_since)} days ago" if days_since is not None else 'No recorded sale'
        )
        lines.append(
            f"- {item.get('product_name', 'Unknown')} ({item.get('product_sku', 'N/A')}): "
            f"Stock {_format_quantity(stock)}, sold {_format_quantity(sold)}, last sale {last_sale_text}"
        )
    return '\n'.join(lines)


def _send_mailjet_email(mailjet, from_email, company_name, to_email, subject, html_content, text_content, pdf_base64=None, pdf_filename=None):
    """Send email via Mailjet with optional PDF attachment."""
    message = {
        'From': {'Email': from_email, 'Name': company_name},
        'To': [{'Email': to_email}],
        'Subject': subject,
        'TextPart': text_content,
        'HTMLPart': html_content,
    }
    
    # Add PDF attachment if provided
    if pdf_base64 and pdf_filename:
        message['Attachments'] = [
            {
                'ContentType': 'application/pdf',
                'Filename': pdf_filename,
                'Base64Content': pdf_base64
            }
        ]
    
    data = {'Messages': [message]}
    
    try:
        result = mailjet.send.create(data=data)
        return result.status_code == 200, None
    except Exception as exc:
        return False, str(exc)


def generate_stock_health_report_pdf(
    fast_items: List[Dict],
    slow_items: List[Dict],
    dead_items: List[Dict],
    *,
    branch_name: Optional[str] = None,
    fast_days: int = 30,
    slow_period: str = '30d',
    dead_period: str = '90d',
) -> bytes:
    """Generate a PDF report from stock health data. Returns the PDF as bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#0f766e'),
        spaceAfter=30,
        alignment=1,
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0f172a'),
        spaceAfter=12,
        spaceBefore=12,
    )
    
    normal_style = styles['Normal']
    
    # Title
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    report_title = f'{company_name} - Stock Health Report'
    if branch_name:
        report_title += f' - {branch_name}'
    title = Paragraph(report_title, title_style)
    story.append(title)
    
    # Report timestamp
    report_time = _get_localized_datetime(timezone.now())
    timestamp_str = report_time.strftime('%B %d, %Y %I:%M %p %Z')
    date_para = Paragraph(f'<b>Generated:</b> {timestamp_str}', normal_style)
    story.append(date_para)
    story.append(Spacer(1, 0.2*inch))
    
    # Fast-moving products
    if fast_items:
        story.append(Paragraph(f'Fast-Moving Products (Last {fast_days} Days)', heading_style))
        fast_data = [['Product Name', 'SKU', 'Quantity Sold', 'Revenue']]
        for item in fast_items:
            fast_data.append([
                item.get('product_name', 'N/A'),
                item.get('product_sku', 'N/A'),
                f"{item.get('quantity_sold', 0):,.2f}",
                f"${item.get('revenue', 0):,.2f}",
            ])
        fast_table = Table(fast_data, colWidths=[2.5*inch, 1.2*inch, 1.2*inch, 1.1*inch])
        fast_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0fdf4')]),
        ]))
        story.append(fast_table)
        story.append(Spacer(1, 0.3*inch))
    
    # Slow-moving products
    if slow_items:
        story.append(Paragraph(f'Slow-Moving Products ({slow_period})', heading_style))
        slow_data = [['Product Name', 'SKU', 'Stock Qty', 'Sold in Period', 'Days Since Last Sale']]
        for item in slow_items:
            slow_data.append([
                item.get('product_name') or item.get('name', 'N/A'),
                item.get('product_sku') or item.get('sku', 'N/A'),
                f"{item.get('warehouse_stock', 0) + item.get('branch_stock', 0):,.2f}",
                f"{item.get('sold_in_period', 0):,.2f}",
                str(item.get('days_since_last_sale', 'N/A')),
            ])
        slow_table = Table(slow_data, colWidths=[2*inch, 1*inch, 1*inch, 1.2*inch, 1.2*inch])
        slow_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f59e0b')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fffbeb')]),
        ]))
        story.append(slow_table)
        story.append(Spacer(1, 0.3*inch))
    
    # Dead stock
    if dead_items:
        story.append(Paragraph(f'Dead Stock ({dead_period})', heading_style))
        dead_data = [['Product Name', 'SKU', 'Stock Qty', 'Sold in Period', 'Days Since Last Sale']]
        for item in dead_items:
            dead_data.append([
                item.get('product_name') or item.get('name', 'N/A'),
                item.get('product_sku') or item.get('sku', 'N/A'),
                f"{item.get('warehouse_stock', 0) + item.get('branch_stock', 0):,.2f}",
                f"{item.get('sold_in_period', 0):,.2f}",
                str(item.get('days_since_last_sale', 'N/A')),
            ])
        dead_table = Table(dead_data, colWidths=[2*inch, 1*inch, 1*inch, 1.2*inch, 1.2*inch])
        dead_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fef2f2')]),
        ]))
        story.append(dead_table)
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def send_stock_health_report_email(
    *,
    fast_days: int = 30,
    slow_period: str = '30d',
    dead_period: str = '90d',
    item_limit: int = 10,
) -> Dict[str, object]:
    """Send stock health report to admins (overall) and branch managers (branch scoped)."""
    fast_days = max(1, fast_days)
    item_limit = max(1, item_limit)

    api_key = getattr(settings, 'MAILJET_API_KEY', '')
    api_secret = getattr(settings, 'MAILJET_API_SECRET', '')
    if not api_key or not api_secret:
        return {'success': False, 'message': 'Mailjet credentials are not configured.'}

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@decormasters.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    mailjet = Client(auth=(api_key, api_secret), version='v3.1')

    report_time = _get_localized_datetime(timezone.now())
    timestamp_str = report_time.strftime('%B %d, %Y %I:%M %p %Z')

    # Prepare organization-wide data for admins
    fast_overall = analytics_services.get_fast_moving_products(days=fast_days, limit=item_limit)
    slow_overall = analytics_services.get_slow_stock_analysis(slow_period)
    dead_overall = analytics_services.get_dead_stock_analysis(dead_period)

    fast_list = fast_overall['items'][:item_limit]
    slow_list = slow_overall['items'][:item_limit]
    dead_list = dead_overall['items'][:item_limit]
    
    # Determine if PDF should be attached (when total items > 20 or any category > 10)
    total_items = len(fast_list) + len(slow_list) + len(dead_list)
    should_attach_pdf = total_items > 20 or len(fast_list) > 10 or len(slow_list) > 10 or len(dead_list) > 10
    
    # Generate PDF for admins if needed
    admin_pdf_bytes = None
    admin_pdf_base64 = None
    if should_attach_pdf:
        admin_pdf_bytes = generate_stock_health_report_pdf(
            fast_list, slow_list, dead_list,
            fast_days=fast_days,
            slow_period=slow_period,
            dead_period=dead_period,
        )
        admin_pdf_base64 = base64.b64encode(admin_pdf_bytes).decode('utf-8')

    admin_subject = f'Stock Health Report - {timestamp_str}'
    admin_html = f"""
    <html>
        <body style="font-family: Arial, sans-serif;">
            <p>Hello,</p>
            <p>Here is the latest stock health summary for the organization as of <strong>{timestamp_str}</strong>.</p>
            {f'<p><strong>Note:</strong> Due to the volume of data, a detailed PDF report is attached.</p>' if should_attach_pdf else ''}
            <h3>Fast-moving products (last {fast_days} days)</h3>
            {_format_fast_items_html(fast_list)}
            <h3>Slow-moving products ({slow_period})</h3>
            {_format_stock_health_items_html(slow_list, 'No slow-moving products detected.')}
            <h3>Dead stock ({dead_period})</h3>
            {_format_stock_health_items_html(dead_list, 'No dead stock detected.')}
            <p>Regards,<br>{company_name}</p>
        </body>
    </html>
    """
    admin_text = (
        f"Hello,"
        f"Here is the latest stock health summary as of {timestamp_str}."
        f"{'Note: A detailed PDF report is attached due to the volume of data.' if should_attach_pdf else ''}"
        f"Fast-moving products (last {fast_days} days): {_format_fast_items_text(fast_list)}"
        f"Slow-moving products ({slow_period}): {_format_stock_health_items_text(slow_list, 'No slow-moving products detected.')}"
        f"Dead stock ({dead_period}): {_format_stock_health_items_text(dead_list, 'No dead stock detected.')}"
        f"Regards, {company_name}"
    )

    admin_roles = ['owner', 'admin']
    admins = User.objects.filter(
        role__in=admin_roles,
        is_active=True,
        account_status='active',
    )

    results = []
    admin_sent = 0
    for admin in admins:
        success, error = _send_mailjet_email(
            mailjet,
            from_email,
            company_name,
            admin.email,
            admin_subject,
            admin_html,
            admin_text,
            pdf_base64=admin_pdf_base64,
            pdf_filename=f'stock_health_report_{timestamp_str.replace(" ", "_").replace(":", "")}.pdf',
        )
        if success:
            admin_sent += 1
        results.append({
            'email': admin.email,
            'role': admin.role,
            'success': success,
            'error': error,
        })

    # Branch manager data (per branch)
    branch_managers = User.objects.filter(
        role='branch_manager',
        is_active=True,
        account_status='active',
    ).prefetch_related('profile__branch')

    branch_sent = 0
    for manager in branch_managers:
        employee = manager.profile.first() if hasattr(manager, 'profile') else None
        branch = getattr(employee, 'branch', None)
        if not branch:
            continue

        branch_fast = analytics_services.get_fast_moving_products(
            days=fast_days,
            limit=item_limit,
            branch_id=branch.id,
        )['items'][:item_limit]
        branch_eval = analytics_services.get_branch_stock_evaluation(branch)
        branch_slow = branch_eval.get('slow_moving_stock', {}).get('items', [])[:item_limit]
        branch_dead = branch_eval.get('dead_stock', {}).get('items', [])[:item_limit]
        
        # Determine if PDF should be attached for branch manager
        branch_total_items = len(branch_fast) + len(branch_slow) + len(branch_dead)
        branch_should_attach_pdf = branch_total_items > 20 or len(branch_fast) > 10 or len(branch_slow) > 10 or len(branch_dead) > 10
        
        # Generate PDF for branch manager if needed
        branch_pdf_bytes = None
        branch_pdf_base64 = None
        if branch_should_attach_pdf:
            branch_pdf_bytes = generate_stock_health_report_pdf(
                branch_fast, branch_slow, branch_dead,
                branch_name=branch.name,
                fast_days=fast_days,
                slow_period=slow_period,
                dead_period=dead_period,
            )
            branch_pdf_base64 = base64.b64encode(branch_pdf_bytes).decode('utf-8')

        branch_subject = f'{branch.name} Stock Health - {timestamp_str}'
        branch_html = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <p>Hello {manager.email},</p>
                <p>Here is the current stock health summary for <strong>{branch.name}</strong> as of <strong>{timestamp_str}</strong>.</p>
                {f'<p><strong>Note:</strong> Due to the volume of data, a detailed PDF report is attached.</p>' if branch_should_attach_pdf else ''}
                <h3>Fast-moving products (last {fast_days} days)</h3>
                {_format_fast_items_html(branch_fast)}
                <h3>Slow-moving products ({slow_period})</h3>
                {_format_stock_health_items_html(branch_slow, 'No slow-moving products detected in your branch.')}
                <h3>Dead stock ({dead_period})</h3>
                {_format_stock_health_items_html(branch_dead, 'No dead stock detected in your branch.')}
                <p>Regards,<br>{company_name}</p>
            </body>
        </html>
        """
        branch_text = (
            f"Hello {manager.email},"
            f"Stock health summary for {branch.name} as of {timestamp_str}"
            f"{'Note: A detailed PDF report is attached due to the volume of data.' if branch_should_attach_pdf else ''}"
            f"Fast-moving products (last {fast_days} days): {_format_fast_items_text(branch_fast)}"
            f"Slow-moving products ({slow_period}):"
            f"{_format_stock_health_items_text(branch_slow, 'No slow-moving products detected in your branch.')}"
            f"Dead stock ({dead_period}):"
            f"{_format_stock_health_items_text(branch_dead, 'No dead stock detected in your branch.')}"
            f"Regards, {company_name}"
        )

        success, error = _send_mailjet_email(
            mailjet,
            from_email,
            company_name,
            manager.email,
            branch_subject,
            branch_html,
            branch_text,
            pdf_base64=branch_pdf_base64,
            pdf_filename=f'{branch.name.replace(" ", "_")}_stock_health_{timestamp_str.replace(" ", "_").replace(":", "")}.pdf',
        )
        if success:
            branch_sent += 1
        results.append({
            'email': manager.email,
            'role': manager.role,
            'branch': branch.name,
            'success': success,
            'error': error,
        })

    return {
        'success': (admin_sent + branch_sent) > 0,
        'admin_sent': admin_sent,
        'branch_sent': branch_sent,
        'results': results,
    }


def _format_low_stock_items_html(items, location_type='Warehouse'):
    """Format low stock items as HTML table."""
    if not items:
        return '<p>No low stock items found.</p>'
    
    rows = ''.join(
        f"<tr style='background-color: #fff3cd;'>"
        f"<td style='padding: 8px; border: 1px solid #ddd;'><strong>{item.get('product_name', 'Unknown')}</strong></td>"
        f"<td style='padding: 8px; border: 1px solid #ddd;'>{item.get('product_sku', 'N/A')}</td>"
        f"<td style='padding: 8px; border: 1px solid #ddd; text-align: right;'><strong style='color: #dc3545;'>{_format_quantity(item.get('quantity'))}</strong></td>"
        f"<td style='padding: 8px; border: 1px solid #ddd; text-align: right;'>{_format_quantity(item.get('reorder_level'))}</td>"
        f"</tr>"
        for item in items
    )
    
    return f"""
    <table style='width: 100%; border-collapse: collapse; margin: 20px 0;'>
        <thead>
            <tr style='background-color: #dc3545; color: white;'>
                <th style='padding: 10px; border: 1px solid #ddd; text-align: left;'>Product Name</th>
                <th style='padding: 10px; border: 1px solid #ddd; text-align: left;'>SKU</th>
                <th style='padding: 10px; border: 1px solid #ddd; text-align: right;'>Current Quantity</th>
                <th style='padding: 10px; border: 1px solid #ddd; text-align: right;'>Reorder Level</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    """


def send_low_stock_report_email() -> Dict[str, object]:
    """Send low stock notifications to warehouse managers, branch managers, and admins (for main warehouse)."""
    api_key = getattr(settings, 'MAILJET_API_KEY', '')
    api_secret = getattr(settings, 'MAILJET_API_SECRET', '')
    if not api_key or not api_secret:
        return {'success': False, 'message': 'Mailjet credentials are not configured.'}

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@decormasters.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    mailjet = Client(auth=(api_key, api_secret), version='v3.1')

    report_time = _get_localized_datetime(timezone.now())
    timestamp_str = report_time.strftime('%B %d, %Y %I:%M %p %Z')

    # Get low stock data
    warehouse_low_stock = stock_services.get_low_stock_products()
    branch_low_stock = stock_services.get_low_branch_stock_products()

    # Group low stock by warehouse/branch
    warehouse_stock_map = {}
    for item in warehouse_low_stock:
        warehouse_id = item['warehouse_id']
        if warehouse_id not in warehouse_stock_map:
            warehouse_stock_map[warehouse_id] = {
                'warehouse_id': warehouse_id,
                'warehouse_name': item['warehouse_name'],
                'items': []
            }
        warehouse_stock_map[warehouse_id]['items'].append(item)

    branch_stock_map = {}
    for item in branch_low_stock:
        branch_id = item['branch_id']
        if branch_id not in branch_stock_map:
            branch_stock_map[branch_id] = {
                'branch_id': branch_id,
                'branch_name': item['branch_name'],
                'items': []
            }
        branch_stock_map[branch_id]['items'].append(item)

    results = []
    warehouse_sent = 0
    branch_sent = 0
    admin_sent = 0

    # Send emails to warehouse managers
    warehouse_managers = User.objects.filter(
        role='warehouse_manager',
        is_active=True,
        account_status='active'
    ).prefetch_related('profile')

    for manager in warehouse_managers:
        employee = manager.profile.first() if hasattr(manager, 'profile') else None
        if not employee or not employee.warehouse:
            continue

        warehouse = employee.warehouse
        warehouse_data = warehouse_stock_map.get(warehouse.id)
        
        if not warehouse_data or not warehouse_data['items']:
            continue  # No low stock for this warehouse

        items = warehouse_data['items']
        employee_name = employee.get_full_name() if employee else manager.email

        subject = f'Low Stock Alert - {warehouse.name}'
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 800px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #dc3545;">⚠️ Low Stock Alert</h2>
                    <p>Hello {employee_name},</p>
                    <p>The following products in <strong>{warehouse.name}</strong> are running low on stock:</p>
                    {_format_low_stock_items_html(items, 'Warehouse')}
                    <p style="color: #856404; background-color: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; margin: 20px 0;">
                        <strong>Action Required:</strong> Please review these items and consider restocking to avoid stockouts.
                    </p>
                    <p>Report generated on: {timestamp_str}</p>
                    <p>Regards,<br>{company_name}</p>
                </div>
            </body>
        </html>
        """
        text_content = (
            f"Low Stock Alert - {warehouse.name}\n\n"
            f"Hello {employee_name},\n\n"
            f"The following products in {warehouse.name} are running low on stock:\n\n"
        )
        for item in items:
            text_content += (
                f"- {item.get('product_name')} ({item.get('product_sku')}): "
                f"Current: {_format_quantity(item.get('quantity'))}, "
                f"Reorder Level: {_format_quantity(item.get('reorder_level'))}\n"
            )
        text_content += f"\nReport generated on: {timestamp_str}\n\nRegards,\n{company_name}"

        success, error = _send_mailjet_email(
            mailjet, from_email, company_name, manager.email,
            subject, html_content, text_content
        )
        if success:
            warehouse_sent += 1
        results.append({
            'email': manager.email,
            'role': 'warehouse_manager',
            'warehouse': warehouse.name,
            'success': success,
            'error': error,
        })

    # Send emails to branch managers
    branch_managers = User.objects.filter(
        role='branch_manager',
        is_active=True,
        account_status='active'
    ).prefetch_related('profile')

    for manager in branch_managers:
        employee = manager.profile.first() if hasattr(manager, 'profile') else None
        if not employee or not employee.branch:
            continue

        branch = employee.branch
        branch_data = branch_stock_map.get(branch.id)
        
        if not branch_data or not branch_data['items']:
            continue  # No low stock for this branch

        items = branch_data['items']
        employee_name = employee.get_full_name() if employee else manager.email

        subject = f'Low Stock Alert - {branch.name}'
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 800px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #dc3545;">⚠️ Low Stock Alert</h2>
                    <p>Hello {employee_name},</p>
                    <p>The following products in <strong>{branch.name}</strong> are running low on stock:</p>
                    {_format_low_stock_items_html(items, 'Branch')}
                    <p style="color: #856404; background-color: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; margin: 20px 0;">
                        <strong>Action Required:</strong> Please review these items and consider requesting stock from the warehouse.
                    </p>
                    <p>Report generated on: {timestamp_str}</p>
                    <p>Regards,<br>{company_name}</p>
                </div>
            </body>
        </html>
        """
        text_content = (
            f"Low Stock Alert - {branch.name}\n\n"
            f"Hello {employee_name},\n\n"
            f"The following products in {branch.name} are running low on stock:\n\n"
        )
        for item in items:
            text_content += (
                f"- {item.get('product_name')} ({item.get('product_sku')}): "
                f"Current: {_format_quantity(item.get('quantity'))}, "
                f"Reorder Level: {_format_quantity(item.get('reorder_level'))}\n"
            )
        text_content += f"\nReport generated on: {timestamp_str}\n\nRegards,\n{company_name}"

        success, error = _send_mailjet_email(
            mailjet, from_email, company_name, manager.email,
            subject, html_content, text_content
        )
        if success:
            branch_sent += 1
        results.append({
            'email': manager.email,
            'role': 'branch_manager',
            'branch': branch.name,
            'success': success,
            'error': error,
        })

    # Send email to admins for main warehouse low stock
    main_warehouse = Warehouse.objects.filter(is_main=True).first()
    if main_warehouse:
        main_warehouse_data = warehouse_stock_map.get(main_warehouse.id)
        if main_warehouse_data and main_warehouse_data['items']:
            items = main_warehouse_data['items']
            
            # Get all admins and owners
            admins = User.objects.filter(
                role__in=['admin', 'owner'],
                is_active=True,
                account_status='active'
            ).prefetch_related('profile')

            for admin in admins:
                employee = admin.profile.first() if hasattr(admin, 'profile') else None
                admin_name = employee.get_full_name() if employee and employee.get_full_name() else admin.email

                subject = f'Low Stock Alert - Main Warehouse ({main_warehouse.name})'
                html_content = f"""
                <html>
                    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                        <div style="max-width: 800px; margin: 0 auto; padding: 20px;">
                            <h2 style="color: #dc3545;">⚠️ Low Stock Alert - Main Warehouse</h2>
                            <p>Hello {admin_name},</p>
                            <p>The following products in the <strong>Main Warehouse ({main_warehouse.name})</strong> are running low on stock:</p>
                            {_format_low_stock_items_html(items, 'Warehouse')}
                            <p style="color: #856404; background-color: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; margin: 20px 0;">
                                <strong>Action Required:</strong> Please review these items and coordinate restocking for the main warehouse.
                            </p>
                            <p>Report generated on: {timestamp_str}</p>
                            <p>Regards,<br>{company_name}</p>
                        </div>
                    </body>
                </html>
                """
                text_content = (
                    f"Low Stock Alert - Main Warehouse ({main_warehouse.name})\n\n"
                    f"Hello {admin_name},\n\n"
                    f"The following products in the Main Warehouse ({main_warehouse.name}) are running low on stock:\n\n"
                )
                for item in items:
                    text_content += (
                        f"- {item.get('product_name')} ({item.get('product_sku')}): "
                        f"Current: {_format_quantity(item.get('quantity'))}, "
                        f"Reorder Level: {_format_quantity(item.get('reorder_level'))}\n"
                    )
                text_content += f"\nReport generated on: {timestamp_str}\n\nRegards,\n{company_name}"

                success, error = _send_mailjet_email(
                    mailjet, from_email, company_name, admin.email,
                    subject, html_content, text_content
                )
                if success:
                    admin_sent += 1
                results.append({
                    'email': admin.email,
                    'role': admin.role,
                    'warehouse': main_warehouse.name,
                    'success': success,
                    'error': error,
                })

    return {
        'success': (warehouse_sent + branch_sent + admin_sent) > 0,
        'warehouse_sent': warehouse_sent,
        'branch_sent': branch_sent,
        'admin_sent': admin_sent,
        'results': results,
    }


def _get_branch_stock_queryset(product_id: int, branch_id: int):
    return BranchStock.objects.filter(
        product_id=product_id,
        branch_id=branch_id,
        quantity__gt=0
    ).order_by('received_date', 'created_at')


def _ensure_branch_stock(product_id: int, branch_id: int, quantity: Decimal):
    available = BranchStock.objects.filter(
        product_id=product_id,
        branch_id=branch_id,
        quantity__gt=0
    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    if available < quantity:
        raise ValueError(f'Insufficient stock for product {product_id}. Available: {available}, requested: {quantity}.')


def _get_branch_selling_price(product_id: int, branch_id: int) -> Decimal:
    latest_stock = BranchStock.objects.filter(
        product_id=product_id,
        branch_id=branch_id,
        quantity__gt=0
    ).order_by('-received_date', '-created_at').first()
    if latest_stock and latest_stock.selling_price:
        return latest_stock.selling_price
    raise ValueError('Unable to determine selling price from branch stock.')


def _get_branch_purchase_price(product_id: int, branch_id: int, quantity: Decimal) -> Decimal:
    _ensure_branch_stock(product_id, branch_id, quantity)
    entries = _get_branch_stock_queryset(product_id, branch_id)
    remaining = quantity
    total_cost = Decimal('0')

    for entry in entries:
        if remaining <= 0:
            break
        take = entry.quantity if entry.quantity <= remaining else remaining
        total_cost += take * entry.purchase_price
        remaining -= take

    if quantity == 0:
        return Decimal('0')
    return total_cost / quantity


def _recalculate_sale_totals(sale: Sale):
    """
    Recalculate sale totals from items.
    This sums all item subtotals (which already have item-level discounts deducted).
    Sale-level discounts are stored separately in sale.discount.
    """
    # Recalculate from unit_price, quantity and item discount to be robust even
    # if the stored subtotal field is out of sync for any reason.
    total = Decimal('0')
    for item in sale.items.all():
        total += (item.unit_price * item.quantity) - (item.discount or Decimal('0'))

    sale.total_amount = total
    sale.save(update_fields=['total_amount', 'updated_at'])


def get_receipt_calculation(sale: Sale) -> Dict:
    """
    Calculate receipt breakdown with proper discount handling.
    Returns detailed calculation for receipt display.
    
    Calculation flow:
    1. Each item: (unit_price × quantity) - item_discount = item_subtotal
    2. Sale subtotal: Sum of all item_subtotals
    3. Sale-level discount: Applied to sale subtotal
    4. Tax: Added after discount
    5. Net amount: subtotal - sale_discount + tax
    """
    # Calculate item-level totals
    items_breakdown = []
    items_subtotal_before_discount = Decimal('0')
    items_total_discount = Decimal('0')
    items_subtotal_after_discount = Decimal('0')
    
    for item in sale.items.all():
        item_total_before_discount = item.unit_price * item.quantity
        item_discount = item.discount or Decimal('0')
        # Derive subtotal from price/qty/discount to avoid relying on a stale field
        item_subtotal = item_total_before_discount - item_discount
        
        items_subtotal_before_discount += item_total_before_discount
        items_total_discount += item_discount
        items_subtotal_after_discount += item_subtotal
        
        items_breakdown.append({
            'product_id': item.product_id,
            'product_name': item.product.name,
            'quantity': float(item.quantity),
            'unit_price': float(item.unit_price),
            'item_total_before_discount': float(item_total_before_discount),
            'item_discount': float(item_discount),
            'item_subtotal': float(item_subtotal),
        })
    
    # Sale-level calculations
    sale_subtotal = sale.total_amount  # Sum of all item subtotals (after item discounts)
    sale_level_discount = sale.discount  # Sale-level discount (applies to entire sale)
    tax_amount = sale.tax
    net_amount = sale.net_amount  # total_amount - sale.discount + tax
    
    return {
        'items': items_breakdown,
        'summary': {
            'items_subtotal_before_discount': float(items_subtotal_before_discount),
            'items_total_discount': float(items_total_discount),
            'sale_subtotal': float(sale_subtotal),
            'sale_level_discount': float(sale_level_discount),
            'total_discount': float(items_total_discount + sale_level_discount),
            'tax': float(tax_amount),
            'net_amount': float(net_amount),
        },
        'calculation_breakdown': {
            'step1_items_subtotal': f'Items subtotal (after item discounts): {sale_subtotal}',
            'step2_sale_discount': f'Sale-level discount: -{sale_level_discount}',
            'step3_subtotal_after_discount': f'Subtotal after discount: {sale_subtotal - sale_level_discount}',
            'step4_tax': f'Tax: +{tax_amount}',
            'step5_net_amount': f'Net amount: {net_amount}',
        }
    }


def _create_sale_item(sale: Sale, item_data: Dict) -> SaleItem:
    product = Product.objects.get(pk=item_data['product_id'])
    quantity = item_data['quantity']
    unit_price = item_data.get('unit_price')
    discount = item_data.get('discount', Decimal('0'))

    if unit_price is None:
        unit_price = _get_branch_selling_price(product.id, sale.branch_id)

    purchase_price = _get_branch_purchase_price(product.id, sale.branch_id, quantity)

    return SaleItem.objects.create(
        sale=sale,
        product=product,
        quantity=quantity,
        unit_price=unit_price,
        purchase_price=purchase_price,
        discount=discount or Decimal('0'),
    )


def _apply_discount_to_created_sale(sale: Sale, discount: Discount) -> Decimal:
    """Apply discount to a sale after items are created, based on discount type."""
    total_discount = Decimal('0')
    
    if discount.apply_to == 'product' and discount.product:
        # Apply to specific product items
        for item in sale.items.filter(product=discount.product):
            item_discount = discount.calculate_discount(item.unit_price, item.quantity)
            item.discount = item.discount + item_discount
            # Save without update_fields to trigger SaleItem.save() which recalculates subtotal
            item.save()
            total_discount += item_discount
    elif discount.apply_to == 'category' and discount.category:
        # Apply to category items
        for item in sale.items.filter(product__category=discount.category):
            item_discount = discount.calculate_discount(item.unit_price, item.quantity)
            item.discount = item.discount + item_discount
            # Save without update_fields to trigger SaleItem.save() which recalculates subtotal
            item.save()
            total_discount += item_discount
    else:
        # Apply to entire sale (all, branch, min_purchase)
        # Use the sale's total_amount which is calculated after items are created
        sale_discount = discount.calculate_discount(sale.total_amount)
        sale.discount = sale.discount + sale_discount
        sale.save(update_fields=['discount', 'updated_at'])
        total_discount = sale_discount
    
    return total_discount


def create_sale(
    branch_id: int,
    cashier,
    type_of_payment: str,
    tax: Decimal = Decimal('0'),
    notes: str = '',
    items_data: Optional[List[Dict]] = None,
    sync_id: Optional[str] = None,
    discount_code: Optional[str] = None,
    discount_id: Optional[int] = None,
) -> Sale:
    branch = Branch.objects.get(pk=branch_id)
    items_data = items_data or []

    # Check if sale with sync_id already exists
    if sync_id:
        sync_id = sync_id.strip()
        if sync_id:
            existing_sale = Sale.objects.filter(sync_id=sync_id).first()
            if existing_sale:
                return existing_sale

    # Validate and get discount if provided (manual selection only)
    discount = None
    discount_info = None
    
    if discount_code:
        discount_code = discount_code.strip()
        if discount_code:
            # Prepare items_data with unit_price for validation (get from branch stock if not provided)
            validation_items = []
            for item in items_data:
                validation_item = item.copy()
                if 'unit_price' not in validation_item or validation_item.get('unit_price') is None:
                    try:
                        validation_item['unit_price'] = _get_branch_selling_price(
                            item['product_id'], branch_id
                        )
                    except ValueError:
                        validation_item['unit_price'] = Decimal('0')
                if 'subtotal' not in validation_item:
                    validation_item['subtotal'] = (
                        validation_item.get('unit_price', Decimal('0')) * 
                        validation_item.get('quantity', Decimal('1'))
                    )
                validation_items.append(validation_item)
            
            # Create a temporary sale object for validation
            temp_sale = Sale(branch=branch, cashier=cashier)
            discount, message = validate_discount_code(discount_code, temp_sale, validation_items)
            if not discount:
                raise ValueError(f'Invalid discount code: {message}')
            
            discount_info = {
                'discount_id': discount.id,
                'discount_code': discount.code,
                'discount_name': discount.name,
                'auto_applied': False,
                'manually_selected': True,
            }
    elif discount_id:
        try:
            discount = Discount.objects.get(pk=discount_id)
        except Discount.DoesNotExist:
            raise ValueError('Discount not found')
        
        # Prepare items_data with unit_price for validation
        validation_items = []
        for item in items_data:
            validation_item = item.copy()
            if 'unit_price' not in validation_item or validation_item.get('unit_price') is None:
                try:
                    validation_item['unit_price'] = _get_branch_selling_price(
                        item['product_id'], branch_id
                    )
                except ValueError:
                    validation_item['unit_price'] = Decimal('0')
            if 'subtotal' not in validation_item:
                validation_item['subtotal'] = (
                    validation_item.get('unit_price', Decimal('0')) * 
                    validation_item.get('quantity', Decimal('1'))
                )
            validation_items.append(validation_item)
        
        # Validate discount can be applied
        temp_sale = Sale(branch=branch, cashier=cashier)
        can_apply, message = discount.can_apply_to_sale(temp_sale, validation_items)
        if not can_apply:
            raise ValueError(f'Discount cannot be applied: {message}')
        
        discount_info = {
            'discount_id': discount.id,
            'discount_code': discount.code,
            'discount_name': discount.name,
            'auto_applied': False,
            'manually_selected': True,
        }

    with transaction.atomic():
        # Create sale first
        sale = Sale.objects.create(
            sale_number=_generate_sale_number(),
            branch=branch,
            cashier=cashier,
            type_of_payment=type_of_payment,
            discount=Decimal('0'),  # Will be updated if discount applies to sale total
            tax=tax or Decimal('0'),
            notes=notes or '',
            status='pending',
            sync_id=sync_id if sync_id else None,
        )

        # Create sale items first (so we have actual unit prices)
        if items_data:
            for item in items_data:
                _create_sale_item(sale, item)
            _recalculate_sale_totals(sale)

        # Apply discount after items are created (so we have actual prices)
        if discount:
            # Manually selected discount (code / id passed from client)
            _apply_discount_to_created_sale(sale, discount)
            _recalculate_sale_totals(sale)
            discount.increment_usage()
            sale._discount_info = discount_info
        else:
            # No explicit discount passed – auto-apply product-linked discounts
            # so that net amount reflects product discounts configured in the system.
            product_ids = list(
                sale.items.values_list('product_id', flat=True).distinct()
            )
            auto_discounts_info: List[Dict] = []

            if product_ids:
                now = timezone.now()
                # Active product-level discounts for products in this sale,
                # valid for this branch or global (no branch set).
                auto_discounts = Discount.objects.filter(
                    is_active=True,
                    apply_to='product',
                    product_id__in=product_ids,
                ).filter(
                    Q(branch_id=branch_id) | Q(branch__isnull=True)
                ).filter(
                    Q(start_date__isnull=True) | Q(start_date__lte=now),
                    Q(end_date__isnull=True) | Q(end_date__gte=now),
                    Q(usage_limit__isnull=True) | Q(usage_count__lt=F('usage_limit')),
                )

                total_auto_discount = Decimal('0')
                for d in auto_discounts:
                    discount_amount = _apply_discount_to_created_sale(sale, d)
                    if discount_amount > 0:
                        # Increment usage once per discount applied on this sale
                        d.increment_usage()
                        total_auto_discount += discount_amount
                        auto_discounts_info.append(
                            {
                                'discount_id': d.id,
                                'discount_code': d.code,
                                'discount_name': d.name,
                                'auto_applied': True,
                                'manually_selected': False,
                                'amount': discount_amount,
                            }
                        )

                if auto_discounts_info:
                    # Update Sale.discount to reflect total of all item-level discounts
                    sale.discount = total_auto_discount
                    sale.save(update_fields=['discount', 'updated_at'])
                    _recalculate_sale_totals(sale)
                    sale._discount_info = {
                        'auto_applied': True,
                        'manually_selected': False,
                        'discounts': auto_discounts_info,
                    }
                else:
                    sale._discount_info = None

    return sale


def bulk_create_sales(
    sales_data: List[Dict],
    cashier,
) -> (List[Sale], List[Dict]):
    """
    Create multiple sales in bulk (for offline sync).
    Handles discount validation errors gracefully - creates sale without discount if discount is invalid.
    """
    created_sales: List[Sale] = []
    errors: List[Dict] = []

    for index, sale_data in enumerate(sales_data):
        try:
            # Try to create sale with discount
            sale = create_sale(
                branch_id=sale_data.get('branch_id'),
                cashier=cashier,
                type_of_payment=sale_data.get('type_of_payment', 'cash'),
                tax=sale_data.get('tax', Decimal('0')),
                notes=sale_data.get('notes', ''),
                items_data=sale_data.get('items', []),
                sync_id=sale_data.get('sync_id'),
                discount_code=sale_data.get('discount_code'),
                discount_id=sale_data.get('discount_id'),
            )
            created_sales.append(sale)
        except ValueError as exc:
            # If it's a discount validation error, try creating without discount
            error_msg = str(exc)
            if 'discount' in error_msg.lower() or 'Discount' in error_msg:
                try:
                    # Create sale without discount
                    sale = create_sale(
                        branch_id=sale_data.get('branch_id'),
                        cashier=cashier,
                        type_of_payment=sale_data.get('type_of_payment', 'cash'),
                        tax=sale_data.get('tax', Decimal('0')),
                        notes=sale_data.get('notes', ''),
                        items_data=sale_data.get('items', []),
                        sync_id=sale_data.get('sync_id'),
                        discount_code=None,  # Remove invalid discount
                        discount_id=None,
                    )
                    # Add discount error info to sale
                    sale._discount_info = {
                        'error': error_msg,
                        'discount_code': sale_data.get('discount_code'),
                        'discount_id': sale_data.get('discount_id'),
                        'auto_applied': False,
                        'rejected': True,
                    }
                    created_sales.append(sale)
                except Exception as inner_exc:
                    # If sale creation still fails, add to errors
                    errors.append({
                        'index': index,
                        'data': sale_data,
                        'error': f'Discount error: {error_msg}. Sale creation also failed: {str(inner_exc)}',
                        'discount_error': error_msg,
                    })
            else:
                # Non-discount error, add to errors
                errors.append({
                    'index': index,
                    'data': sale_data,
                    'error': error_msg,
                })
        except Exception as exc:
            # Other errors
            errors.append({
                'index': index,
                'data': sale_data,
                'error': str(exc),
            })

    return created_sales, errors


def add_items_to_sale(sale: Sale, items_data: List[Dict]) -> Sale:
    if sale.status != 'pending':
        raise ValueError('Only pending sales can be modified.')

    with transaction.atomic():
        for item in items_data:
            _create_sale_item(sale, item)
        _recalculate_sale_totals(sale)

    return sale


def complete_sale(sale: Sale, completed_by=None) -> Sale:
    if sale.status != 'pending':
        raise ValueError('Only pending sales can be completed.')
    if not sale.items.exists():
        raise ValueError('Cannot complete a sale without items.')

    with transaction.atomic():
        for item in sale.items.all():
            stock_services.remove_stock_from_branch(
                product_id=item.product_id,
                branch_id=sale.branch_id,
                quantity=item.quantity,
                reason=f'Sale completion: {sale.sale_number}',
                created_by=completed_by,
            )
        sale.status = 'completed'
        sale.save(update_fields=['status', 'updated_at'])

    return sale


def cancel_sale(sale: Sale) -> Sale:
    if sale.status != 'pending':
        raise ValueError('Only pending sales can be cancelled.')
    sale.status = 'cancelled'
    sale.save(update_fields=['status', 'updated_at'])
    return sale


def process_sale_return(
    sale: Sale,
    product_id: int,
    quantity: Decimal,
    reason: str = '',
    refund_amount: Optional[Decimal] = None,
    processed_by=None,
) -> ProductReturn:
    if sale.status not in ['completed', 'returned']:
        raise ValueError('Only completed sales can have returns.')

    items = sale.items.filter(product_id=product_id)
    if not items.exists():
        raise ValueError('Product does not exist in this sale.')

    sold_quantity = items.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    already_returned = sale.returns.filter(product_id=product_id).aggregate(total=Sum('quantity'))['total'] or Decimal('0')

    if quantity + already_returned > sold_quantity:
        raise ValueError('Return quantity exceeds quantity sold.')

    # Determine average purchase and selling prices from sale items
    total_cost = sum((item.purchase_price * item.quantity) for item in items)
    total_sale = sum((item.unit_price * item.quantity) for item in items)
    avg_purchase_price = total_cost / sold_quantity if sold_quantity else Decimal('0')
    avg_selling_price = total_sale / sold_quantity if sold_quantity else Decimal('0')

    print(f'avg_purchase_price: {avg_purchase_price}')
    print(f'avg_selling_price: {avg_selling_price}')

    if refund_amount is None:
        refund_amount = avg_selling_price * quantity

    with transaction.atomic():
        branch_stock_entry = BranchStock.objects.filter(
            product_id=product_id,
            branch_id=sale.branch_id,
            quantity__gt=0
        ).order_by('-received_date', '-created_at').first()

        if branch_stock_entry and branch_stock_entry.selling_price == avg_selling_price and branch_stock_entry.purchase_price == avg_purchase_price:
            print("incrementing stock entry quantity")
            branch_stock_entry.quantity += quantity
            branch_stock_entry.save(update_fields=['quantity'])
        else:
            print("creating new stock entry for return")
            stock_services.add_stock_to_branch(
                product_id=product_id,
                branch_id=sale.branch_id,
                quantity=quantity,
                purchase_price=avg_purchase_price,
                selling_price=avg_selling_price,
                notes=f'Return for sale {sale.sale_number}',
                created_by=processed_by,
            )

        product_return = ProductReturn.objects.create(
            sale=sale,
            product_id=product_id,
            quantity=quantity,
            reason=reason or '',
            refund_amount=refund_amount,
            processed_by=processed_by,
        )

        sale.status = 'returned'
        sale.save(update_fields=['status', 'updated_at'])

    return product_return


def get_daily_sales_report_data(report_date: Optional[date] = None) -> Dict:
    """
    Generate daily sales report data for a specific date.
    If no date is provided, uses today's date.
    
    Returns a dictionary with:
    - report_date: The date of the report
    - overall_summary: Overall statistics
    - branch_summaries: Per-branch statistics
    - payment_methods: Breakdown by payment method
    """
    if report_date is None:
        report_date = timezone.now().date()
    
    # Get all completed sales for the date
    sales = Sale.objects.filter(
        created_at__date=report_date,
        status='completed'
    ).select_related('branch', 'cashier').prefetch_related('items__product')
    
    # Overall summary
    total_sales = sales.count()
    total_amount = sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
    total_discount = sales.aggregate(total=Sum('discount'))['total'] or Decimal('0')
    total_tax = sales.aggregate(total=Sum('tax'))['total'] or Decimal('0')
    
    # Calculate total cost and profit from sale items
    total_cost = Decimal('0')
    total_profit = Decimal('0')
    total_items_sold = Decimal('0')
    
    for sale in sales:
        for item in sale.items.all():
            item_cost = item.purchase_price * item.quantity
            item_profit = item.subtotal - item_cost
            total_cost += item_cost
            total_profit += item_profit
            total_items_sold += item.quantity
    
    overall_summary = {
        'total_sales': total_sales,
        'total_amount': total_amount,
        'total_discount': total_discount,
        'total_tax': total_tax,
        'total_cost': total_cost,
        'total_profit': total_profit,
        'total_items_sold': total_items_sold,
        'profit_margin': (total_profit / total_amount * 100) if total_amount > 0 else Decimal('0'),
    }
    
    # Branch summaries
    branch_summaries = []
    branches = Branch.objects.filter(sales__in=sales).distinct()
    
    for branch in branches:
        branch_sales = sales.filter(branch=branch)
        branch_total_sales = branch_sales.count()
        branch_total_amount = branch_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        branch_total_discount = branch_sales.aggregate(total=Sum('discount'))['total'] or Decimal('0')
        branch_total_tax = branch_sales.aggregate(total=Sum('tax'))['total'] or Decimal('0')
        
        branch_cost = Decimal('0')
        branch_profit = Decimal('0')
        branch_items_sold = Decimal('0')
        
        for sale in branch_sales:
            for item in sale.items.all():
                item_cost = item.purchase_price * item.quantity
                item_profit = item.subtotal - item_cost
                branch_cost += item_cost
                branch_profit += item_profit
                branch_items_sold += item.quantity
        
        branch_summaries.append({
            'branch_name': branch.name,
            'total_sales': branch_total_sales,
            'total_amount': branch_total_amount,
            'total_discount': branch_total_discount,
            'total_tax': branch_total_tax,
            'total_cost': branch_cost,
            'total_profit': branch_profit,
            'total_items_sold': branch_items_sold,
            'profit_margin': (branch_profit / branch_total_amount * 100) if branch_total_amount > 0 else Decimal('0'),
        })
    
    # Payment method breakdown
    payment_methods = {}
    for payment_type, _ in Sale.TYPE_OF_PAYMENT_CHOICES:
        method_sales = sales.filter(type_of_payment=payment_type)
        method_count = method_sales.count()
        method_total = method_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        payment_methods[payment_type] = {
            'count': method_count,
            'total': method_total,
            'percentage': (method_total / total_amount * 100) if total_amount > 0 else Decimal('0'),
        }
    
    return {
        'report_date': report_date,
        'overall_summary': overall_summary,
        'branch_summaries': branch_summaries,
        'payment_methods': payment_methods,
    }


def generate_daily_sales_report_pdf(report_data: Dict) -> bytes:
    """
    Generate a PDF report from daily sales report data.
    Returns the PDF as bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#0f766e'),
        spaceAfter=30,
        alignment=1,  # Center alignment
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0f172a'),
        spaceAfter=12,
        spaceBefore=12,
    )
    
    normal_style = styles['Normal']
    
    # Title
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    title = Paragraph(f'{company_name} - Daily Sales Report', title_style)
    story.append(title)
    
    # Report Date
    report_date = report_data['report_date']
    date_str = report_date.strftime('%B %d, %Y')
    date_para = Paragraph(f'<b>Report Date:</b> {date_str}', normal_style)
    story.append(date_para)
    story.append(Spacer(1, 0.2*inch))
    
    # Overall Summary
    overall = report_data['overall_summary']
    story.append(Paragraph('Overall Summary', heading_style))
    
    summary_data = [
        ['Metric', 'Value'],
        ['Total Sales', f"{overall['total_sales']}"],
        ['Total Revenue', f"${overall['total_amount']:,.2f}"],
        ['Total Discount', f"${overall['total_discount']:,.2f}"],
        ['Total Tax', f"${overall['total_tax']:,.2f}"],
        ['Total Cost', f"${overall['total_cost']:,.2f}"],
        ['Total Profit', f"${overall['total_profit']:,.2f}"],
        ['Profit Margin', f"{overall['profit_margin']:.2f}%"],
        ['Total Items Sold', f"{overall['total_items_sold']:,.2f}"],
    ]
    
    summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.3*inch))
    
    # Branch Summaries
    branch_summaries = report_data['branch_summaries']
    if branch_summaries:
        story.append(Paragraph('Branch Performance', heading_style))
        
        branch_data = [['Branch', 'Sales', 'Revenue', 'Cost', 'Profit', 'Margin']]
        for branch in branch_summaries:
            branch_data.append([
                branch['branch_name'],
                str(branch['total_sales']),
                f"${branch['total_amount']:,.2f}",
                f"${branch['total_cost']:,.2f}",
                f"${branch['total_profit']:,.2f}",
                f"{branch['profit_margin']:.2f}%",
            ])
        
        branch_table = Table(branch_data, colWidths=[1.5*inch, 0.8*inch, 1*inch, 1*inch, 1*inch, 0.8*inch])
        branch_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ]))
        story.append(branch_table)
        story.append(Spacer(1, 0.3*inch))
    
    # Payment Methods
    payment_methods = report_data['payment_methods']
    if payment_methods:
        story.append(Paragraph('Payment Method Breakdown', heading_style))
        
        payment_data = [['Payment Method', 'Count', 'Total', 'Percentage']]
        for method, data in payment_methods.items():
            method_display = dict(Sale.TYPE_OF_PAYMENT_CHOICES).get(method, method.replace('_', ' ').title())
            payment_data.append([
                method_display,
                str(data['count']),
                f"${data['total']:,.2f}",
                f"{data['percentage']:.2f}%",
            ])
        
        payment_table = Table(payment_data, colWidths=[2*inch, 1*inch, 1.5*inch, 1.5*inch])
        payment_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ]))
        story.append(payment_table)
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def send_daily_sales_report_email(report_date: Optional[date] = None) -> Dict:
    """
    Generate and send daily sales report PDF via email to admin, owner, and branch managers.
    Also saves the report data to the database for historical tracking.
    Returns a dictionary with success status and details.
    """
    if report_date is None:
        report_date = timezone.now().date()
    
    # Get report data
    report_data = get_daily_sales_report_data(report_date)
    
    # Save report data to database
    overall_summary = report_data['overall_summary']
    
    # Convert JSON fields to serializable format (Decimal to float)
    branch_summaries_serializable = _make_json_serializable(report_data['branch_summaries'])
    payment_methods_serializable = _make_json_serializable(report_data['payment_methods'])
    
    DailySalesReport.objects.update_or_create(
        report_date=report_date,
        defaults={
            'total_sales': overall_summary['total_sales'],
            'total_amount': overall_summary['total_amount'],
            'total_discount': overall_summary['total_discount'],
            'total_tax': overall_summary['total_tax'],
            'total_cost': overall_summary['total_cost'],
            'total_profit': overall_summary['total_profit'],
            'total_items_sold': overall_summary['total_items_sold'],
            'profit_margin': overall_summary['profit_margin'],
            'branch_summaries': branch_summaries_serializable,
            'payment_methods': payment_methods_serializable,
        }
    )
    
    # Generate PDF
    pdf_bytes = generate_daily_sales_report_pdf(report_data)
    pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
    
    # Get recipients
    recipients = []
    
    # Get all admins
    admins = User.objects.filter(role='admin', is_active=True, account_status='active')
    for admin in admins:
        employee = admin.profile.first() if hasattr(admin, 'profile') else None
        recipients.append({
            'email': admin.email,
            'name': employee.get_full_name() if employee else admin.email,
            'role': 'Admin'
        })
    
    # Get all owners
    owners = User.objects.filter(role='owner', is_active=True, account_status='active')
    for owner in owners:
        employee = owner.profile.first() if hasattr(owner, 'profile') else None
        recipients.append({
            'email': owner.email,
            'name': employee.get_full_name() if employee else owner.email,
            'role': 'Owner'
        })
    
    # Get all branch managers
    branch_managers = User.objects.filter(
        role='branch_manager',
        is_active=True,
        account_status='active'
    ).prefetch_related('profile')
    for manager in branch_managers:
        employee = manager.profile.first() if hasattr(manager, 'profile') else None
        if employee:
            recipients.append({
                'email': manager.email,
                'name': employee.get_full_name() or manager.email,
                'role': 'Branch Manager',
                'branch': employee.branch.name if employee.branch else None
            })
    
    if not recipients:
        return {
            'success': False,
            'message': 'No recipients found for daily sales report',
            'recipients_count': 0
        }
    
    # Email configuration
    api_key = getattr(settings, 'MAILJET_API_KEY', '')
    api_secret = getattr(settings, 'MAILJET_API_SECRET', '')
    
    if not api_key or not api_secret:
        return {
            'success': False,
            'message': 'Mailjet credentials are not configured',
            'recipients_count': len(recipients)
        }
    
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@decormasters.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    report_date_str = report_date.strftime('%B %d, %Y')
    
    # Prepare email content
    subject = f'Daily Sales Report - {report_date_str}'
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #0f766e;">Daily Sales Report</h2>
            <p>Please find attached the daily sales report for <strong>{report_date_str}</strong>.</p>
            <p><strong>Summary:</strong></p>
            <ul>
                <li>Total Sales: {report_data['overall_summary']['total_sales']}</li>
                <li>Total Revenue: ${report_data['overall_summary']['total_amount']:,.2f}</li>
                <li>Total Profit: ${report_data['overall_summary']['total_profit']:,.2f}</li>
                <li>Profit Margin: {report_data['overall_summary']['profit_margin']:.2f}%</li>
            </ul>
            <p>Best regards,<br>{company_name}</p>
        </div>
    </body>
    </html>
    """
    
    text_content = f"""
    Daily Sales Report
    
    Please find attached the daily sales report for {report_date_str}.
    
    Summary:
    - Total Sales: {report_data['overall_summary']['total_sales']}
    - Total Revenue: ${report_data['overall_summary']['total_amount']:,.2f}
    - Total Profit: ${report_data['overall_summary']['total_profit']:,.2f}
    - Profit Margin: {report_data['overall_summary']['profit_margin']:.2f}%
    
    Best regards,
    {company_name}
    """
    
    # Send email to each recipient
    mailjet = Client(auth=(api_key, api_secret), version='v3.1')
    results = []
    
    for recipient in recipients:
        data = {
            'Messages': [
                {
                    'From': {
                        'Email': from_email,
                        'Name': company_name
                    },
                    'To': [
                        {
                            'Email': recipient['email'],
                            'Name': recipient['name']
                        }
                    ],
                    'Subject': subject,
                    'TextPart': text_content,
                    'HTMLPart': html_content,
                    'Attachments': [
                        {
                            'ContentType': 'application/pdf',
                            'Filename': f'daily_sales_report_{report_date.strftime("%Y%m%d")}.pdf',
                            'Base64Content': pdf_base64
                        }
                    ]
                }
            ]
        }
        
        try:
            result = mailjet.send.create(data=data)
            if result.status_code in (200, 201):
                results.append({
                    'email': recipient['email'],
                    'role': recipient['role'],
                    'success': True
                })
            else:
                results.append({
                    'email': recipient['email'],
                    'role': recipient['role'],
                    'success': False,
                    'error': f'Status {result.status_code}'
                })
        except Exception as e:
            results.append({
                'email': recipient['email'],
                'role': recipient['role'],
                'success': False,
                'error': str(e)
            })
    
    successful = sum(1 for r in results if r.get('success', False))
    
    return {
        'success': successful > 0,
        'message': f'Sent to {successful} out of {len(recipients)} recipients',
        'recipients_count': len(recipients),
        'successful_count': successful,
        'results': results
    }


def get_monthly_sales_report_data(report_month: Optional[int] = None, report_year: Optional[int] = None) -> Dict:
    """
    Generate monthly sales report data for a specific month and year.
    If no month/year is provided, uses the previous month.
    
    Returns a dictionary with:
    - report_month: The month of the report (1-12)
    - report_year: The year of the report
    - start_date: First day of the month
    - end_date: Last day of the month
    - overall_summary: Overall statistics
    - branch_summaries: Per-branch statistics
    - payment_methods: Breakdown by payment method
    """
    now = timezone.now()
    if report_year is None:
        report_year = now.year
    if report_month is None:
        # Default to previous month
        if now.month == 1:
            report_month = 12
            report_year -= 1
        else:
            report_month = now.month - 1
    
    # Get first and last day of the month
    start_date = date(report_year, report_month, 1)
    last_day = calendar.monthrange(report_year, report_month)[1]
    end_date = date(report_year, report_month, last_day)
    
    # Get all completed sales for the month
    sales = Sale.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
        status='completed'
    ).select_related('branch', 'cashier').prefetch_related('items__product')
    
    # Overall summary - convert ZIG amounts to USD
    total_sales = sales.count()
    total_amount = Decimal('0')
    total_discount = Decimal('0')
    total_tax = Decimal('0')
    total_cost = Decimal('0')
    total_profit = Decimal('0')
    total_items_sold = Decimal('0')
    
    for sale in sales:
        payment_method = sale.type_of_payment
        # Convert sale amounts from ZIG to USD if needed
        sale_total = convert_zig_to_usd(sale.total_amount, payment_method)
        sale_discount = convert_zig_to_usd(sale.discount, payment_method)
        sale_tax = convert_zig_to_usd(sale.tax, payment_method)
        
        total_amount += sale_total
        total_discount += sale_discount
        total_tax += sale_tax
        
        for item in sale.items.all():
            # Convert item amounts from ZIG to USD if needed
            item_subtotal = convert_zig_to_usd(item.subtotal, payment_method)
            item_cost = item.purchase_price * item.quantity
            item_cost_usd = convert_zig_to_usd(item_cost, payment_method)
            item_profit = item_subtotal - item_cost_usd
            
            total_cost += item_cost_usd
            total_profit += item_profit
            total_items_sold += item.quantity
    
    overall_summary = {
        'total_sales': total_sales,
        'total_amount': total_amount,
        'total_discount': total_discount,
        'total_tax': total_tax,
        'total_cost': total_cost,
        'total_profit': total_profit,
        'total_items_sold': total_items_sold,
        'profit_margin': (total_profit / total_amount * 100) if total_amount > 0 else Decimal('0'),
    }
    
    # Branch summaries
    branch_summaries = []
    branches = Branch.objects.filter(sales__in=sales).distinct()
    
    for branch in branches:
        branch_sales = sales.filter(branch=branch)
        branch_total_sales = branch_sales.count()
        branch_total_amount = Decimal('0')
        branch_total_discount = Decimal('0')
        branch_total_tax = Decimal('0')
        branch_cost = Decimal('0')
        branch_profit = Decimal('0')
        branch_items_sold = Decimal('0')
        
        for sale in branch_sales:
            payment_method = sale.type_of_payment
            # Convert sale amounts from ZIG to USD if needed
            sale_total = convert_zig_to_usd(sale.total_amount, payment_method)
            sale_discount = convert_zig_to_usd(sale.discount, payment_method)
            sale_tax = convert_zig_to_usd(sale.tax, payment_method)
            
            branch_total_amount += sale_total
            branch_total_discount += sale_discount
            branch_total_tax += sale_tax
            
            for item in sale.items.all():
                # Convert item amounts from ZIG to USD if needed
                item_subtotal = convert_zig_to_usd(item.subtotal, payment_method)
                item_cost = item.purchase_price * item.quantity
                item_cost_usd = convert_zig_to_usd(item_cost, payment_method)
                item_profit = item_subtotal - item_cost_usd
                
                branch_cost += item_cost_usd
                branch_profit += item_profit
                branch_items_sold += item.quantity
        
        branch_summaries.append({
            'branch_id': branch.id,
            'branch_name': branch.name,
            'total_sales': branch_total_sales,
            'total_amount': branch_total_amount,
            'total_discount': branch_total_discount,
            'total_tax': branch_total_tax,
            'total_cost': branch_cost,
            'total_profit': branch_profit,
            'total_items_sold': branch_items_sold,
            'profit_margin': (branch_profit / branch_total_amount * 100) if branch_total_amount > 0 else Decimal('0'),
        })
    
    # Payment method breakdown - convert ZIG amounts to USD
    payment_methods = {}
    for payment_type, _ in Sale.TYPE_OF_PAYMENT_CHOICES:
        method_sales = sales.filter(type_of_payment=payment_type)
        method_count = method_sales.count()
        method_total = Decimal('0')
        
        for sale in method_sales:
            sale_total = convert_zig_to_usd(sale.total_amount, payment_type)
            method_total += sale_total
        
        payment_methods[payment_type] = {
            'count': method_count,
            'total': method_total,
            'percentage': (method_total / total_amount * 100) if total_amount > 0 else Decimal('0'),
        }
    
    month_name = calendar.month_name[report_month]
    
    return {
        'report_month': report_month,
        'report_year': report_year,
        'month_name': month_name,
        'start_date': start_date,
        'end_date': end_date,
        'overall_summary': overall_summary,
        'branch_summaries': branch_summaries,
        'payment_methods': payment_methods,
    }


def generate_monthly_sales_report_pdf(report_data: Dict, branch_name: Optional[str] = None) -> bytes:
    """
    Generate a PDF report from monthly sales report data.
    Returns the PDF as bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#0f766e'),
        spaceAfter=30,
        alignment=1,  # Center alignment
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0f172a'),
        spaceAfter=12,
        spaceBefore=12,
    )
    
    normal_style = styles['Normal']
    
    # Title
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    title_text = f'{company_name} - Monthly Sales Report'
    if branch_name:
        title_text += f' - {branch_name}'
    title = Paragraph(title_text, title_style)
    story.append(title)
    
    # Report Period
    month_name = report_data['month_name']
    year = report_data['report_year']
    start_date = report_data['start_date']
    end_date = report_data['end_date']
    period_str = f'{month_name} {year} ({start_date.strftime("%B %d")} - {end_date.strftime("%B %d, %Y")})'
    period_para = Paragraph(f'<b>Report Period:</b> {period_str}', normal_style)
    story.append(period_para)
    story.append(Spacer(1, 0.2*inch))
    
    # Overall Summary
    overall = report_data['overall_summary']
    story.append(Paragraph('Overall Summary', heading_style))
    
    summary_data = [
        ['Metric', 'Value'],
        ['Total Sales', f"{overall['total_sales']}"],
        ['Total Revenue', f"${overall['total_amount']:,.2f}"],
        ['Total Discount', f"${overall['total_discount']:,.2f}"],
        ['Total Tax', f"${overall['total_tax']:,.2f}"],
        ['Total Cost', f"${overall['total_cost']:,.2f}"],
        ['Total Profit', f"${overall['total_profit']:,.2f}"],
        ['Profit Margin', f"{overall['profit_margin']:.2f}%"],
        ['Total Items Sold', f"{overall['total_items_sold']:,.2f}"],
    ]
    
    summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.3*inch))
    
    # Branch Summaries (only if not branch-specific report)
    if not branch_name and report_data['branch_summaries']:
        story.append(Paragraph('Branch Breakdown', heading_style))
        
        branch_data = [['Branch', 'Sales', 'Revenue', 'Profit', 'Margin']]
        for branch in report_data['branch_summaries']:
            branch_data.append([
                branch['branch_name'],
                f"{branch['total_sales']}",
                f"${branch['total_amount']:,.2f}",
                f"${branch['total_profit']:,.2f}",
                f"{branch['profit_margin']:.2f}%",
            ])
        
        branch_table = Table(branch_data, colWidths=[2*inch, 1*inch, 1.5*inch, 1.5*inch, 1*inch])
        branch_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        story.append(branch_table)
        story.append(Spacer(1, 0.3*inch))
    
    # Payment Methods
    if report_data['payment_methods']:
        story.append(Paragraph('Payment Methods Breakdown', heading_style))
        
        payment_data = [['Payment Method', 'Count', 'Amount', 'Percentage']]
        for method, data in report_data['payment_methods'].items():
            if data['count'] > 0:
                payment_data.append([
                    method.replace('_', ' ').title(),
                    f"{data['count']}",
                    f"${data['total']:,.2f}",
                    f"{data['percentage']:.2f}%",
                ])
        
        payment_table = Table(payment_data, colWidths=[2*inch, 1*inch, 1.5*inch, 1.5*inch])
        payment_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        story.append(payment_table)
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def send_monthly_sales_report_email(report_month: Optional[int] = None, report_year: Optional[int] = None) -> Dict:
    """
    Generate and send monthly sales report PDF via email to admin, owner (organization-wide),
    and branch managers (branch-specific).
    Returns a dictionary with success status and details.
    """
    # Get report data
    report_data = get_monthly_sales_report_data(report_month, report_year)
    
    # Email configuration
    api_key = getattr(settings, 'MAILJET_API_KEY', '')
    api_secret = getattr(settings, 'MAILJET_API_SECRET', '')
    
    if not api_key or not api_secret:
        return {
            'success': False,
            'message': 'Mailjet credentials are not configured',
            'recipients_count': 0
        }
    
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@decormasters.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    month_name = report_data['month_name']
    year = report_data['report_year']
    period_str = f'{month_name} {year}'
    
    # Generate organization-wide PDF for admins/owners
    org_pdf_bytes = generate_monthly_sales_report_pdf(report_data, branch_name=None)
    org_pdf_base64 = base64.b64encode(org_pdf_bytes).decode('utf-8')
    
    # Prepare email content for organization-wide report
    org_subject = f'Monthly Sales Report - {period_str}'
    org_html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #0f766e;">Monthly Sales Report</h2>
            <p>Please find attached the monthly sales report for <strong>{period_str}</strong>.</p>
            <p><strong>Summary:</strong></p>
            <ul>
                <li>Total Sales: {report_data['overall_summary']['total_sales']}</li>
                <li>Total Revenue: ${report_data['overall_summary']['total_amount']:,.2f}</li>
                <li>Total Profit: ${report_data['overall_summary']['total_profit']:,.2f}</li>
                <li>Profit Margin: {report_data['overall_summary']['profit_margin']:.2f}%</li>
                <li>Total Items Sold: {report_data['overall_summary']['total_items_sold']:,.2f}</li>
            </ul>
            <p>Report Period: {report_data['start_date'].strftime('%B %d')} - {report_data['end_date'].strftime('%B %d, %Y')}</p>
            <p>Best regards,<br>{company_name}</p>
        </div>
    </body>
    </html>
    """
    
    org_text_content = f"""
    Monthly Sales Report
    
    Please find attached the monthly sales report for {period_str}.
    
    Summary:
    - Total Sales: {report_data['overall_summary']['total_sales']}
    - Total Revenue: ${report_data['overall_summary']['total_amount']:,.2f}
    - Total Profit: ${report_data['overall_summary']['total_profit']:,.2f}
    - Profit Margin: {report_data['overall_summary']['profit_margin']:.2f}%
    - Total Items Sold: {report_data['overall_summary']['total_items_sold']:,.2f}
    
    Report Period: {report_data['start_date'].strftime('%B %d')} - {report_data['end_date'].strftime('%B %d, %Y')}
    
    Best regards,
    {company_name}
    """
    
    # Get recipients
    recipients = []
    
    # Get all admins and owners
    admins = User.objects.filter(role__in=['admin', 'owner'], is_active=True, account_status='active')
    for admin in admins:
        employee = admin.profile.first() if hasattr(admin, 'profile') else None
        recipients.append({
            'email': admin.email,
            'name': employee.get_full_name() if employee and employee.get_full_name() else admin.email,
            'role': admin.role,
            'branch_id': None,  # Organization-wide
        })
    
    # Get all branch managers
    branch_managers = User.objects.filter(
        role='branch_manager',
        is_active=True,
        account_status='active'
    ).prefetch_related('profile')
    
    for manager in branch_managers:
        employee = manager.profile.first() if hasattr(manager, 'profile') else None
        if employee and employee.branch:
            recipients.append({
                'email': manager.email,
                'name': employee.get_full_name() if employee.get_full_name() else manager.email,
                'role': 'branch_manager',
                'branch_id': employee.branch.id,
                'branch_name': employee.branch.name,
            })
    
    if not recipients:
        return {
            'success': False,
            'message': 'No recipients found for monthly sales report',
            'recipients_count': 0
        }
    
    # Send email to each recipient
    mailjet = Client(auth=(api_key, api_secret), version='v3.1')
    results = []
    
    for recipient in recipients:
        # For branch managers, generate branch-specific report
        if recipient['branch_id']:
            branch_name = recipient['branch_name']
            # Filter branch data
            branch_data = {
                'report_month': report_data['report_month'],
                'report_year': report_data['report_year'],
                'month_name': report_data['month_name'],
                'start_date': report_data['start_date'],
                'end_date': report_data['end_date'],
                'overall_summary': next(
                    (b for b in report_data['branch_summaries'] if b['branch_id'] == recipient['branch_id']),
                    {
                        'total_sales': 0,
                        'total_amount': Decimal('0'),
                        'total_discount': Decimal('0'),
                        'total_tax': Decimal('0'),
                        'total_cost': Decimal('0'),
                        'total_profit': Decimal('0'),
                        'total_items_sold': Decimal('0'),
                        'profit_margin': Decimal('0'),
                    }
                ),
                'branch_summaries': [],  # No branch breakdown for branch-specific report
                'payment_methods': report_data['payment_methods'],  # Keep payment methods
            }
            
            branch_pdf_bytes = generate_monthly_sales_report_pdf(branch_data, branch_name=branch_name)
            branch_pdf_base64 = base64.b64encode(branch_pdf_bytes).decode('utf-8')
            
            branch_subject = f'Monthly Sales Report - {branch_name} - {period_str}'
            branch_html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #0f766e;">Monthly Sales Report - {branch_name}</h2>
                    <p>Hello {recipient['name']},</p>
                    <p>Please find attached the monthly sales report for <strong>{branch_name}</strong> for {period_str}.</p>
                    <p><strong>Summary:</strong></p>
                    <ul>
                        <li>Total Sales: {branch_data['overall_summary']['total_sales']}</li>
                        <li>Total Revenue: ${branch_data['overall_summary']['total_amount']:,.2f}</li>
                        <li>Total Profit: ${branch_data['overall_summary']['total_profit']:,.2f}</li>
                        <li>Profit Margin: {branch_data['overall_summary']['profit_margin']:.2f}%</li>
                        <li>Total Items Sold: {branch_data['overall_summary']['total_items_sold']:,.2f}</li>
                    </ul>
                    <p>Report Period: {report_data['start_date'].strftime('%B %d')} - {report_data['end_date'].strftime('%B %d, %Y')}</p>
                    <p>Best regards,<br>{company_name}</p>
                </div>
            </body>
            </html>
            """
            
            branch_text_content = f"""
            Monthly Sales Report - {branch_name}
            
            Hello {recipient['name']},
            
            Please find attached the monthly sales report for {branch_name} for {period_str}.
            
            Summary:
            - Total Sales: {branch_data['overall_summary']['total_sales']}
            - Total Revenue: ${branch_data['overall_summary']['total_amount']:,.2f}
            - Total Profit: ${branch_data['overall_summary']['total_profit']:,.2f}
            - Profit Margin: {branch_data['overall_summary']['profit_margin']:.2f}%
            - Total Items Sold: {branch_data['overall_summary']['total_items_sold']:,.2f}
            
            Report Period: {report_data['start_date'].strftime('%B %d')} - {report_data['end_date'].strftime('%B %d, %Y')}
            
            Best regards,
            {company_name}
            """
            
            pdf_base64 = branch_pdf_base64
            subject = branch_subject
            html_content = branch_html_content
            text_content = branch_text_content
            pdf_filename = f'monthly_sales_report_{branch_name.replace(" ", "_")}_{report_data["report_year"]}{report_data["report_month"]:02d}.pdf'
        else:
            # Organization-wide report for admins/owners
            pdf_base64 = org_pdf_base64
            subject = org_subject
            html_content = org_html_content
            text_content = org_text_content
            pdf_filename = f'monthly_sales_report_{report_data["report_year"]}{report_data["report_month"]:02d}.pdf'
        
        data = {
            'Messages': [
                {
                    'From': {
                        'Email': from_email,
                        'Name': company_name
                    },
                    'To': [
                        {
                            'Email': recipient['email'],
                            'Name': recipient['name']
                        }
                    ],
                    'Subject': subject,
                    'TextPart': text_content,
                    'HTMLPart': html_content,
                    'Attachments': [
                        {
                            'ContentType': 'application/pdf',
                            'Filename': pdf_filename,
                            'Base64Content': pdf_base64
                        }
                    ]
                }
            ]
        }
        
        try:
            result = mailjet.send.create(data=data)
            if result.status_code in (200, 201):
                results.append({
                    'email': recipient['email'],
                    'role': recipient['role'],
                    'branch': recipient.get('branch_name'),
                    'success': True
                })
            else:
                results.append({
                    'email': recipient['email'],
                    'role': recipient['role'],
                    'branch': recipient.get('branch_name'),
                    'success': False,
                    'error': f'Status {result.status_code}'
                })
        except Exception as e:
            results.append({
                'email': recipient['email'],
                'role': recipient['role'],
                'branch': recipient.get('branch_name'),
                'success': False,
                'error': str(e)
            })
    
    successful = sum(1 for r in results if r.get('success', False))
    
    return {
        'success': successful > 0,
        'message': f'Sent to {successful} out of {len(recipients)} recipients',
        'recipients_count': len(recipients),
        'successful_count': successful,
        'results': results
    }


def generate_profit_loss_report_pdf(report_data: Dict, branch_name: Optional[str] = None) -> bytes:
    """
    Generate a PDF report from profit and loss report data.
    Returns the PDF as bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#0f766e'),
        spaceAfter=30,
        alignment=1,  # Center alignment
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0f172a'),
        spaceAfter=12,
        spaceBefore=12,
    )
    
    normal_style = styles['Normal']
    
    # Title
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    title_text = f'{company_name} - Profit & Loss Report'
    if branch_name:
        title_text += f' - {branch_name}'
    title = Paragraph(title_text, title_style)
    story.append(title)
    
    # Report Period
    start_date = report_data['start_date']
    end_date = report_data['end_date']
    period_str = f'{start_date.strftime("%B %d, %Y")} to {end_date.strftime("%B %d, %Y")}'
    period_para = Paragraph(f'<b>Report Period:</b> {period_str}', normal_style)
    story.append(period_para)
    story.append(Spacer(1, 0.2*inch))
    
    # Profit & Loss Statement
    story.append(Paragraph('Profit & Loss Statement', heading_style))
    
    revenue = report_data['total_revenue']
    cost_of_goods = report_data['total_cost_of_goods']
    gross_profit = report_data['gross_profit']
    expenses = report_data['total_expenses']
    net_profit = report_data['net_profit']
    
    # Calculate percentages
    gross_margin = (gross_profit / revenue * 100) if revenue > 0 else Decimal('0')
    expense_ratio = (expenses / revenue * 100) if revenue > 0 else Decimal('0')
    net_margin = (net_profit / revenue * 100) if revenue > 0 else Decimal('0')
    
    pl_data = [
        ['Item', 'Amount', '% of Revenue'],
        ['Revenue', f"${revenue:,.2f}", '100.00%'],
        ['Cost of Goods Sold', f"${cost_of_goods:,.2f}", f"{(cost_of_goods / revenue * 100):.2f}%" if revenue > 0 else '0.00%'],
        ['Gross Profit', f"${gross_profit:,.2f}", f"{gross_margin:.2f}%"],
        ['', '', ''],
        ['Operating Expenses', f"${expenses:,.2f}", f"{expense_ratio:.2f}%"],
        ['', '', ''],
        ['Net Profit', f"${net_profit:,.2f}", f"{net_margin:.2f}%"],
    ]
    
    pl_table = Table(pl_data, colWidths=[3.5*inch, 2*inch, 1.5*inch])
    pl_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (2, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ('LINEBELOW', (0, 2), (-1, 2), 2, colors.black),
        ('LINEBELOW', (0, 6), (-1, 6), 2, colors.black),
        ('TEXTCOLOR', (0, 3), (-1, 3), colors.HexColor('#0f766e')),
        ('TEXTCOLOR', (0, 7), (-1, 7), colors.HexColor('#0f766e')),
    ]))
    story.append(pl_table)
    story.append(Spacer(1, 0.3*inch))
    
    # Key Metrics
    story.append(Paragraph('Key Metrics', heading_style))
    
    metrics_data = [
        ['Metric', 'Value'],
        ['Gross Profit Margin', f"{gross_margin:.2f}%"],
        ['Expense Ratio', f"{expense_ratio:.2f}%"],
        ['Net Profit Margin', f"{net_margin:.2f}%"],
        ['Return on Revenue', f"{net_margin:.2f}%"],
    ]
    
    metrics_table = Table(metrics_data, colWidths=[3*inch, 2*inch])
    metrics_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f766e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
    ]))
    story.append(metrics_table)
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def send_profit_loss_report_email(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict:
    """
    Generate and send profit & loss report PDF via email to admin, owner (organization-wide),
    and branch managers (branch-specific).
    Returns a dictionary with success status and details.
    """
    # Default to previous month if dates not provided
    if end_date is None:
        end_date = timezone.now().date()
    if start_date is None:
        # Default to first day of previous month
        if end_date.month == 1:
            start_date = date(end_date.year - 1, 12, 1)
        else:
            start_date = date(end_date.year, end_date.month - 1, 1)
    
    # Email configuration
    api_key = getattr(settings, 'MAILJET_API_KEY', '')
    api_secret = getattr(settings, 'MAILJET_API_SECRET', '')
    
    if not api_key or not api_secret:
        return {
            'success': False,
            'message': 'Mailjet credentials are not configured',
            'recipients_count': 0
        }
    
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@decormasters.com')
    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    period_str = f'{start_date.strftime("%B %d, %Y")} to {end_date.strftime("%B %d, %Y")}'
    
    # Generate organization-wide P&L report for admins/owners
    org_report_data = accounting_services.generate_profit_loss_report(
        start_date=start_date,
        end_date=end_date,
        branch_id=None,
        generated_by=None,
        persist=False,
    )
    
    org_pdf_bytes = generate_profit_loss_report_pdf(org_report_data, branch_name=None)
    org_pdf_base64 = base64.b64encode(org_pdf_bytes).decode('utf-8')
    
    # Prepare email content for organization-wide report
    org_subject = f'Profit & Loss Report - {period_str}'
    org_html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #0f766e;">Profit & Loss Report</h2>
            <p>Please find attached the profit & loss report for <strong>{period_str}</strong>.</p>
            <p><strong>Summary:</strong></p>
            <ul>
                <li>Total Revenue: ${org_report_data['total_revenue']:,.2f}</li>
                <li>Cost of Goods Sold: ${org_report_data['total_cost_of_goods']:,.2f}</li>
                <li>Gross Profit: ${org_report_data['gross_profit']:,.2f}</li>
                <li>Total Expenses: ${org_report_data['total_expenses']:,.2f}</li>
                <li>Net Profit: ${org_report_data['net_profit']:,.2f}</li>
            </ul>
            <p>Best regards,<br>{company_name}</p>
        </div>
    </body>
    </html>
    """
    
    org_text_content = f"""
    Profit & Loss Report
    
    Please find attached the profit & loss report for {period_str}.
    
    Summary:
    - Total Revenue: ${org_report_data['total_revenue']:,.2f}
    - Cost of Goods Sold: ${org_report_data['total_cost_of_goods']:,.2f}
    - Gross Profit: ${org_report_data['gross_profit']:,.2f}
    - Total Expenses: ${org_report_data['total_expenses']:,.2f}
    - Net Profit: ${org_report_data['net_profit']:,.2f}
    
    Best regards,
    {company_name}
    """
    
    # Get recipients
    recipients = []
    
    # Get all admins and owners
    admins = User.objects.filter(role__in=['admin', 'owner'], is_active=True, account_status='active')
    for admin in admins:
        employee = admin.profile.first() if hasattr(admin, 'profile') else None
        recipients.append({
            'email': admin.email,
            'name': employee.get_full_name() if employee and employee.get_full_name() else admin.email,
            'role': admin.role,
            'branch_id': None,  # Organization-wide
        })
    
    # Get all branch managers
    branch_managers = User.objects.filter(
        role='branch_manager',
        is_active=True,
        account_status='active'
    ).prefetch_related('profile')
    
    for manager in branch_managers:
        employee = manager.profile.first() if hasattr(manager, 'profile') else None
        if employee and employee.branch:
            recipients.append({
                'email': manager.email,
                'name': employee.get_full_name() if employee.get_full_name() else manager.email,
                'role': 'branch_manager',
                'branch_id': employee.branch.id,
                'branch_name': employee.branch.name,
            })
    
    if not recipients:
        return {
            'success': False,
            'message': 'No recipients found for profit & loss report',
            'recipients_count': 0
        }
    
    # Send email to each recipient
    mailjet = Client(auth=(api_key, api_secret), version='v3.1')
    results = []
    
    for recipient in recipients:
        # For branch managers, generate branch-specific report
        if recipient['branch_id']:
            branch_name = recipient['branch_name']
            branch_report_data = accounting_services.generate_profit_loss_report(
                start_date=start_date,
                end_date=end_date,
                branch_id=recipient['branch_id'],
                generated_by=None,
                persist=False,
            )
            
            branch_pdf_bytes = generate_profit_loss_report_pdf(branch_report_data, branch_name=branch_name)
            branch_pdf_base64 = base64.b64encode(branch_pdf_bytes).decode('utf-8')
            
            branch_subject = f'Profit & Loss Report - {branch_name} - {period_str}'
            branch_html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #0f766e;">Profit & Loss Report - {branch_name}</h2>
                    <p>Hello {recipient['name']},</p>
                    <p>Please find attached the profit & loss report for <strong>{branch_name}</strong> for {period_str}.</p>
                    <p><strong>Summary:</strong></p>
                    <ul>
                        <li>Total Revenue: ${branch_report_data['total_revenue']:,.2f}</li>
                        <li>Cost of Goods Sold: ${branch_report_data['total_cost_of_goods']:,.2f}</li>
                        <li>Gross Profit: ${branch_report_data['gross_profit']:,.2f}</li>
                        <li>Total Expenses: ${branch_report_data['total_expenses']:,.2f}</li>
                        <li>Net Profit: ${branch_report_data['net_profit']:,.2f}</li>
                    </ul>
                    <p>Best regards,<br>{company_name}</p>
                </div>
            </body>
            </html>
            """
            
            branch_text_content = f"""
            Profit & Loss Report - {branch_name}
            
            Hello {recipient['name']},
            
            Please find attached the profit & loss report for {branch_name} for {period_str}.
            
            Summary:
            - Total Revenue: ${branch_report_data['total_revenue']:,.2f}
            - Cost of Goods Sold: ${branch_report_data['total_cost_of_goods']:,.2f}
            - Gross Profit: ${branch_report_data['gross_profit']:,.2f}
            - Total Expenses: ${branch_report_data['total_expenses']:,.2f}
            - Net Profit: ${branch_report_data['net_profit']:,.2f}
            
            Best regards,
            {company_name}
            """
            
            pdf_base64 = branch_pdf_base64
            subject = branch_subject
            html_content = branch_html_content
            text_content = branch_text_content
            pdf_filename = f'profit_loss_report_{branch_name.replace(" ", "_")}_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.pdf'
        else:
            # Organization-wide report for admins/owners
            pdf_base64 = org_pdf_base64
            subject = org_subject
            html_content = org_html_content
            text_content = org_text_content
            pdf_filename = f'profit_loss_report_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.pdf'
        
        data = {
            'Messages': [
                {
                    'From': {
                        'Email': from_email,
                        'Name': company_name
                    },
                    'To': [
                        {
                            'Email': recipient['email'],
                            'Name': recipient['name']
                        }
                    ],
                    'Subject': subject,
                    'TextPart': text_content,
                    'HTMLPart': html_content,
                    'Attachments': [
                        {
                            'ContentType': 'application/pdf',
                            'Filename': pdf_filename,
                            'Base64Content': pdf_base64
                        }
                    ]
                }
            ]
        }
        
        try:
            result = mailjet.send.create(data=data)
            if result.status_code in (200, 201):
                results.append({
                    'email': recipient['email'],
                    'role': recipient['role'],
                    'branch': recipient.get('branch_name'),
                    'success': True
                })
            else:
                results.append({
                    'email': recipient['email'],
                    'role': recipient['role'],
                    'branch': recipient.get('branch_name'),
                    'success': False,
                    'error': f'Status {result.status_code}'
                })
        except Exception as e:
            results.append({
                'email': recipient['email'],
                'role': recipient['role'],
                'branch': recipient.get('branch_name'),
                'success': False,
                'error': str(e)
            })
    
    successful = sum(1 for r in results if r.get('success', False))
    
    return {
        'success': successful > 0,
        'message': f'Sent to {successful} out of {len(recipients)} recipients',
        'recipients_count': len(recipients),
        'successful_count': successful,
        'results': results
    }


def get_sales_history(
    start_date: date,
    end_date: date,
    branch_id: Optional[int] = None,
    group_by: str = 'day',
    use_stored_reports: bool = True,
) -> Dict:
    """
    Get sales history with aggregations.
    
    Args:
        start_date: Start date for the history range
        end_date: End date for the history range
        branch_id: Optional branch ID to filter by
        group_by: How to group the data ('day', 'month', or 'all')
        use_stored_reports: If True, use DailySalesReport when available, otherwise calculate from Sale records
    
    Returns:
        Dictionary with summary and breakdown data
    """
    # Get completed sales in the date range
    sales_query = Sale.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
        status='completed'
    ).select_related('branch', 'cashier').prefetch_related('items__product')
    
    if branch_id:
        sales_query = sales_query.filter(branch_id=branch_id)
    
    # Overall summary - convert ZIG amounts to USD
    total_sales = sales_query.count()
    total_amount = Decimal('0')
    total_discount = Decimal('0')
    total_tax = Decimal('0')
    total_cost = Decimal('0')
    total_profit = Decimal('0')
    total_items_sold = Decimal('0')
    
    for sale in sales_query:
        payment_method = sale.type_of_payment
        # Convert sale amounts from ZIG to USD if needed
        sale_total = convert_zig_to_usd(sale.total_amount, payment_method)
        sale_discount = convert_zig_to_usd(sale.discount, payment_method)
        sale_tax = convert_zig_to_usd(sale.tax, payment_method)
        
        total_amount += sale_total
        total_discount += sale_discount
        total_tax += sale_tax
        
        for item in sale.items.all():
            # Convert item amounts from ZIG to USD if needed
            item_subtotal = convert_zig_to_usd(item.subtotal, payment_method)
            item_cost = item.purchase_price * item.quantity
            item_cost_usd = convert_zig_to_usd(item_cost, payment_method)
            item_profit = item_subtotal - item_cost_usd
            
            total_cost += item_cost_usd
            total_profit += item_profit
            total_items_sold += item.quantity
    
    summary = {
        'total_sales': total_sales,
        'total_amount': total_amount,
        'total_discount': total_discount,
        'total_tax': total_tax,
        'total_cost': total_cost,
        'total_profit': total_profit,
        'total_items_sold': total_items_sold,
        'profit_margin': (total_profit / total_amount * 100) if total_amount > 0 else Decimal('0'),
        'start_date': start_date,
        'end_date': end_date,
        'branch_id': branch_id,
    }
    
    # Generate breakdown based on group_by
    breakdown = []
    
    if group_by == 'all':
        # No breakdown, just summary
        breakdown = []
    elif group_by == 'day':
        # Group by day
        if use_stored_reports and not branch_id:
            # Try to use stored DailySalesReport first (only when no branch filter)
            reports_query = DailySalesReport.objects.filter(
                report_date__gte=start_date,
                report_date__lte=end_date
            ).order_by('report_date')
            
            breakdown = []
            for report in reports_query:
                breakdown.append({
                    'date': report.report_date.isoformat(),
                    'total_sales': report.total_sales,
                    'total_amount': float(report.total_amount),
                    'total_discount': float(report.total_discount),
                    'total_tax': float(report.total_tax),
                    'total_cost': float(report.total_cost),
                    'total_profit': float(report.total_profit),
                    'profit_margin': float(report.profit_margin),
                    'total_items_sold': float(report.total_items_sold),
                    'from_stored_report': True,
                })
            
            # Fill in any missing dates by calculating from sales
            stored_dates = {date.fromisoformat(item['date']) for item in breakdown}
            all_dates_in_range = set()
            current = start_date
            while current <= end_date:
                all_dates_in_range.add(current)
                current += timedelta(days=1)
            
            missing_dates_set = all_dates_in_range - stored_dates
            if missing_dates_set:
                missing_sales = sales_query.filter(
                    created_at__date__in=list(missing_dates_set)
                )
                missing_breakdown = _get_daily_breakdown_from_sales(missing_sales)
                breakdown.extend(missing_breakdown)
            
            breakdown.sort(key=lambda x: x['date'])
        else:
            # Calculate from sales (when branch filter or use_stored_reports is False)
            breakdown = _get_daily_breakdown_from_sales(sales_query)
    
    elif group_by == 'month':
        # Group by month
        breakdown = []
        sales_annotated = sales_query.annotate(
            period=TruncMonth('created_at')
        ).values('period').annotate(
            total_sales=Count('id'),
            total_amount=Sum('total_amount'),
            total_discount=Sum('discount'),
            total_tax=Sum('tax'),
        ).order_by('period')
        
        for row in sales_annotated:
            period_date = row['period'].date() if hasattr(row['period'], 'date') else row['period']
            
            # Calculate cost and profit for this period
            period_sales = sales_query.filter(
                created_at__year=period_date.year,
                created_at__month=period_date.month
            )
            
            period_cost = Decimal('0')
            period_profit = Decimal('0')
            period_items = Decimal('0')
            period_amount = Decimal('0')
            period_discount = Decimal('0')
            period_tax = Decimal('0')
            
            for sale in period_sales:
                payment_method = sale.type_of_payment
                # Convert sale amounts from ZIG to USD if needed
                sale_total = convert_zig_to_usd(sale.total_amount, payment_method)
                sale_discount = convert_zig_to_usd(sale.discount, payment_method)
                sale_tax = convert_zig_to_usd(sale.tax, payment_method)
                
                period_amount += sale_total
                period_discount += sale_discount
                period_tax += sale_tax
                
                for item in sale.items.all():
                    # Convert item amounts from ZIG to USD if needed
                    item_subtotal = convert_zig_to_usd(item.subtotal, payment_method)
                    item_cost = item.purchase_price * item.quantity
                    item_cost_usd = convert_zig_to_usd(item_cost, payment_method)
                    item_profit = item_subtotal - item_cost_usd
                    
                    period_cost += item_cost_usd
                    period_profit += item_profit
                    period_items += item.quantity
            
            breakdown.append({
                'month': period_date.strftime('%Y-%m'),
                'total_sales': row['total_sales'],
                'total_amount': float(period_amount),
                'total_discount': float(period_discount),
                'total_tax': float(period_tax),
                'total_cost': float(period_cost),
                'total_profit': float(period_profit),
                'total_items_sold': float(period_items),
                'profit_margin': float((period_profit / period_amount * 100) if period_amount > 0 else Decimal('0')),
            })
    
    return {
        'summary': _make_json_serializable(summary),
        'breakdown': breakdown,
    }


def _get_daily_breakdown_from_sales(sales_query) -> List[Dict]:
    """Helper function to calculate daily breakdown from Sale records."""
    breakdown = []
    sales_annotated = sales_query.annotate(
        sale_date=TruncDate('created_at')
    ).values('sale_date').annotate(
        total_sales=Count('id'),
        total_amount=Sum('total_amount'),
        total_discount=Sum('discount'),
        total_tax=Sum('tax'),
    ).order_by('sale_date')
    
    for row in sales_annotated:
        sale_date = row['sale_date']
        
        # Get sales for this date
        date_sales = sales_query.filter(created_at__date=sale_date)
        
        # Calculate cost and profit for this date - convert ZIG amounts to USD
        date_cost = Decimal('0')
        date_profit = Decimal('0')
        date_items = Decimal('0')
        date_amount = Decimal('0')
        date_discount = Decimal('0')
        date_tax = Decimal('0')
        
        for sale in date_sales:
            payment_method = sale.type_of_payment
            # Convert sale amounts from ZIG to USD if needed
            sale_total = convert_zig_to_usd(sale.total_amount, payment_method)
            sale_discount = convert_zig_to_usd(sale.discount, payment_method)
            sale_tax = convert_zig_to_usd(sale.tax, payment_method)
            
            date_amount += sale_total
            date_discount += sale_discount
            date_tax += sale_tax
            
            for item in sale.items.all():
                # Convert item amounts from ZIG to USD if needed
                item_subtotal = convert_zig_to_usd(item.subtotal, payment_method)
                item_cost = item.purchase_price * item.quantity
                item_cost_usd = convert_zig_to_usd(item_cost, payment_method)
                item_profit = item_subtotal - item_cost_usd
                
                date_cost += item_cost_usd
                date_profit += item_profit
                date_items += item.quantity
        
        breakdown.append({
            'date': sale_date.isoformat(),
            'total_sales': row['total_sales'],
            'total_amount': float(date_amount),
            'total_discount': float(date_discount),
            'total_tax': float(date_tax),
            'total_cost': float(date_cost),
            'total_profit': float(date_profit),
            'total_items_sold': float(date_items),
            'profit_margin': float((date_profit / row['total_amount'] * 100) if row['total_amount'] else Decimal('0')),
            'from_stored_report': False,
        })
    
    return breakdown


# ========== Discount Functions ==========

def create_discount(
    name: str,
    discount_type: str,
    discount_value: Decimal,
    apply_to: str = 'all',
    code: Optional[str] = None,
    description: str = '',
    product_id: Optional[int] = None,
    category_id: Optional[int] = None,
    branch_id: Optional[int] = None,
    min_purchase_amount: Optional[Decimal] = None,
    max_discount_amount: Optional[Decimal] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    usage_limit: Optional[int] = None,
    created_by=None,
) -> Discount:
    """Create a new discount."""
    if code and Discount.objects.filter(code=code).exists():
        raise ValueError(f'Discount code "{code}" already exists.')
    
    discount = Discount.objects.create(
        name=name,
        code=code,
        description=description,
        discount_type=discount_type,
        discount_value=discount_value,
        apply_to=apply_to,
        product_id=product_id,
        category_id=category_id,
        branch_id=branch_id,
        min_purchase_amount=min_purchase_amount,
        max_discount_amount=max_discount_amount,
        start_date=start_date,
        end_date=end_date,
        usage_limit=usage_limit,
        created_by=created_by,
    )
    
    return discount


def update_discount(
    discount_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    discount_type: Optional[str] = None,
    discount_value: Optional[Decimal] = None,
    apply_to: Optional[str] = None,
    code: Optional[str] = None,
    product_id: Optional[int] = None,
    category_id: Optional[int] = None,
    branch_id: Optional[int] = None,
    min_purchase_amount: Optional[Decimal] = None,
    max_discount_amount: Optional[Decimal] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    usage_limit: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> Discount:
    """Update an existing discount."""
    discount = Discount.objects.get(pk=discount_id)
    
    if code and code != discount.code:
        if Discount.objects.filter(code=code).exists():
            raise ValueError(f'Discount code "{code}" already exists.')
        discount.code = code
    
    if name is not None:
        discount.name = name
    if description is not None:
        discount.description = description
    if discount_type is not None:
        discount.discount_type = discount_type
    if discount_value is not None:
        discount.discount_value = discount_value
    if apply_to is not None:
        discount.apply_to = apply_to
    if product_id is not None:
        discount.product_id = product_id
    if category_id is not None:
        discount.category_id = category_id
    if branch_id is not None:
        discount.branch_id = branch_id
    if min_purchase_amount is not None:
        discount.min_purchase_amount = min_purchase_amount
    if max_discount_amount is not None:
        discount.max_discount_amount = max_discount_amount
    if start_date is not None:
        discount.start_date = start_date
    if end_date is not None:
        discount.end_date = end_date
    if usage_limit is not None:
        discount.usage_limit = usage_limit
    if is_active is not None:
        discount.is_active = is_active
    
    discount.save()
    return discount


def validate_discount_code(code: str, sale: Sale, items: List[Dict]) -> Tuple[Optional[Discount], str]:
    """Validate and return discount if code is valid for the sale."""
    try:
        discount = Discount.objects.get(code=code)
    except Discount.DoesNotExist:
        return None, 'Discount code not found'
    
    can_apply, message = discount.can_apply_to_sale(sale, items)
    if not can_apply:
        return None, message
    
    return discount, 'Discount is valid'


def apply_discount_to_sale(sale: Sale, discount_code: Optional[str] = None, discount_id: Optional[int] = None) -> Tuple[Decimal, Optional[Discount]]:
    """Apply discount to a sale and return the discount amount."""
    discount = None
    
    if discount_code:
        items_data = [
            {
                'product_id': item.product.id,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'subtotal': item.subtotal,
            }
            for item in sale.items.all()
        ]
        discount, message = validate_discount_code(discount_code, sale, items_data)
        if not discount:
            raise ValueError(message)
    elif discount_id:
        try:
            discount = Discount.objects.get(pk=discount_id)
        except Discount.DoesNotExist:
            raise ValueError('Discount not found')
        
        items_data = [
            {
                'product_id': item.product.id,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'subtotal': item.subtotal,
            }
            for item in sale.items.all()
        ]
        can_apply, message = discount.can_apply_to_sale(sale, items_data)
        if not can_apply:
            raise ValueError(message)
    
    if not discount:
        return Decimal('0'), None
    
    # Calculate total discount amount
    total_discount = Decimal('0')
    
    if discount.apply_to == 'product' and discount.product:
        # Apply to specific product items
        for item in sale.items.filter(product=discount.product):
            item_discount = discount.calculate_discount(item.unit_price, item.quantity)
            item.discount = item.discount + item_discount
            item.save()
            total_discount += item_discount
    elif discount.apply_to == 'category' and discount.category:
        # Apply to category items
        for item in sale.items.filter(product__category=discount.category):
            item_discount = discount.calculate_discount(item.unit_price, item.quantity)
            item.discount = item.discount + item_discount
            item.save()
            total_discount += item_discount
    else:
        # Apply to entire sale
        total_amount = sale.total_amount
        sale_discount = discount.calculate_discount(total_amount)
        sale.discount = sale.discount + sale_discount
        total_discount = sale_discount
    
    sale.save(update_fields=['discount', 'updated_at'])
    _recalculate_sale_totals(sale)
    
    # Increment discount usage
    discount.increment_usage()
    
    return total_discount, discount


def get_available_discounts(branch_id: Optional[int] = None, product_id: Optional[int] = None) -> List[Discount]:
    """
    Get all available (active and valid) discounts.

    Notes:
    - Branch-scoped call should still return global (branch-less) discounts so
      product/category discounts set without a branch are not filtered out.
    - Product filter is applied after branch filtering to keep only relevant
      product/category discounts.
    """
    now = timezone.now()
    discounts = Discount.objects.filter(is_active=True)
    
    if branch_id:
        # Include discounts tied to the branch or global ones (branch=None)
        discounts = discounts.filter(Q(branch_id=branch_id) | Q(branch__isnull=True))
    
    if product_id:
        from products.models import Product
        product = Product.objects.get(pk=product_id)
        discounts = discounts.filter(
            Q(apply_to='all') |
            Q(apply_to='product', product_id=product_id) |
            Q(apply_to='category', category=product.category) |
            Q(apply_to='min_purchase')
        )
    
    # Filter by date range
    discounts = discounts.filter(
        Q(start_date__isnull=True) | Q(start_date__lte=now),
        Q(end_date__isnull=True) | Q(end_date__gte=now),
    )
    
    # Filter by usage limit
    discounts = discounts.filter(
        Q(usage_limit__isnull=True) | Q(usage_count__lt=F('usage_limit'))
    )
    
    return list(discounts.order_by('-created_at'))


def find_eligible_discounts_for_sale(
    branch_id: int,
    items_data: List[Dict],
    sale_total: Optional[Decimal] = None,
) -> List[Tuple[Discount, str]]:
    """
    Find all eligible discounts for a sale based on items, branch, and total.
    Returns list of tuples: (discount, reason_for_eligibility)
    """
    branch = Branch.objects.get(pk=branch_id)
    eligible_discounts = []
    
    # Calculate sale total if not provided
    if sale_total is None:
        sale_total = sum(
            item.get('subtotal', item.get('unit_price', Decimal('0')) * item.get('quantity', Decimal('1')))
            for item in items_data
        )
    
    # Get all active discounts for this branch
    now = timezone.now()
    discounts = Discount.objects.filter(
        is_active=True
    ).filter(
        Q(branch_id=branch_id) | Q(apply_to__in=['all', 'min_purchase']) | Q(branch__isnull=True)
    ).filter(
        Q(start_date__isnull=True) | Q(start_date__lte=now),
        Q(end_date__isnull=True) | Q(end_date__gte=now),
        Q(usage_limit__isnull=True) | Q(usage_count__lt=F('usage_limit'))
    )
    
    # Create temporary sale object for validation
    temp_sale = Sale(branch=branch)
    
    # Check each discount
    for discount in discounts:
        can_apply, message = discount.can_apply_to_sale(temp_sale, items_data)
        if can_apply:
            # Determine reason for eligibility
            if discount.apply_to == 'all':
                reason = 'Applies to all products'
            elif discount.apply_to == 'product':
                reason = f'Applies to product: {discount.product.name}'
            elif discount.apply_to == 'category':
                reason = f'Applies to category: {discount.category.name}'
            elif discount.apply_to == 'branch':
                reason = f'Applies to branch: {discount.branch.name}'
            elif discount.apply_to == 'min_purchase':
                reason = f'Minimum purchase of {discount.min_purchase_amount} met'
            else:
                reason = 'Eligible discount'
            
            eligible_discounts.append((discount, reason))
    
    # Sort by discount value (highest first) for better discounts
    from products.models import Product
    eligible_discounts.sort(
        key=lambda x: (
            x[0].calculate_discount(sale_total) if x[0].apply_to not in ['product', 'category'] 
            else sum(
                x[0].calculate_discount(
                    item.get('unit_price', Decimal('0')),
                    item.get('quantity', Decimal('1'))
                )
                for item in items_data
                if (x[0].apply_to == 'product' and item.get('product_id') == x[0].product_id) or
                   (x[0].apply_to == 'category' and Product.objects.filter(
                       id=item.get('product_id'), 
                       category=x[0].category
                   ).exists())
            )
        ),
        reverse=True
    )
    
    return eligible_discounts


def create_or_update_cash_received(
    cashier_id: int,
    branch_id: int,
    date: date,
    total_amount: Decimal,
    entered_by: User,
    type_of_payment: str = 'usd_cash',
    notes: str = ''
) -> CashReceived:
    """
    Create or update a cash received entry for a cashier on a specific date.
    Supports multiple entries per day - one entry per payment type (USD Cash, ZIG Cash, Ecocash, Bank Transfer, etc.).
    If an entry already exists for the cashier, branch, date, and payment type, it will be updated.
    
    Args:
        cashier_id: ID of the cashier
        branch_id: ID of the branch
        date: Date when cash was received
        total_amount: Total amount of cash received
        entered_by: User (manager) who is entering this record
        type_of_payment: Type of payment/currency (default: 'usd_cash')
                        Options: 'usd_cash', 'zig_cash', 'ecocash_usd', 'ecocash_zig', 
                                'bank_transfer_usd', 'bank_transfer_zig'
        notes: Optional notes about the cash received
    
    Returns:
        CashReceived instance
    """
    from django.db import IntegrityError
    from django.core.exceptions import ValidationError
    
    cashier = User.objects.get(pk=cashier_id)
    branch = Branch.objects.get(pk=branch_id)
    
    # Verify cashier belongs to branch
    profile = getattr(cashier, 'profile', None)
    if profile:
        employee = profile.first()
        if employee and employee.branch != branch:
            raise ValueError('Cashier does not belong to the specified branch.')
    
    # Validate type_of_payment
    valid_payment_types = [choice[0] for choice in Sale.TYPE_OF_PAYMENT_CHOICES]
    if type_of_payment not in valid_payment_types:
        raise ValueError(f'Invalid type_of_payment. Must be one of: {", ".join(valid_payment_types)}')
    
    # Use get_or_create with transaction to handle unique constraint properly
    # This prevents duplicate key errors from concurrent requests
    try:
        with transaction.atomic():
            # Use get_or_create which handles the unique constraint automatically
            # It returns (object, created) tuple where created is True if object was created
            cash_received, created = CashReceived.objects.get_or_create(
                cashier=cashier,
                branch=branch,
                date=date,
                type_of_payment=type_of_payment,
                defaults={
                    'total_amount': total_amount,
                    'entered_by': entered_by,
                    'notes': notes,
                }
            )
            
            # If record already existed, update it with new values
            if not created:
                cash_received.total_amount = total_amount
                cash_received.entered_by = entered_by
                cash_received.notes = notes
                cash_received.save(update_fields=['total_amount', 'entered_by', 'notes'])
            
            return cash_received
    except (IntegrityError, ValidationError):
        # Handle edge case: if get_or_create fails due to concurrent creation,
        # fetch the existing record and update it
        cash_received = CashReceived.objects.get(
            cashier=cashier,
            branch=branch,
            date=date,
            type_of_payment=type_of_payment
        )
        cash_received.total_amount = total_amount
        cash_received.entered_by = entered_by
        cash_received.notes = notes
        cash_received.save(update_fields=['total_amount', 'entered_by', 'notes'])
        return cash_received


def calculate_cash_variance(
    cashier_id: Optional[int] = None,
    branch_id: Optional[int] = None,
    date: Optional[date] = None
) -> Dict:
    """
    Calculate variance between total sales and cash received for a cashier.
    Supports multiple cash received entries per day (one per payment type).
    
    Args:
        cashier_id: Optional cashier ID to filter by
        branch_id: Optional branch ID to filter by
        date: Optional date to filter by (defaults to today)
    
    Returns:
        Dictionary with:
        - cashier_id: Cashier ID
        - cashier_email: Cashier email
        - cashier_name: Cashier full name
        - branch_id: Branch ID
        - branch_name: Branch name
        - date: Date of the calculation
        - total_sales_amount_usd: Total amount from completed sales (in USD)
        - total_discount_usd: Total discount (in USD)
        - total_tax_usd: Total tax (in USD)
        - net_sales_amount_usd: Net sales amount after discount and tax (in USD)
        - cash_received_amount_usd: Total cash received (converted to USD)
        - variance_usd: Difference (cash_received - total_sales) in USD
        - variance_percentage: Variance as percentage of sales
        - sales_count: Number of completed sales
        - has_cash_received_entries: Boolean indicating if any entries exist
        - cash_received_entries_count: Number of cash received entries
        - cash_received_by_type: Dictionary with amounts grouped by payment type
        - cash_received_entries: List of all cash received entries with details
    """
    from accounts.models import User
    from organization.models import Branch
    
    if date is None:
        date = timezone.now().date()
    
    # Build sales query - include all payment types (cash, ecocash, bank transfer)
    # Filter for cash-related payment methods
    cash_payment_methods = ['usd_cash', 'zig_cash', 'ecocash_usd', 'ecocash_zig', 
                            'bank_transfer_usd', 'bank_transfer_zig']
    sales_query = Sale.objects.filter(
        created_at__date=date,
        status='completed',
        type_of_payment__in=cash_payment_methods
    )
    
    if cashier_id:
        sales_query = sales_query.filter(cashier_id=cashier_id)
    if branch_id:
        sales_query = sales_query.filter(branch_id=branch_id)
    
    # Calculate total sales amount - convert all to USD for comparison
    total_sales_amount_usd = Decimal('0')
    total_discount_usd = Decimal('0')
    total_tax_usd = Decimal('0')
    sales_count = 0
    
    for sale in sales_query:
        # Convert sale amounts to USD
        sale_amount_usd = convert_zig_to_usd(sale.total_amount, sale.type_of_payment)
        discount_usd = convert_zig_to_usd(sale.discount, sale.type_of_payment)
        tax_usd = convert_zig_to_usd(sale.tax, sale.type_of_payment)
        
        total_sales_amount_usd += sale_amount_usd
        total_discount_usd += discount_usd
        total_tax_usd += tax_usd
        sales_count += 1
    
    # Net sales amount (after discount and tax) in USD
    net_sales_amount_usd = total_sales_amount_usd - total_discount_usd + total_tax_usd
    
    # Get all cash received entries for the day (can be multiple - one per payment type)
    cash_received_query = CashReceived.objects.filter(date=date)
    
    if cashier_id:
        cash_received_query = cash_received_query.filter(cashier_id=cashier_id)
    if branch_id:
        cash_received_query = cash_received_query.filter(branch_id=branch_id)
    
    cash_received_entries = list(cash_received_query.all())
    
    # Aggregate all cash received amounts and convert to USD for comparison
    cash_received_amount_usd = Decimal('0')
    cash_received_by_type = {}
    
    for entry in cash_received_entries:
        entry_amount_usd = convert_zig_to_usd(entry.total_amount, entry.type_of_payment)
        cash_received_amount_usd += entry_amount_usd
        
        # Track amounts by payment type
        if entry.type_of_payment not in cash_received_by_type:
            cash_received_by_type[entry.type_of_payment] = {
                'amount': Decimal('0'),
                'amount_usd': Decimal('0'),
                'entries': []
            }
        cash_received_by_type[entry.type_of_payment]['amount'] += entry.total_amount
        cash_received_by_type[entry.type_of_payment]['amount_usd'] += entry_amount_usd
        cash_received_by_type[entry.type_of_payment]['entries'].append({
            'id': entry.id,
            'total_amount': entry.total_amount,
            'type_of_payment': entry.type_of_payment,
            'type_of_payment_display': entry.get_type_of_payment_display(),
            'entered_by': entry.entered_by.email if entry.entered_by else None,
            'notes': entry.notes,
            'created_at': entry.created_at,
        })
    
    # Calculate variance (in USD)
    variance = cash_received_amount_usd - net_sales_amount_usd
    variance_percentage = (variance / net_sales_amount_usd * 100) if net_sales_amount_usd > 0 else Decimal('0')
    
    # Get cashier and branch info
    cashier_info = {}
    branch_info = {}
    
    if cashier_id:
        cashier = User.objects.get(pk=cashier_id)
        cashier_info = {
            'cashier_id': cashier.id,
            'cashier_email': cashier.email,
        }
        # Get cashier name from employee profile
        profile = getattr(cashier, 'profile', None)
        if profile:
            employee = profile.first()
            if employee:
                cashier_info['cashier_name'] = f"{employee.first_name} {employee.last_name}".strip()
    
    if branch_id:
        branch = Branch.objects.get(pk=branch_id)
        branch_info = {
            'branch_id': branch.id,
            'branch_name': branch.name,
        }
    elif cash_received_entries:
        # Use the first entry's branch if branch_id not provided
        first_entry = cash_received_entries[0]
        branch_info = {
            'branch_id': first_entry.branch.id,
            'branch_name': first_entry.branch.name,
        }
    
    result = {
        **cashier_info,
        **branch_info,
        'date': date,
        'total_sales_amount_usd': total_sales_amount_usd,
        'total_discount_usd': total_discount_usd,
        'total_tax_usd': total_tax_usd,
        'net_sales_amount_usd': net_sales_amount_usd,
        'cash_received_amount_usd': cash_received_amount_usd,
        'variance_usd': variance,
        'variance_percentage': variance_percentage,
        'sales_count': sales_count,
        'has_cash_received_entries': len(cash_received_entries) > 0,
        'cash_received_entries_count': len(cash_received_entries),
        'cash_received_by_type': cash_received_by_type,
    }
    
    # Include all cash received entries
    if cash_received_entries:
        result['cash_received_entries'] = [
            {
                'id': entry.id,
                'total_amount': entry.total_amount,
                'type_of_payment': entry.type_of_payment,
                'type_of_payment_display': entry.get_type_of_payment_display(),
                'entered_by': entry.entered_by.email if entry.entered_by else None,
                'notes': entry.notes,
                'created_at': entry.created_at,
            }
            for entry in cash_received_entries
        ]
    
    return result


# ========== Exchange Rate Helpers ==========

def get_current_exchange_rate() -> Optional[ExchangeRate]:
    """Return the single current exchange rate instance if it exists."""
    return ExchangeRate.objects.first()


def is_zig_payment_method(payment_method: str) -> bool:
    """Check if the payment method is in ZIG currency."""
    zig_payment_methods = ['zig_cash', 'ecocash_zig', 'bank_transfer_zig']
    return payment_method in zig_payment_methods


def convert_zig_to_usd(amount: Decimal, payment_method: str) -> Decimal:
    """
    Convert ZIG amount to USD if payment method is in ZIG.
    Otherwise, return the amount as-is.
    
    Args:
        amount: The amount to convert
        payment_method: The payment method from the sale
    
    Returns:
        Amount in USD (converted if ZIG, original if USD)
    """
    if not is_zig_payment_method(payment_method):
        return amount
    
    exchange_rate = get_current_exchange_rate()
    if not exchange_rate or not exchange_rate.current_rate:
        # If no exchange rate is set, return original amount
        # In production, you might want to raise an error here
        return amount
    
    # Convert ZIG to USD by multiplying by current_rate
    return amount / exchange_rate.current_rate


def set_exchange_rate(current_rate: Decimal) -> ExchangeRate:
    """
    Create or update the current exchange rate.
    Ensures only one record is kept.
    
    Args:
        current_rate: The exchange rate value (must be greater than zero)
    
    Returns:
        ExchangeRate instance
    
    Raises:
        ValueError: If current_rate is not positive
    """
    if current_rate <= 0:
        raise ValueError('Exchange rate must be greater than zero.')
    
    with transaction.atomic():
        exchange_rate = ExchangeRate.objects.select_for_update().first()
        if exchange_rate:
            exchange_rate.current_rate = current_rate
            exchange_rate.save(update_fields=['current_rate', 'updated_at'] if hasattr(exchange_rate, 'updated_at') else ['current_rate'])
        else:
            exchange_rate = ExchangeRate.objects.create(current_rate=current_rate)
    return exchange_rate

