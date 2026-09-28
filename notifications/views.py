from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import InAppNotification, NotificationChannelConfig, NotificationLog, NotificationPreference, NotificationTemplate
from .serializers import (
    InAppNotificationSerializer,
    NotificationChannelConfigSerializer,
    NotificationLogSerializer,
    NotificationPreferenceSerializer,
    NotificationTemplateSerializer,
)


class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class NotificationPreferenceView(APIView):
    """GET/PATCH /api/accounts/notification-preferences/"""

    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, user):
        prefs, _ = NotificationPreference.objects.get_or_create(user=user)
        return prefs

    def get(self, request):
        prefs = self.get_object(request.user)
        return Response(NotificationPreferenceSerializer(prefs, context={'request': request}).data)

    def patch(self, request):
        prefs = self.get_object(request.user)
        serializer = NotificationPreferenceSerializer(
            prefs, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        # Non-staff cannot set admin_alert_* fields
        if not request.user.is_staff:
            for key in list(serializer.validated_data.keys()):
                if key.startswith('admin_alert_'):
                    serializer.validated_data.pop(key, None)
        serializer.save()
        return Response(NotificationPreferenceSerializer(prefs, context={'request': request}).data)


class InAppNotificationListView(APIView):
    """GET /api/accounts/notifications/ — own notifications only."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = InAppNotification.objects.filter(user=request.user).order_by('-created_at')
        unread_only = (request.query_params.get('unread') or '').lower() in ('1', 'true', 'yes')
        if unread_only:
            qs = qs.filter(is_read=False)
        limit = min(int(request.query_params.get('limit') or 50), 200)
        items = list(qs[:limit])
        unread_count = InAppNotification.objects.filter(user=request.user, is_read=False).count()
        return Response({
            'unread_count': unread_count,
            'count': len(items),
            'results': InAppNotificationSerializer(items, many=True).data,
        })


class InAppNotificationMarkReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, notification_id):
        note = InAppNotification.objects.filter(id=notification_id, user=request.user).first()
        if not note:
            return Response({'detail': 'Notification not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not note.is_read:
            note.is_read = True
            note.save(update_fields=['is_read'])
        return Response(InAppNotificationSerializer(note).data)


class InAppNotificationMarkAllReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        updated = InAppNotification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'message': 'All notifications marked as read.', 'updated': updated})


class AdminNotificationLogListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = NotificationLog.objects.all().order_by('-created_at')
        event = (request.query_params.get('event') or '').strip()
        channel = (request.query_params.get('channel') or '').strip()
        status_filter = (request.query_params.get('status') or '').strip()
        if event:
            qs = qs.filter(event=event)
        if channel:
            qs = qs.filter(channel=channel)
        if status_filter:
            qs = qs.filter(status=status_filter)
        limit = min(int(request.query_params.get('limit') or 100), 500)
        items = list(qs[:limit])
        return Response({
            'count': len(items),
            'results': NotificationLogSerializer(items, many=True).data,
        })


class AdminChannelConfigListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = NotificationChannelConfig.objects.all().order_by('event', 'channel')
        return Response({
            'count': qs.count(),
            'results': NotificationChannelConfigSerializer(qs, many=True).data,
        })


class AdminChannelConfigDetailView(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, config_id):
        row = NotificationChannelConfig.objects.filter(id=config_id).first()
        if not row:
            return Response({'detail': 'Config not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = NotificationChannelConfigSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AdminNotificationTemplateListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = NotificationTemplate.objects.all().order_by('event', 'channel')
        return Response({
            'count': qs.count(),
            'results': NotificationTemplateSerializer(qs, many=True).data,
        })


class AdminNotificationTemplateDetailView(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, template_id):
        row = NotificationTemplate.objects.filter(id=template_id).first()
        if not row:
            return Response({'detail': 'Template not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = NotificationTemplateSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AdminInAppNotificationListView(APIView):
    """Staff sees their own admin in-app feed (ownership enforced)."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = InAppNotification.objects.filter(user=request.user).order_by('-created_at')
        limit = min(int(request.query_params.get('limit') or 50), 200)
        items = list(qs[:limit])
        unread_count = InAppNotification.objects.filter(user=request.user, is_read=False).count()
        return Response({
            'unread_count': unread_count,
            'count': len(items),
            'results': InAppNotificationSerializer(items, many=True).data,
        })
