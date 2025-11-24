from django.contrib import admin
from .models import (
    Branch, Warehouse
)

my_models = [Branch, Warehouse]
admin.site.register(my_models)