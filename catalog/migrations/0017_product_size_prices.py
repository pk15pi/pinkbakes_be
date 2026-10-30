# BUG-026: optional per-size prices on Product (additive; existing rows get {}).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0016_orderitem_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='size_prices',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
