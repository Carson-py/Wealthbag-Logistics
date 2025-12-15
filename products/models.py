from django.db import models
from django.utils import timezone
from django.core.files.storage import default_storage
from organization.models import Warehouse, Branch
import os


class Category(models.Model):
    name = models.CharField(max_length=255, unique=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name_plural = 'Categories'


class Unit(models.Model):
    name = models.CharField(max_length=50, unique=True)
    abbreviation = models.CharField(max_length=10, blank=True)
    
    def __str__(self):
        return self.name

class Product(models.Model):
    sku = models.CharField(max_length=128, unique=True, verbose_name='SKU')
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    image = models.ImageField(null=True, blank=True, upload_to = 'product_images')
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.name}"   
    @property
    def primary_barcode(self):
        """Get the primary barcode for this product"""
        barcode = self.barcodes.filter(is_primary=True).first()
        return barcode.barcode if barcode else None


def barcode_image_upload_path(instance, filename):
    """Generate upload path for barcode images"""
    # Format: barcodes/product_{product_id}/barcode_{timestamp}_{filename}
    import uuid
    ext = filename.split('.')[-1] if '.' in filename else 'png'
    # Use timestamp and UUID to ensure uniqueness before instance is saved
    unique_id = str(uuid.uuid4())[:8]
    filename = f"barcode_{unique_id}.{ext}"
    return os.path.join('barcodes', f'product_{instance.product.id}', filename)


class Barcode(models.Model):
    """
    Barcode model to link barcodes to products.
    Barcodes are auto-generated using CODE128 format when products are created.
    The barcode value is the product SKU, making it easy to lookup products.
    """
    # All barcodes use CODE128 format (auto-generated)
    BARCODE_TYPE = 'CODE128'
    
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='barcodes')
    barcode_image = models.ImageField(upload_to=barcode_image_upload_path, verbose_name='Barcode Image',
                                     help_text='Auto-generated barcode image (CODE128 format)',
                                     null=True, blank=True)
    barcode = models.CharField(max_length=128, unique=True, db_index=True, verbose_name='Barcode Value',
                              help_text='Barcode value (product SKU or ID)')
    is_primary = models.BooleanField(default=False, verbose_name='Primary Barcode',
                                    help_text='Mark as primary for quick product lookup')
    notes = models.TextField(blank=True, help_text='Additional notes about this barcode')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Barcode'
        verbose_name_plural = 'Barcodes'
        ordering = ['-is_primary', 'barcode']
        indexes = [
            models.Index(fields=['barcode']),  # Fast lookup for sales
            models.Index(fields=['product', 'is_primary']),
        ]
        constraints = [
            # Ensure only one primary barcode per product
            models.UniqueConstraint(
                fields=['product'],
                condition=models.Q(is_primary=True),
                name='unique_primary_barcode_per_product'
            )
        ]
    
    def __str__(self):
        return f"{self.barcode} ({self.product.name})"
    
    def save(self, *args, **kwargs):
        """Ensure only one primary barcode per product"""
        # Ensure only one primary barcode per product
        if self.is_primary:
            # Unset other primary barcodes for this product
            Barcode.objects.filter(product=self.product, is_primary=True).exclude(pk=self.pk if self.pk else None).update(is_primary=False)
        super().save(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
        """Delete barcode image file when barcode is deleted"""
        if self.barcode_image:
            if default_storage.exists(self.barcode_image.name):
                default_storage.delete(self.barcode_image.name)
        super().delete(*args, **kwargs)

        