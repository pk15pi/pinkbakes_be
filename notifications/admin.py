from django.contrib import admin

from .models import (
    InAppNotification,
    NotificationChannelConfig,
    NotificationLog,
    NotificationPreference,
    NotificationTemplate,
)


@admin.register(NotificationChannelConfig)
class NotificationChannelConfigAdmin(admin.ModelAdmin):
    list_display = ('event', 'channel', 'is_enabled', 'updated_at')
    list_filter = ('channel', 'is_enabled')
    search_fields = ('event',)


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ('event', 'channel', 'subject', 'is_active', 'updated_at')
    list_filter = ('channel', 'is_active')
    search_fields = ('event', 'subject')


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('event', 'channel', 'status', 'recipient', 'retry_count', 'created_at')
    list_filter = ('channel', 'status', 'event')
    search_fields = ('idempotency_key', 'recipient', 'reference_id')
    readonly_fields = ('created_at',)


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'email_order_updates', 'email_delivery_updates', 'email_promotional', 'updated_at')
    search_fields = ('user__username', 'user__email')


@admin.register(InAppNotification)
class InAppNotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'title', 'is_read', 'created_at')
    list_filter = ('event', 'is_read')
    search_fields = ('title', 'user__username', 'user__email')
