from django.db import migrations


def seed(apps, schema_editor):
    from notifications.defaults import seed_channel_configs, seed_email_templates
    seed_channel_configs()
    seed_email_templates()


def unseed(apps, schema_editor):
    NotificationChannelConfig = apps.get_model('notifications', 'NotificationChannelConfig')
    NotificationTemplate = apps.get_model('notifications', 'NotificationTemplate')
    NotificationChannelConfig.objects.all().delete()
    NotificationTemplate.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
