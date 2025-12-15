from django.db import models
from django.utils import timezone
from decimal import Decimal
from datetime import datetime
import uuid
from products.models import Product
from organization.models import Warehouse, Branch

class Supplier(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name


class StockEntryGroup(models.Model):
    """
    Groups multiple stock entries together (e.g., from a single delivery/receipt).
    Allows tracking multiple products added to warehouse in one operation.
    """
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='stock_entry_groups')
    reference_number = models.CharField(max_length=100, unique=True, blank=True, verbose_name='Reference Number')
    notes = models.TextField(blank=True, verbose_name='Group Notes')
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, related_name='created_stock_entry_groups')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Stock Entry Group'
        verbose_name_plural = 'Stock Entry Groups'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Stock Entry Group: {self.reference_number or self.id} @ {self.warehouse.name}"
    
    def _generate_reference_number(self) -> str:
        """Generate a unique reference number for stock entry groups"""
        date_prefix = datetime.now().strftime('%Y%m%d')
        uuid_part = str(uuid.uuid4())[:8].upper()
        reference_number = f'STOCK-{date_prefix}-{uuid_part}'
        
        max_attempts = 10
        attempts = 0
        while attempts < max_attempts:
            if not StockEntryGroup.objects.filter(reference_number=reference_number).exclude(pk=self.pk if self.pk else None).exists():
                return reference_number
            uuid_part = str(uuid.uuid4())[:8].upper()
            reference_number = f'STOCK-{date_prefix}-{uuid_part}'
            attempts += 1
        
        timestamp = int(timezone.now().timestamp() * 1000) % 1000000
        return f'STOCK-{date_prefix}-{uuid_part}-{timestamp}'
    
    def save(self, *args, **kwargs):
        if not self.reference_number or self.reference_number.strip() == '':
            self.reference_number = self._generate_reference_number()
        super().save(*args, **kwargs)
    
    @property
    def total_cost(self):
        """Calculate total cost for all entries in this group"""
        return sum(entry.total_cost for entry in self.entries.all())


class StockEntry(models.Model):
    """
    Track stock entries with purchase prices.
    Allows tracking different batches of the same product with different purchase prices.
    Can be grouped via StockEntryGroup for multi-product entries.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_entries')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='stock_entries')
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_entries')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    purchase_price = models.DecimalField(max_digits=16, decimal_places=8, verbose_name='Purchase Price Per Unit')
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Selling Price Per Unit')
    reorder_level = models.IntegerField(default = 0)
    batch_number = models.CharField(max_length=100, unique=True, blank=False, verbose_name='Batch')
    received_date = models.DateTimeField(default=timezone.now, verbose_name='Date Received')
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, related_name='created_stock_entries')
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Grouping for multi-product entries
    entry_group = models.ForeignKey('StockEntryGroup', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='entries', verbose_name='Entry Group',
                                    help_text='Group this entry belongs to (for multi-product entries)')
    
    # Fields to track initial stock vs transferred stock
    is_initial_stock = models.BooleanField(default=True, verbose_name='Is Initial Stock',
                                          help_text='True if this is initial stock added directly, False if from transfer')
    source_transfer = models.ForeignKey('StockTransfer', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='destination_stock_entries', verbose_name='Source Transfer',
                                       help_text='The transfer that created this stock entry (if from transfer)')
    original_stock_entry = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='transferred_entries', verbose_name='Original Stock Entry',
                                            help_text='The original stock entry this was transferred from')
    original_batch_number = models.CharField(max_length=100, blank=True, verbose_name='Original Batch Number',
                                           help_text='The original batch number before transfer')
    
    class Meta:
        verbose_name = 'Stock Entry'
        verbose_name_plural = 'Stock Entries'
        ordering = ['-received_date', '-created_at']
        indexes = [
            models.Index(fields=['product', 'warehouse']),
            models.Index(fields=['warehouse', 'received_date']),
            models.Index(fields=['product', 'purchase_price']),
        ]
    
    def __str__(self):
        return f"{self.product.name} @ {self.warehouse.name}: {self.quantity} units @ ${self.purchase_price} (Batch: {self.batch_number or 'N/A'})"
    
    @property
    def total_cost(self):
        """Calculate total cost for this stock entry"""
        return self.quantity * self.purchase_price


class StockAdjustment(models.Model):
    """
    Track stock adjustments (additions, removals, corrections)
    """
    ADJUSTMENT_TYPES = [
        ('addition', 'Addition'),
        ('removal', 'Removal'),
        ('correction', 'Correction'),
        ('damaged', 'Damaged')
    ]
    
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_adjustments')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='stock_adjustments')
    adjustment_type = models.CharField(max_length=20, choices=ADJUSTMENT_TYPES)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Quantity Change')
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, 
                                        verbose_name='Purchase Price (if adding stock)')
    reason = models.TextField(blank=True)
    reference_number = models.CharField(max_length=100, blank=True, verbose_name='Reference Number')
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, related_name='created_adjustments')
    created_at = models.DateTimeField(auto_now_add=True)
    corrected_adjustment = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                             related_name='correction_adjustments', verbose_name='Corrected Adjustment',
                                             help_text='The adjustment that was corrected by this adjustment')
    corrected_adjustment = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                             related_name='correction_adjustments', verbose_name='Corrected Adjustment',
                                             help_text='The adjustment that was corrected by this adjustment')
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product', 'warehouse', 'created_at']),
            models.Index(fields=['adjustment_type', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.adjustment_type.title()}: {self.product.name} @ {self.warehouse.name} - {self.quantity} units"

# Model to store the stock for a certain branch
class BranchStock(models.Model):
    """
    Track stock entries for branches with purchase prices.
    Similar to StockEntry but for branches.
    Allows tracking different batches of the same product with different purchase prices.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='branch_stock_entries')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='stock_entries')
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='branch_stock_entries')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    purchase_price = models.DecimalField(max_digits=16, decimal_places=8, verbose_name='Purchase Price Per Unit')
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Selling Price Per Unit')
    reorder_level = models.IntegerField(default = 0)
    batch_number = models.CharField(max_length=100, unique=True, blank=False, verbose_name='Batch')
    received_date = models.DateTimeField(default=timezone.now, verbose_name='Date Received')
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, related_name='created_branch_stock_entries')
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Fields to track initial stock vs transferred stock
    is_initial_stock = models.BooleanField(default=False, verbose_name='Is Initial Stock',
                                          help_text='True if this is initial stock added directly, False if from transfer (branches only receive stock via transfers)')
    source_transfer = models.ForeignKey('StockTransfer', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='destination_branch_stock', verbose_name='Source Transfer',
                                        help_text='The transfer that created this stock entry')
    original_stock_entry = models.ForeignKey(StockEntry, on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='transferred_to_branch', verbose_name='Original Warehouse Stock Entry',
                                            help_text='The original warehouse stock entry this was transferred from')
    original_branch_stock = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='transferred_entries', verbose_name='Original Branch Stock Entry',
                                            help_text='The original branch stock entry this was transferred from (for branch-to-branch transfers)')
    original_batch_number = models.CharField(max_length=100, blank=True, verbose_name='Original Batch Number',
                                           help_text='The original batch number before transfer')
    
    class Meta:
        verbose_name = 'Branch Stock Entry'
        verbose_name_plural = 'Branch Stock Entries'
        ordering = ['-received_date', '-created_at']
        indexes = [
            models.Index(fields=['product', 'branch']),
            models.Index(fields=['branch', 'received_date']),
            models.Index(fields=['product', 'purchase_price']),
        ]
    
    def __str__(self):
        return f"{self.product.name} @ {self.branch.name}: {self.quantity} units @ ${self.purchase_price} (Batch: {self.batch_number or 'N/A'})"
    
    @property
    def total_cost(self):
        """Calculate total cost for this branch stock entry"""
        return self.quantity * self.purchase_price


# Model to store the stock transfer process from warehouse to warehouse, warehouse to branch, branch to branch and branch to warehouse
class StockTransfer(models.Model):
    """
    Track stock transfers between warehouses and branches.
    Supports: warehouse to warehouse, warehouse to branch, branch to branch, and branch to warehouse.
    Can contain multiple products via StockTransferItem.
    """
    TRANSFER_TYPES = [
        ('warehouse_to_warehouse', 'Warehouse to Warehouse'),
        ('warehouse_to_branch', 'Warehouse to Branch'),
        ('branch_to_branch', 'Branch to Branch'),
        ('branch_to_warehouse', 'Branch to Warehouse'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_transit', 'In Transit'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    transfer_type = models.CharField(max_length=30, choices=TRANSFER_TYPES, verbose_name='Transfer Type')
    
    # Legacy fields for backward compatibility (single product transfers)
    # These are nullable to support multi-product transfers via StockTransferItem
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_transfers', null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfers')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reorder_level = models.IntegerField(null=True, blank=True, default=None)
    purchase_price = models.DecimalField(max_digits=16, decimal_places=8, null=True, blank=True, verbose_name='Purchase Price Per Unit')
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Selling Price Per Unit')
    batch_number = models.CharField(max_length=100, blank=True, verbose_name='Batch Number')
    
    # Source can be either warehouse or branch
    source_warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, null=True, blank=True, 
                                         related_name='outgoing_transfers', verbose_name='Source Warehouse')
    source_branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True,
                                      related_name='outgoing_transfers', verbose_name='Source Branch')
    
    # Destination can be either warehouse or branch
    destination_warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, null=True, blank=True,
                                             related_name='incoming_transfers', verbose_name='Destination Warehouse')
    destination_branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True,
                                          related_name='incoming_transfers', verbose_name='Destination Branch')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reference_number = models.CharField(max_length=100, unique=True, blank=True, verbose_name='Reference Number')
    notes = models.TextField(blank=True)
    
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, 
                                   related_name='created_transfers', verbose_name='Created By')
    completed_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='completed_transfers', verbose_name='Completed By')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        verbose_name = 'Stock Transfer'
        verbose_name_plural = 'Stock Transfers'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['transfer_type', 'status']),
            models.Index(fields=['product', 'status']),
            models.Index(fields=['source_warehouse', 'status']),
            models.Index(fields=['source_branch', 'status']),
            models.Index(fields=['destination_warehouse', 'status']),
            models.Index(fields=['destination_branch', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]
    
    def __str__(self):
        source = self.source_warehouse.name if self.source_warehouse else self.source_branch.name
        destination = self.destination_warehouse.name if self.destination_warehouse else self.destination_branch.name
        item_count = self.items.count() if hasattr(self, 'items') else (1 if self.product else 0)
        return f"Transfer: {item_count} item(s) from {source} to {destination} - {self.get_status_display()}"
    
    @property
    def total_cost(self):
        """Calculate total cost for this transfer (sum of all items)"""
        if hasattr(self, 'items') and self.items.exists():
            return sum(item.total_cost for item in self.items.all())
        # Fallback to legacy single product
        if self.quantity and self.purchase_price:
            return self.quantity * self.purchase_price
        return Decimal('0')
    
    def _generate_reference_number(self) -> str:
        """
        Generate a unique reference number for stock transfers.
        Format: REF-YYYYMMDD-UUID (first 8 chars of UUID for readability)
        """
        date_prefix = datetime.now().strftime('%Y%m%d')
        uuid_part = str(uuid.uuid4())[:8].upper()
        reference_number = f'REF-{date_prefix}-{uuid_part}'
        
        # Ensure uniqueness - exclude current instance if it exists
        max_attempts = 10
        attempts = 0
        
        while attempts < max_attempts:
            queryset = StockTransfer.objects.filter(reference_number=reference_number)
            # Exclude current instance if updating
            if self.pk:
                queryset = queryset.exclude(pk=self.pk)
            
            if not queryset.exists():
                return reference_number
            
            # If exists, generate new one
            uuid_part = str(uuid.uuid4())[:8].upper()
            reference_number = f'REF-{date_prefix}-{uuid_part}'
            attempts += 1
        
        # Fallback: add timestamp to ensure uniqueness
        timestamp = int(timezone.now().timestamp() * 1000) % 1000000
        return f'REF-{date_prefix}-{uuid_part}-{timestamp}'
    
    def save(self, *args, **kwargs):
        """Override save to auto-generate reference_number if not provided"""
        if not self.reference_number or self.reference_number.strip() == '':
            self.reference_number = self._generate_reference_number()
        super().save(*args, **kwargs)
    
    def clean(self):
        """Validate that source and destination are set correctly based on transfer type"""
        from django.core.exceptions import ValidationError
        
        if self.transfer_type == 'warehouse_to_warehouse':
            if not self.source_warehouse or not self.destination_warehouse:
                raise ValidationError('Both source and destination warehouses must be specified for warehouse to warehouse transfers.')
            if self.source_warehouse == self.destination_warehouse:
                raise ValidationError('Source and destination warehouses cannot be the same.')
        
        elif self.transfer_type == 'warehouse_to_branch':
            if not self.source_warehouse or not self.destination_branch:
                raise ValidationError('Source warehouse and destination branch must be specified for warehouse to branch transfers.')
        
        elif self.transfer_type == 'branch_to_branch':
            if not self.source_branch or not self.destination_branch:
                raise ValidationError('Both source and destination branches must be specified for branch to branch transfers.')
            if self.source_branch == self.destination_branch:
                raise ValidationError('Source and destination branches cannot be the same.')
        
        elif self.transfer_type == 'branch_to_warehouse':
            if not self.source_branch or not self.destination_warehouse:
                raise ValidationError('Source branch and destination warehouse must be specified for branch to warehouse transfers.')


class StockTransferItem(models.Model):
    """
    Individual product items within a stock transfer.
    Allows a single transfer to contain multiple products.
    """
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name='items', verbose_name='Transfer')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='transfer_items', verbose_name='Product')
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfer_items', verbose_name='Supplier')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Quantity')
    purchase_price = models.DecimalField(max_digits=16, decimal_places=8, null=True, blank=True, verbose_name='Purchase Price Per Unit')
    reorder_level = models.IntegerField(null=True, blank=True, default=None)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Selling Price Per Unit')
    batch_number = models.CharField(max_length=100, blank=True, verbose_name='Batch Number')
    notes = models.TextField(blank=True, verbose_name='Item Notes')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Stock Transfer Item'
        verbose_name_plural = 'Stock Transfer Items'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['transfer', 'product']),
            models.Index(fields=['product', 'transfer']),
        ]
    
    def __str__(self):
        return f"{self.product.name} - {self.quantity} units @ ${self.purchase_price} (Transfer: {self.transfer.reference_number})"
    
    @property
    def total_cost(self):
        """Calculate total cost for this transfer item"""
        if self.purchase_price is None:
            return Decimal('0')
        return self.quantity * self.purchase_price