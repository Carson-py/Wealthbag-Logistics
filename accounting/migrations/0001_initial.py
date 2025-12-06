from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('organization', '0002_alter_branch_options_alter_warehouse_options_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ExpenseCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='ProfitLossReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('total_revenue', models.DecimalField(decimal_places=2, max_digits=14)),
                ('total_cost_of_goods', models.DecimalField(decimal_places=2, max_digits=14)),
                ('total_expenses', models.DecimalField(decimal_places=2, max_digits=14)),
                ('net_profit', models.DecimalField(decimal_places=2, max_digits=14)),
                ('generated_at', models.DateTimeField(auto_now_add=True)),
                ('branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='profit_loss_reports', to='organization.branch')),
                ('generated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='profit_loss_reports', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-generated_at'],
            },
        ),
        migrations.CreateModel(
            name='Expense',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.TextField(blank=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('incurred_on', models.DateField()),
                ('attachment', models.FileField(blank=True, null=True, upload_to='expenses/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expenses', to='organization.branch')),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expenses', to='accounting.expensecategory')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_expenses', to=settings.AUTH_USER_MODEL)),
                ('warehouse', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expenses', to='organization.warehouse')),
            ],
            options={
                'ordering': ['-incurred_on', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='profitlossreport',
            index=models.Index(fields=['start_date', 'end_date'], name='accounting_start_d_90c175_idx'),
        ),
        migrations.AddIndex(
            model_name='expense',
            index=models.Index(fields=['branch', 'incurred_on'], name='accounting_branch__bbd079_idx'),
        ),
        migrations.AddIndex(
            model_name='expense',
            index=models.Index(fields=['warehouse', 'incurred_on'], name='accounting_warehous_5acdd3_idx'),
        ),
    ]

