from django.db import migrations

PAIRS = [
    ('ORDER_CREATED', 'email'), ('ORDER_CREATED', 'sms'), ('ORDER_CREATED', 'whatsapp'),
    ('ORDER_CONFIRMED', 'sms'), ('ORDER_CONFIRMED', 'whatsapp'),
    ('ORDER_CANCELLED', 'sms'), ('ORDER_CANCELLED', 'whatsapp'),
    ('ORDER_OUT_FOR_DELIVERY', 'sms'), ('ORDER_OUT_FOR_DELIVERY', 'whatsapp'),
    ('DELIVERY_COMPLETED', 'sms'), ('DELIVERY_COMPLETED', 'whatsapp'),
    ('NEW_ORDER', 'email'), ('NEW_ORDER', 'sms'), ('NEW_ORDER', 'whatsapp'),
    ('LOW_STOCK', 'whatsapp'),
]


def forwards(apps, schema_editor):
    Config = apps.get_model('notifications', 'NotificationChannelConfig')
    for event, channel in PAIRS:
        # get_or_create: never re-enable a row an admin already switched off.
        Config.objects.get_or_create(event=event, channel=channel, defaults={'is_enabled': True})


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('notifications', '0002_seed_defaults')]
    operations = [migrations.RunPython(forwards, backwards)]
