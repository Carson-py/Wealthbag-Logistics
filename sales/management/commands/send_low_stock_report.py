from django.core.management.base import BaseCommand

from sales import services


class Command(BaseCommand):
    help = 'Send low stock alert emails to warehouse managers, branch managers, and admins (for main warehouse).'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Sending low stock alert emails...'))
        
        try:
            result = services.send_low_stock_report_email()

            warehouse_sent = result.get('warehouse_sent', 0)
            branch_sent = result.get('branch_sent', 0)
            admin_sent = result.get('admin_sent', 0)
            any_sent = result.get('success') or (warehouse_sent + branch_sent + admin_sent) > 0

            if any_sent:
                self.stdout.write(self.style.SUCCESS(
                    f"Sent low stock alert emails (warehouses: {warehouse_sent}, "
                    f"branches: {branch_sent}, admins: {admin_sent})"
                ))
            else:
                # If nothing was sent but there is no explicit error message, it's usually
                # because there were no low-stock items or no matching recipients.
                message = result.get('message')
                if message:
                    self.stdout.write(self.style.ERROR(
                        f"Failed to send low stock alert emails: {message}"
                    ))
                else:
                    self.stdout.write(self.style.WARNING(
                        "No low stock alert emails were sent (no low-stock items or no eligible recipients)."
                    ))
            
            # Display detailed results
            for entry in result.get('results', []):
                status = self.style.SUCCESS('✓') if entry.get('success') else self.style.ERROR('✗')
                details = f"{entry.get('email')} ({entry.get('role')})"
                if entry.get('warehouse'):
                    details += f" - Warehouse: {entry['warehouse']}"
                if entry.get('branch'):
                    details += f" - Branch: {entry['branch']}"
                if entry.get('error'):
                    details += f": {entry['error']}"
                self.stdout.write(f"{status} {details}")
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error sending low stock alert emails: {str(e)}'))

