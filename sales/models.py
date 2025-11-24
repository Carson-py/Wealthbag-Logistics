from django.db import models
from django.utils import timezone
from accounts.models import User
from products.models import Product
from organization.models import Branch, Warehouse


class Sale(models.Model):
    """Sales transactions"""
    STATUS_CHOICES = [
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]
    
    sale_number = models.CharField(max_length=50, unique=True, verbose_name='Sale Number')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='sales')
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='sales')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['branch', 'created_at']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Sale {self.sale_number} - {self.branch.name}"
    
    @property
    def net_amount(self):
        """Calculate net amount after discount and tax"""
        return self.total_amount - self.discount + self.tax


class SaleItem(models.Model):
    """Individual items in a sale"""
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='sale_items')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Selling Price Per Unit')
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, 
                                         verbose_name='Purchase Price Per Unit (at time of sale)')
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    
    class Meta:
        indexes = [
            models.Index(fields=['sale', 'product']),
            models.Index(fields=['product', 'sale']),
        ]
    
    def __str__(self):
        return f"{self.product.name} x {self.quantity} in {self.sale.sale_number}"
    
    def save(self, *args, **kwargs):
        """Calculate subtotal before saving"""
        self.subtotal = (self.unit_price * self.quantity) - self.discount
        super().save(*args, **kwargs)
    
    @property
    def cost(self):
        """Calculate total cost for this sale item"""
        return self.quantity * self.purchase_price
    
    @property
    def profit(self):
        """Calculate profit for this sale item"""
        return self.subtotal - self.cost
    
    @property
    def profit_margin(self):
        """Calculate profit margin percentage"""
        if self.subtotal > 0:
            return (self.profit / self.subtotal) * 100
        return 0


class ProductReturn(models.Model):
    """Track product returns"""
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='returns')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='returns')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(blank=True)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2)
    processed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='processed_returns')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Return: {self.product.name} x {self.quantity} from {self.sale.sale_number}"
