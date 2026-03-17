from django.contrib import admin
from .models import Layby, LaybyItem, LaybyPayment

# Register your models here.
admin.site.register(Layby)
admin.site.register(LaybyItem)
admin.site.register(LaybyPayment)
