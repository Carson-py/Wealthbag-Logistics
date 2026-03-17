from django.db import models
from django.utils import timezone
from decimal import Decimal
from accounts.models import User
from products.models import Product
from organization.models import Branch
from sales.models import Sale

class Layby(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('overdue', 'Overdue'),
    ]

    layby_number = models.CharField(max_length=50, unique=True, verbose_name='Layby Number')
    customer_name = models.CharField(max_length=255)
    customer_phone = models.CharField(max_length=20)
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='laybys')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='laybys')
    
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    due_date = models.DateField()
    
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Layby'
        verbose_name_plural = 'Laybys'

    def __str__(self):
        return f"Layby {self.layby_number} - {self.customer_name}"

    def save(self, *args, **kwargs):
        if not self.layby_number:
            self.layby_number = self._generate_layby_number()
        
        # Balance is always total - total_paid
        # We handle this in services after payments are made
        super().save(*args, **kwargs)

    def _generate_layby_number(self):
        import uuid
        return f"LB-{timezone.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"

    @property
    def total_paid(self):
        return self.payments.aggregate(total=models.Sum('amount'))['total'] or Decimal('0')

class LaybyItem(models.Model):
    layby = models.ForeignKey(Layby, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='layby_items')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)

    def save(self, *args, **kwargs):
        self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} x {self.quantity} for {self.layby.layby_number}"

class LaybyPayment(models.Model):
    PAYMENT_METHOD_CHOICES = Sale.TYPE_OF_PAYMENT_CHOICES

    layby = models.ForeignKey(Layby, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES)
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='layby_payments')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment of {self.amount} for {self.layby.layby_number}"
