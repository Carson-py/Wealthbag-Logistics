from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from organization.models import Branch
from sales.models import ReturnAuthorizationCode
from sales import services as sales_services


class Command(BaseCommand):
    help = 'Rotate branch return-authorization codes (invalidate old ones and issue new codes).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--branch-id',
            type=int,
            help='Rotate codes for a specific branch only (default: all branches).'
        )
        parser.add_argument(
            '--expires-in-minutes',
            type=int,
            default=30,
            help='Lifetime of the new codes in minutes (1-1440, default: 30).'
        )
        parser.add_argument(
            '--notify',
            action='store_true',
            help='Send the new code to branch managers via email.'
        )

    def handle(self, *args, **options):
        branch_id = options.get('branch_id')
        expires_in_minutes = options.get('expires_in_minutes') or 30
        notify = options.get('notify', True)

        if expires_in_minutes < 1 or expires_in_minutes > 1440:
            raise CommandError('expires-in-minutes must be between 1 and 1440.')

        queryset = Branch.objects.all()
        if branch_id:
            queryset = queryset.filter(pk=branch_id)
            if not queryset.exists():
                raise CommandError(f'Branch with id {branch_id} does not exist.')

        now = timezone.now()
        expired_count = ReturnAuthorizationCode.objects.filter(
            is_active=True,
            expires_at__lt=now
        ).update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f'Deactivated {expired_count} expired codes.'))

        rotated = 0
        notified = 0
        for branch in queryset:
            auth_code = sales_services.generate_return_authorization_code(
                branch_id=branch.id,
                expires_in_minutes=expires_in_minutes,
                created_by=None,
            )
            rotated += 1
            if notify:
                result = sales_services.notify_branch_managers_of_return_code(auth_code)
                if result.get('success'):
                    notified += 1

        self.stdout.write(self.style.SUCCESS(f'Generated new codes for {rotated} branch(es).'))
        if notify:
            self.stdout.write(self.style.SUCCESS(f'Notifications sent for {notified} branch(es).'))

