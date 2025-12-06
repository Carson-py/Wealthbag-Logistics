from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from datetime import date, timedelta
import pytz
from sales import services


class Command(BaseCommand):
    help = 'Send daily sales report via email to admin, owner, and branch managers'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Date for the report (YYYY-MM-DD). Defaults to today.',
        )

    def handle(self, *args, **options):
        report_date = None
        tz_name = 'UTC'  # Default
        
        if options.get('date'):
            try:
                report_date = date.fromisoformat(options['date'])
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(f'Invalid date format: {options["date"]}. Use YYYY-MM-DD format.')
                )
                return
        else:
            # Get current date in company timezone to ensure accuracy
            tz_name = getattr(settings, 'COMPANY_TIME_ZONE', getattr(settings, 'TIME_ZONE', 'UTC'))
            try:
                company_tz = pytz.timezone(tz_name)
            except pytz.UnknownTimeZoneError:
                company_tz = timezone.get_current_timezone()
            
            # Get current datetime in company timezone
            now_utc = timezone.now()
            now_local = now_utc.astimezone(company_tz)
            
            # For end-of-day reports, use the current date in company timezone
            # This ensures we report on the day that's ending, not a previous day
            report_date = now_local.date()
            
            # If running very early morning (before 2 AM), use previous day
            # This handles cases where the report runs just after midnight
            if now_local.hour < 2:
                # Running early morning, send report for previous day
                report_date = now_local.date() - timedelta(days=1)
        
        self.stdout.write(f'Generating daily sales report for {report_date} (company timezone: {tz_name})...')
        
        try:
            result = services.send_daily_sales_report_email(report_date=report_date)
            
            if result['success']:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Successfully sent daily sales report to {result["successful_count"]} out of '
                        f'{result["recipients_count"]} recipients'
                    )
                )
                
                # Show detailed results
                for res in result['results']:
                    if res.get('success'):
                        self.stdout.write(
                            self.style.SUCCESS(f'  ✓ {res["email"]} ({res["role"]})')
                        )
                    else:
                        self.stdout.write(
                            self.style.ERROR(
                                f'  ✗ {res["email"]} ({res["role"]}): {res.get("error", "Unknown error")}'
                            )
                        )
            else:
                self.stdout.write(
                    self.style.ERROR(f'Failed to send daily sales report: {result.get("message", "Unknown error")}')
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error sending daily sales report: {str(e)}')
            )

