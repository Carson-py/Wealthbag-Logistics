from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal
from typing import List, Dict
from accounts.models import User
from products.models import Product, Category
from organization.models import Branch, Warehouse


class Sale(models.Model):
    """Sales transactions"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]

    TYPE_OF_PAYMENT_CHOICES = [
        ('cash', 'Cash'),
        ('ecocash', 'Ecocash'),
        ('one_money', 'One Money'),
        ('bank_transfer', 'Bank Transfer')
    ]
    
    sync_id = models.CharField(max_length=50, unique=True, verbose_name='Sync ID', null=True, blank=True)
    sale_number = models.CharField(max_length=50, unique=True, verbose_name='Sale Number')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='sales')
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='sales')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    type_of_payment = models.CharField(max_length=20, choices=TYPE_OF_PAYMENT_CHOICES, default='cash')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['branch', 'created_at']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['created_at']),
            models.Index(fields=['sync_id']),
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
    created_at = models.DateField(auto_now_add=True)
    
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


class DailySalesReport(models.Model):
    """Store daily sales report data for historical tracking"""
    report_date = models.DateField(unique=True, verbose_name='Report Date', db_index=True)
    
    # Overall summary
    total_sales = models.IntegerField(default=0, verbose_name='Total Sales Count')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total Revenue')
    total_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total Discount')
    total_tax = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total Tax')
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total Cost')
    total_profit = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total Profit')
    total_items_sold = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total Items Sold')
    profit_margin = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name='Profit Margin (%)')
    
    # JSON field to store branch summaries and payment methods breakdown
    branch_summaries = models.JSONField(default=list, verbose_name='Branch Summaries')
    payment_methods = models.JSONField(default=dict, verbose_name='Payment Methods Breakdown')
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Updated At')
    
    class Meta:
        ordering = ['-report_date']
        verbose_name = 'Daily Sales Report'
        verbose_name_plural = 'Daily Sales Reports'
        indexes = [
            models.Index(fields=['report_date']),
            models.Index(fields=['-report_date']),
        ]
    
    def __str__(self):
        return f"Daily Sales Report - {self.report_date}"
    
    @property
    def net_amount(self):
        """Calculate net amount after discount and tax"""
        return self.total_amount - self.total_discount + self.total_tax


class Discount(models.Model):
    """Discount rules and codes for sales"""
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('fixed', 'Fixed Amount'),
    ]
    
    APPLY_TO_CHOICES = [
        ('all', 'All Products'),
        ('product', 'Specific Product'),
        ('category', 'Product Category'),
        ('branch', 'Specific Branch'),
        ('min_purchase', 'Minimum Purchase Amount'),
    ]
    
    name = models.CharField(max_length=255, verbose_name='Discount Name')
    code = models.CharField(max_length=50, unique=True, null=True, blank=True, verbose_name='Discount Code')
    description = models.TextField(blank=True, verbose_name='Description')
    
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='percentage', verbose_name='Discount Type')
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Discount Value', 
                                        help_text='Percentage (0-100) or fixed amount')
    
    apply_to = models.CharField(max_length=20, choices=APPLY_TO_CHOICES, default='all', verbose_name='Apply To')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, null=True, blank=True, related_name='discounts', verbose_name='Product')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, null=True, blank=True, related_name='discounts', verbose_name='Category')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True, related_name='discounts', verbose_name='Branch')
    min_purchase_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, 
                                             verbose_name='Minimum Purchase Amount',
                                             help_text='Minimum total amount required for discount')
    
    max_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                             verbose_name='Maximum Discount Amount',
                                             help_text='Maximum discount amount (for percentage discounts)')
    
    start_date = models.DateTimeField(null=True, blank=True, verbose_name='Start Date')
    end_date = models.DateTimeField(null=True, blank=True, verbose_name='End Date')
    
    is_active = models.BooleanField(default=True, verbose_name='Is Active')
    usage_limit = models.IntegerField(null=True, blank=True, verbose_name='Usage Limit',
                                      help_text='Maximum number of times this discount can be used')
    usage_count = models.IntegerField(default=0, verbose_name='Usage Count')
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_discounts', verbose_name='Created By')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Updated At')
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Discount'
        verbose_name_plural = 'Discounts'
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active', 'start_date', 'end_date']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.code or 'No Code'})"
    
    def is_valid(self):
        """Check if discount is currently valid"""
        if not self.is_active:
            return False
        
        now = timezone.now()
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False
        if self.usage_limit and self.usage_count >= self.usage_limit:
            return False
        
        return True
    
    def calculate_discount(self, amount: Decimal, quantity: Decimal = Decimal('1')) -> Decimal:
        """Calculate discount amount for given price and quantity"""
        if not self.is_valid():
            return Decimal('0')
        
        if self.discount_type == 'percentage':
            discount = (amount * quantity) * (self.discount_value / 100)
            if self.max_discount_amount:
                discount = min(discount, self.max_discount_amount)
        else:  # fixed
            discount = self.discount_value * quantity
        
        return discount
    
    def can_apply_to_sale(self, sale: 'Sale', items: List[Dict]) -> tuple[bool, str]:
        """Check if discount can be applied to a sale"""
        if not self.is_valid():
            return False, 'Discount is not active or has expired'
        
        # Check apply_to conditions
        if self.apply_to == 'branch' and self.branch and sale.branch != self.branch:
            return False, 'Discount not valid for this branch'
        
        if self.apply_to == 'min_purchase' and self.min_purchase_amount:
            total_amount = sum(item.get('subtotal', item.get('unit_price', 0) * item.get('quantity', 0)) for item in items)
            if total_amount < self.min_purchase_amount:
                return False, f'Minimum purchase amount of {self.min_purchase_amount} required'
        
        if self.apply_to == 'product' and self.product:
            if not any(item.get('product_id') == self.product.id for item in items):
                return False, 'Discount product not in sale'
        
        if self.apply_to == 'category' and self.category:
            from products.models import Product
            product_ids = [item.get('product_id') for item in items]
            products = Product.objects.filter(id__in=product_ids, category=self.category)
            if not products.exists():
                return False, 'No products from discount category in sale'
        
        return True, 'Discount can be applied'
    
    def increment_usage(self):
        """Increment usage count"""
        self.usage_count += 1
        self.save(update_fields=['usage_count'])


class ReturnAuthorizationCode(models.Model):
    """Authorization codes required for branch managers to process returns."""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='return_authorization_codes')
    code = models.CharField(max_length=12)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_return_authorization_codes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['branch', 'is_active', 'expires_at']),
        ]

    def __str__(self):
        return f"Return code for {self.branch.name} (expires {self.expires_at})"


class CashReceived(models.Model):
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='cash_received',
                                verbose_name='Cashier', help_text='Cashier who received the cash')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='cash_received_entries',
                               verbose_name='Branch', help_text='Branch where cash was received')
    date = models.DateField(verbose_name='Date', help_text='Date when cash was received')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, 
                                       verbose_name='Total Cash Received', 
                                       help_text='Total amount of cash received from the cashier')
    entered_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, 
                                  related_name='entered_cash_received',
                                  verbose_name='Entered By', 
                                  help_text='Manager who entered this cash received record')
    notes = models.TextField(blank=True, verbose_name='Notes', 
                            help_text='Additional notes about the cash received')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cash Received'
        verbose_name_plural = 'Cash Received'
        ordering = ['-date', '-created_at']
        unique_together = ['cashier', 'branch', 'date']  # One entry per cashier per branch per day
        indexes = [
            models.Index(fields=['cashier', 'date']),
            models.Index(fields=['branch', 'date']),
            models.Index(fields=['date']),
        ]

    def __str__(self):
        cashier_name = self.cashier.email if self.cashier else 'Unknown'
        return f"{cashier_name} - {self.date} - ${self.total_amount}"
    