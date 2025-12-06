from django.contrib import admin
from .models import Sale, SaleItem, ProductReturn, DailySalesReport, Discount, ReturnAuthorizationCode, CashReceived
# Register your models here.

my_models = [Sale, SaleItem, ProductReturn, DailySalesReport, Discount, ReturnAuthorizationCode, CashReceived]
admin.site.register(my_models)