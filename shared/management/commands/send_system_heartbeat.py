from django.core.management.base import BaseCommand

from shared.services import send_system_heartbeat_emails


class Command(BaseCommand):
    help = 'Email superusers that the system is up and running (Mailjet).'

    def handle(self, *args, **options):
        self.stdout.write('Sending system heartbeat emails to superusers...')
        result = send_system_heartbeat_emails()

        if not result['recipients_count']:
            self.stdout.write(self.style.WARNING('No active superusers with an email address.'))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Sent {result['successful_count']} of {result['recipients_count']} heartbeat emails"
        ))
        for entry in result['results']:
            if entry.get('success'):
                self.stdout.write(self.style.SUCCESS(f"  ✓ {entry['email']}"))
            else:
                self.stdout.write(self.style.ERROR(
                    f"  ✗ {entry['email']}: {entry.get('error', 'Unknown error')}"
                ))
