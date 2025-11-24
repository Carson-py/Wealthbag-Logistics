from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0007_alter_stocktransfer_product_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='stocktransfer',
            name='reorder_level',
            field=models.IntegerField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='stocktransferitem',
            name='reorder_level',
            field=models.IntegerField(blank=True, default=None, null=True),
        ),
    ]

