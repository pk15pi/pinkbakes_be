from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_stock(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    InventoryTransaction = apps.get_model('catalog', 'InventoryTransaction')
    threshold_default = 5
    for product in Product.objects.all():
        if product.availability == 'out_of_stock':
            available = 0
        elif product.availability == 'low_stock':
            available = 3
        else:
            # published / in_stock and anything else get a sensible catalog default
            available = 50
        product.available_quantity = available
        product.reserved_quantity = 0
        product.sold_quantity = 0
        product.low_stock_threshold = threshold_default
        # Sync availability from quantity
        if available <= 0:
            product.availability = 'out_of_stock'
        elif available <= threshold_default:
            product.availability = 'low_stock'
        else:
            product.availability = 'in_stock'
        product.save(update_fields=[
            'available_quantity', 'reserved_quantity', 'sold_quantity',
            'low_stock_threshold', 'availability',
        ])
        InventoryTransaction.objects.create(
            product=product,
            quantity_change=available,
            previous_quantity=0,
            new_quantity=available,
            adjustment_type='INITIAL_STOCK',
            reason='Data migration seed from prior availability enum',
            reference_type='migration',
            reference_id='0008',
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('catalog', '0007_order_cancel_and_refund'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='available_quantity',
            field=models.PositiveIntegerField(default=50),
        ),
        migrations.AddField(
            model_name='product',
            name='reserved_quantity',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='product',
            name='sold_quantity',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='product',
            name='low_stock_threshold',
            field=models.PositiveIntegerField(default=5),
        ),
        migrations.AddField(
            model_name='product',
            name='stock_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='adminactivity',
            name='action',
            field=models.CharField(
                choices=[
                    ('login', 'Login'),
                    ('product_create', 'Product Created'),
                    ('product_update', 'Product Updated'),
                    ('product_delete', 'Product Deleted'),
                    ('product_image_upload', 'Product Image Upload'),
                    ('product_3d_upload', '3D Asset Upload'),
                    ('review_moderate', 'Review Moderated'),
                    ('settings_update', 'Settings Updated'),
                    ('inventory_adjust', 'Inventory Adjusted'),
                ],
                default='login',
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name='InventoryTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity_change', models.IntegerField(default=0)),
                ('previous_quantity', models.IntegerField(default=0)),
                ('new_quantity', models.IntegerField(default=0)),
                ('adjustment_type', models.CharField(
                    choices=[
                        ('INITIAL_STOCK', 'Initial Stock'),
                        ('RESTOCK', 'Restock'),
                        ('MANUAL_ADJUSTMENT', 'Manual Adjustment'),
                        ('ORDER_RESERVED', 'Order Reserved'),
                        ('ORDER_CONSUMED', 'Order Consumed'),
                        ('ORDER_RELEASED', 'Order Released'),
                        ('ORDER_RESTORED', 'Order Restored'),
                        ('CORRECTION', 'Correction'),
                    ],
                    max_length=30,
                )),
                ('reason', models.TextField(blank=True, default='')),
                ('reference_type', models.CharField(blank=True, default='', max_length=40)),
                ('reference_id', models.CharField(blank=True, default='', max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('performed_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='inventory_transactions',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='inventory_transactions',
                    to='catalog.product',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='inventorytransaction',
            index=models.Index(fields=['product', 'created_at'], name='catalog_inv_product_0c3a2a_idx'),
        ),
        migrations.AddIndex(
            model_name='inventorytransaction',
            index=models.Index(fields=['reference_type', 'reference_id'], name='catalog_inv_referen_7e1c4b_idx'),
        ),
        migrations.AddIndex(
            model_name='inventorytransaction',
            index=models.Index(fields=['adjustment_type', 'created_at'], name='catalog_inv_adjustm_9a2f1d_idx'),
        ),
        migrations.RunPython(seed_stock, noop_reverse),
    ]
