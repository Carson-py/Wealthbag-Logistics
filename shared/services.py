from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from mailjet_rest import Client
import pytz

User = get_user_model()


def _send_mailjet_email(subject, from_email, from_name, to_email, to_name, text_content, html_content):
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
                    'Name': from_name,
                },
                'To': [
                    {
                        'Email': to_email,
                        'Name': to_name,
                    }
                ],
                'Subject': subject,
                'TextPart': text_content,
                'HTMLPart': html_content,
            }
        ]
    }

    result = mailjet.send.create(data=data)
    if result.status_code not in (200, 201):
        raise RuntimeError(f'Mailjet email failed with status {result.status_code}: {result.json()}')


def _recipient_display_name(user):
    employee = user.profile.first() if hasattr(user, 'profile') else None
    if employee:
        full_name = employee.get_full_name().strip()
        if full_name:
            return full_name
    return user.email


def send_system_heartbeat_emails():
    """Notify superusers that the system is up and running."""
    tz_name = getattr(settings, 'COMPANY_TIME_ZONE', getattr(settings, 'TIME_ZONE', 'UTC'))
    now = timezone.now()
    try:
        now_local = now.astimezone(pytz.timezone(tz_name))
    except pytz.UnknownTimeZoneError:
        now_local = timezone.localtime(now)

    company_name = getattr(settings, 'COMPANY_NAME', 'Decor Masters')
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@decormasters.com')
    logo_url = getattr(settings, 'COMPANY_LOGO_URL')
    support_email = getattr(settings, 'SUPPORT_EMAIL', 'support@carsontechnologies.co.zw')

    superusers = (
        User.objects.filter(is_superuser=True, is_active=True)
        .exclude(email='')
        .prefetch_related('profile')
    )

    results = []
    for user in superusers:
        display_name = _recipient_display_name(user)
        context = {
            'user': user,
            'full_name': display_name,
            'company_name': company_name,
            'company_logo_url': logo_url,
            'support_email': support_email,
            'checked_at': now_local.strftime('%Y-%m-%d %H:%M %Z'),
        }
        html_content = render_to_string('shared/emails/system_heartbeat.html', context)
        text_content = strip_tags(html_content)
        try:
            _send_mailjet_email(
                subject=f'{company_name} system status: running',
                from_email=from_email,
                from_name=company_name,
                to_email=user.email,
                to_name=display_name,
                text_content=text_content,
                html_content=html_content,
            )
            results.append({'email': user.email, 'success': True})
        except Exception as exc:
            results.append({'email': user.email, 'success': False, 'error': str(exc)})

    return {
        'success': any(r['success'] for r in results) if results else False,
        'recipients_count': len(results),
        'successful_count': sum(1 for r in results if r['success']),
        'results': results,
    }
