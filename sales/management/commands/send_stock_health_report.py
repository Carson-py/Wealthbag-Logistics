from django.core.management.base import BaseCommand

from sales import services


class Command(BaseCommand):
    help = 'Send stock health report emails to admins (organization-wide) and branch managers (branch-scoped).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fast-days',
            type=int,
            default=30,
            help='Number of days to evaluate fast-moving products (default: 30).',
        )
        parser.add_argument(
            '--slow-period',
            type=str,
            default='30d',
            help='Slow stock analysis window (default: 30d).',
        )
        parser.add_argument(
            '--dead-period',
            type=str,
            default='90d',
            help='Dead stock analysis window (default: 90d).',
        )
        parser.add_argument(
            '--item-limit',
            type=int,
            default=10,
            help='Maximum number of products to include per section (default: 10).',
        )

    def handle(self, *args, **options):
        result = services.send_stock_health_report_email(
            fast_days=options['fast_days'],
            slow_period=options['slow_period'],
            dead_period=options['dead_period'],
            item_limit=options['item_limit'],
        )

        if result.get('success'):
            self.stdout.write(self.style.SUCCESS(
                f"Sent stock health report emails (admins: {result.get('admin_sent', 0)}, "
                f"branches: {result.get('branch_sent', 0)})"
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f"Failed to send stock health report emails: {result.get('message', 'Unknown error')}"
            ))

        for entry in result.get('results', []):
            status = self.style.SUCCESS('✓') if entry.get('success') else self.style.ERROR('✗')
            details = f"{entry.get('email')} ({entry.get('role')})"
            if entry.get('branch'):
                details += f" - {entry['branch']}"
            if entry.get('error'):
                details += f": {entry['error']}"
            self.stdout.write(f"{status} {details}")

