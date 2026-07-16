from django.db import models
from django.core.exceptions import ValidationError

# Create your models here.
class Warehouse(models.Model):
    name = models.CharField(max_length=255)
    is_main = models.BooleanField(default=False, verbose_name='Is Main Warehouse',
                                  help_text='Mark this warehouse as the main warehouse. Only one warehouse should be main.')
    location = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Warehouse'
        verbose_name_plural = 'Warehouses'
        ordering = ['-is_main', 'name']

    def __str__(self):
        return self.name
    
    def clean(self):
        """Ensure only one warehouse is marked as main"""
        if self.is_main:
            # Check if another warehouse is already marked as mainx,[]
            other_main = Warehouse.objects.filter(is_main=True).exclude(pk=self.pk).first()
            if other_main:
                raise ValidationError(
                    f'Warehouse "{other_main.name}" is already marked as main. '
                    'Only one warehouse can be marked as main. Please unmark the other warehouse first.'
                )
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Branch(models.Model):
    name = models.CharField(max_length=255)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='branches',
                                  verbose_name='Warehouse', 
                                  help_text='Warehouse this branch belongs to (optional)')
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Branch'
        verbose_name_plural = 'Branches'
        ordering = ['name']

    def __str__(self):
        return self.name