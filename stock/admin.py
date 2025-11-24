from django.contrib import admin
from .models import StockEntry, StockAdjustment, StockTransfer, Supplier, BranchStock, StockTransfer


@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ['product', 'warehouse', 'quantity', 'purchase_price', 'batch_number', 'received_date', 'created_by']
    list_filter = ['warehouse', 'product', 'received_date']
    search_fields = ['product__name', 'product__sku', 'batch_number', 'warehouse__name']
    readonly_fields = ['created_at', 'total_cost']
    date_hierarchy = 'received_date'
    
    fieldsets = (
        ('Product Information', {
            'fields': ('product', 'warehouse', 'supplier')
        }),
        ('Stock Details', {
            'fields': ('quantity', 'purchase_price', 'total_cost', 'batch_number')
        }),
        ('Additional Information', {
            'fields': ('received_date', 'notes', 'created_by', 'created_at')
        }),
    )


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ['product', 'warehouse', 'adjustment_type', 'quantity', 'created_at', 'created_by']
    list_filter = ['adjustment_type', 'warehouse', 'created_at']
    search_fields = ['product__name', 'product__sku', 'reason', 'reference_number']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'

admin.site.register(Supplier)
admin.site.register(BranchStock)
admin.site.register(StockTransfer)

