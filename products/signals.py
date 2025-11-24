"""
Django signals for products app.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Product
from .services import generate_product_barcode


@receiver(post_save, sender=Product)
def auto_generate_product_barcode(sender, instance, created, **kwargs):
    """
    Automatically generate a barcode for a product when it's created.
    The barcode value will be the product SKU, making it easy to lookup products.
    """
    if created:
        # Only generate barcode for newly created products
        try:
            generate_product_barcode(instance)
        except Exception as e:
            # Log error but don't prevent product creation
            print(f"Error generating barcode for product {instance.sku}: {str(e)}")

