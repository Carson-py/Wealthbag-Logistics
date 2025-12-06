from django.db import models
from django.conf import settings

from organization.models import Branch, Warehouse


class ExpenseCategory(models.Model):
    """Categories for grouping expenses."""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Expense(models.Model):
    """Operational expense recorded against branch/warehouse."""
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    incurred_on = models.DateField()
    attachment = models.FileField(upload_to='expenses/', null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_expenses')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-incurred_on', '-created_at']
        indexes = [
            models.Index(fields=['branch', 'incurred_on']),
            models.Index(fields=['warehouse', 'incurred_on']),
        ]

    def __str__(self):
        return f"{self.amount} on {self.incurred_on}"


class ProfitLossReport(models.Model):
    """Cached profit/loss report snapshots."""
    start_date = models.DateField()
    end_date = models.DateField()
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='profit_loss_reports')
    total_revenue = models.DecimalField(max_digits=14, decimal_places=2)
    total_cost_of_goods = models.DecimalField(max_digits=14, decimal_places=2)
    total_expenses = models.DecimalField(max_digits=14, decimal_places=2)
    net_profit = models.DecimalField(max_digits=14, decimal_places=2)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='profit_loss_reports')
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['start_date', 'end_date']),
        ]

    def __str__(self):
        scope = self.branch.name if self.branch else 'All Branches'
        return f"PL {scope}: {self.start_date} - {self.end_date}"
