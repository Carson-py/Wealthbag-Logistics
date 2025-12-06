from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import date
from sales import services


class Command(BaseCommand):
    help = 'Send profit & loss report via email to admin, owner (organization-wide), and branch managers (branch-specific).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for the report (YYYY-MM-DD). Defaults to first day of previous month.',
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date for the report (YYYY-MM-DD). Defaults to last day of previous month.',
        )

    def handle(self, *args, **options):
        start_date = None
        end_date = None
        
        # Parse start date if provided
        if options.get('start_date'):
            try:
                start_date = date.fromisoformat(options['start_date'])
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(f'Invalid start date format: {options["start_date"]}. Use YYYY-MM-DD format.')
                )
                return
        
        # Parse end date if provided
        if options.get('end_date'):
            try:
                end_date = date.fromisoformat(options['end_date'])
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(f'Invalid end date format: {options["end_date"]}. Use YYYY-MM-DD format.')
                )
                return
        
        # Validate date range if both provided
        if start_date and end_date and start_date > end_date:
            self.stdout.write(
                self.style.ERROR('Start date cannot be after end date.')
            )
            return
        
        # Determine dates if not provided
        if end_date is None:
            end_date = timezone.now().date()
        if start_date is None:
            # Default to first day of previous month
            if end_date.month == 1:
                start_date = date(end_date.year - 1, 12, 1)
            else:
                start_date = date(end_date.year, end_date.month - 1, 1)
        
        period_str = f'{start_date.strftime("%B %d, %Y")} to {end_date.strftime("%B %d, %Y")}'
        self.stdout.write(f'Generating profit & loss report for {period_str}...')
        
        try:
            result = services.send_profit_loss_report_email(
                start_date=start_date,
                end_date=end_date
            )
            
            if result.get('success'):
                self.stdout.write(self.style.SUCCESS(
                    f"Sent profit & loss report to {result.get('successful_count', 0)} out of "
                    f"{result.get('recipients_count', 0)} recipients"
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    f"Failed to send profit & loss report: {result.get('message', 'Unknown error')}"
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
            self.stdout.write(self.style.ERROR(f'Error sending profit & loss report: {str(e)}'))

