from django.contrib import admin
from .models import Unit, Category, Product, Barcode
# Register your models here.

my_models = [Unit, Category, Product, Barcode]

admin.site.register(my_models)