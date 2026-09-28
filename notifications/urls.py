from django.urls import path

from .views import (
    AdminChannelConfigDetailView,
    AdminChannelConfigListView,
    AdminInAppNotificationListView,
    AdminNotificationLogListView,
    AdminNotificationTemplateDetailView,
    AdminNotificationTemplateListView,
    InAppNotificationListView,
    InAppNotificationMarkAllReadView,
    InAppNotificationMarkReadView,
    NotificationPreferenceView,
)

# Mounted under /api/accounts/
account_urlpatterns = [
    path('notification-preferences/', NotificationPreferenceView.as_view(), name='notification-preferences'),
    path('notifications/', InAppNotificationListView.as_view(), name='in-app-notifications'),
    path('notifications/mark-all-read/', InAppNotificationMarkAllReadView.as_view(), name='in-app-notifications-mark-all-read'),
    path('notifications/<int:notification_id>/read/', InAppNotificationMarkReadView.as_view(), name='in-app-notification-mark-read'),
]

# Mounted under /api/
admin_urlpatterns = [
    path('admin/notification-logs/', AdminNotificationLogListView.as_view(), name='admin-notification-logs'),
    path('admin/notification-channels/', AdminChannelConfigListView.as_view(), name='admin-notification-channels'),
    path('admin/notification-channels/<int:config_id>/', AdminChannelConfigDetailView.as_view(), name='admin-notification-channel-detail'),
    path('admin/notification-templates/', AdminNotificationTemplateListView.as_view(), name='admin-notification-templates'),
    path('admin/notification-templates/<int:template_id>/', AdminNotificationTemplateDetailView.as_view(), name='admin-notification-template-detail'),
    path('admin/notifications/', AdminInAppNotificationListView.as_view(), name='admin-in-app-notifications'),
]
