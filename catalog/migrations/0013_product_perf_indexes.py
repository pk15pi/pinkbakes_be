# Generated manually for performance pass — Product list/filter indexes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0012_adminactivity_action_choices_expand'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['is_active', 'status', 'featured', 'created_at'], name='catalog_prod_list_idx'),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['category', 'is_active', 'status'], name='catalog_prod_cat_idx'),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['availability'], name='catalog_prod_avail_idx'),
        ),
    ]
