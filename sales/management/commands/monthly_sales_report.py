from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import date
from sales import services


class Command(BaseCommand):
    help = 'Send monthly sales report via email to admin, owner (organization-wide), and branch managers (branch-specific).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--month',
            type=int,
            help='Month for the report (1-12). Defaults to previous month.',
        )
        parser.add_argument(
            '--year',
            type=int,
            help='Year for the report (YYYY). Defaults to current year or previous year if month is December.',
        )

    def handle(self, *args, **options):
        report_month = options.get('month')
        report_year = options.get('year')
        
        # Validate month if provided
        if report_month is not None:
            if report_month < 1 or report_month > 12:
                self.stdout.write(
                    self.style.ERROR(f'Invalid month: {report_month}. Month must be between 1 and 12.')
                )
                return
        
        # Validate year if provided
        if report_year is not None:
            current_year = timezone.now().year
            if report_year < 2000 or report_year > current_year + 1:
                self.stdout.write(
                    self.style.ERROR(f'Invalid year: {report_year}. Year must be between 2000 and {current_year + 1}.')
                )
                return
        
        # Determine month and year
        now = timezone.now()
        if report_month is None:
            # Default to previous month
            if now.month == 1:
                report_month = 12
                if report_year is None:
                    report_year = now.year - 1
            else:
                report_month = now.month - 1
                if report_year is None:
                    report_year = now.year
        
        if report_year is None:
            report_year = now.year
        
        month_name = date(report_year, report_month, 1).strftime('%B')
        self.stdout.write(f'Generating monthly sales report for {month_name} {report_year}...')
        
        try:
            result = services.send_monthly_sales_report_email(
                report_month=report_month,
                report_year=report_year
            )
            
            if result.get('success'):
                self.stdout.write(self.style.SUCCESS(
                    f"Sent monthly sales report to {result.get('successful_count', 0)} out of "
                    f"{result.get('recipients_count', 0)} recipients"
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    f"Failed to send monthly sales report: {result.get('message', 'Unknown error')}"
                ))
            
            # Display detailed results
            for entry in result.get('results', []):
                status = self.style.SUCCESS('✓') if entry.get('success') else self.style.ERROR('✗')
                details = f"{entry.get('email')} ({entry.get('role')})"
                if entry.get('branch'):
                    details += f" - Branch: {entry['branch']}"
                if entry.get('error'):
                    details += f": {entry['error']}"
                self.stdout.write(f"{status} {details}")
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error sending monthly sales report: {str(e)}'))

