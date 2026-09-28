from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0011_customer_address_delivery'),
    ]

    operations = [
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
                    ('order_status', 'Order Status Updated'),
                    ('order_cancel', 'Order Cancelled'),
                    ('refund', 'Refund Action'),
                    ('inventory_adjust', 'Inventory Adjusted'),
                    ('coupon_create', 'Coupon Created'),
                    ('coupon_update', 'Coupon Updated'),
                    ('employee_assign', 'Delivery Employee Assigned'),
                    ('employee_unassign', 'Delivery Employee Unassigned'),
                    ('employee_update', 'Employee Updated'),
                    ('customer_status', 'Customer Account Status'),
                    ('export', 'Data Export'),
                ],
                default='login',
                max_length=40,
            ),
        ),
    ]
