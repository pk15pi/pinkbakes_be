from django.conf import settings
from django.db import models

from .events import ALL_CHANNELS


class NotificationChannelConfig(models.Model):
    """Which channels fire for each event (enable/disable per event+channel)."""

    event = models.CharField(max_length=64, db_index=True)
    channel = models.CharField(max_length=20, choices=[(c, c) for c in ALL_CHANNELS])
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('event', 'channel')]
        ordering = ['event', 'channel']

    def __str__(self):
        return f'{self.event}/{self.channel}={"on" if self.is_enabled else "off"}'


class NotificationTemplate(models.Model):
    event = models.CharField(max_length=64, db_index=True)
    channel = models.CharField(max_length=20, choices=[(c, c) for c in ALL_CHANNELS], default='email')
    subject = models.CharField(max_length=255, blank=True, default='')
    body_html = models.TextField(blank=True, default='')
    body_text = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('event', 'channel')]
        ordering = ['event', 'channel']

    def __str__(self):
        return f'{self.event}/{self.channel}'


class NotificationPreference(models.Model):
    """Per-user prefs. Critical events ignore opt-out."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_preferences'
    )
    email_order_updates = models.BooleanField(default=True)
    email_delivery_updates = models.BooleanField(default=True)
    email_promotional = models.BooleanField(default=True)
    email_review_updates = models.BooleanField(default=True)
    sms_order_updates = models.BooleanField(default=True)
    sms_promotional = models.BooleanField(default=False)
    # Admin operational alert prefs (staff only meaningful)
    admin_alert_new_order = models.BooleanField(default=True)
    admin_alert_low_stock = models.BooleanField(default=True)
    admin_alert_payment_failed = models.BooleanField(default=True)
    admin_alert_refund_failed = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'prefs:{self.user_id}'


class NotificationLog(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='notification_logs'
    )
    event = models.CharField(max_length=64, db_index=True)
    channel = models.CharField(max_length=20, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    idempotency_key = models.CharField(max_length=191, blank=True, default='', db_index=True)
    reference_type = models.CharField(max_length=40, blank=True, default='')
    reference_id = models.CharField(max_length=64, blank=True, default='')
    recipient = models.CharField(max_length=255, blank=True, default='')
    retry_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['event', 'channel', 'status']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['idempotency_key'],
                condition=~models.Q(idempotency_key=''),
                name='notifications_log_unique_idempotency_key',
            ),
        ]

    def __str__(self):
        return f'{self.event}/{self.channel}/{self.status}'


class InAppNotification(models.Model):
    """Customer + admin in-app notification center records."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='in_app_notifications'
    )
    event = models.CharField(max_length=64, db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default='')
    is_read = models.BooleanField(default=False)
    reference_type = models.CharField(max_length=40, blank=True, default='')
    reference_id = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', 'created_at']),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.event}:{self.title[:40]}'
