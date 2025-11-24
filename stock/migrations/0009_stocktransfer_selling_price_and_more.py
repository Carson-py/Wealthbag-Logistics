from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0008_stocktransfer_reorder_level'),
    ]

    operations = [
        migrations.AddField(
            model_name='stocktransfer',
            name='selling_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Selling Price Per Unit'),
        ),
        migrations.AddField(
            model_name='stocktransferitem',
            name='selling_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Selling Price Per Unit'),
        ),
        migrations.AlterField(
            model_name='stocktransferitem',
            name='purchase_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Purchase Price Per Unit'),
        ),
    ]

