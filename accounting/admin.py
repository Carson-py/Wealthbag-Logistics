from django.contrib import admin
from . models import ExpenseCategory, Expense, ProfitLossReport
# Register your models here.

my_models = [ExpenseCategory, Expense, ProfitLossReport]
admin.site.register(my_models)