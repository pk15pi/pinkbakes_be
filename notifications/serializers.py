from rest_framework import serializers

from .models import InAppNotification, NotificationChannelConfig, NotificationLog, NotificationPreference, NotificationTemplate


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            'email_order_updates',
            'email_delivery_updates',
            'email_promotional',
            'email_review_updates',
            'sms_order_updates',
            'sms_promotional',
            'admin_alert_new_order',
            'admin_alert_low_stock',
            'admin_alert_payment_failed',
            'admin_alert_refund_failed',
            'updated_at',
        ]
        read_only_fields = ['updated_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        # Hide admin alert prefs from non-staff
        if not (request and request.user and request.user.is_staff):
            for key in list(data.keys()):
                if key.startswith('admin_alert_'):
                    data.pop(key, None)
        return data

    def validate(self, attrs):
        # Never allow opting out of critical via this API — those fields simply don't exist.
        return attrs


class InAppNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = InAppNotification
        fields = [
            'id', 'event', 'title', 'body', 'is_read',
            'reference_type', 'reference_id', 'created_at',
        ]
        read_only_fields = fields


class NotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = [
            'id', 'user', 'event', 'channel', 'status', 'idempotency_key',
            'reference_type', 'reference_id', 'recipient', 'retry_count',
            'last_error', 'created_at',
        ]
        read_only_fields = fields


class NotificationChannelConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationChannelConfig
        fields = ['id', 'event', 'channel', 'is_enabled', 'updated_at']
        read_only_fields = ['id', 'event', 'channel', 'updated_at']


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = [
            'id', 'event', 'channel', 'subject', 'body_html', 'body_text',
            'is_active', 'updated_at',
        ]
        read_only_fields = ['id', 'event', 'channel', 'updated_at']
