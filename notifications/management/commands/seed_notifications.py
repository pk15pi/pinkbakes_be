from django.core.management.base import BaseCommand

from notifications.defaults import seed_channel_configs, seed_email_templates


class Command(BaseCommand):
    help = 'Seed notification channel configs and default email templates.'

    def handle(self, *args, **options):
        seed_channel_configs()
        seed_email_templates()
        self.stdout.write(self.style.SUCCESS('Notification defaults seeded.'))
