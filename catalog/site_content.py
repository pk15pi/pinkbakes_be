"""Admin-managed site content (gallery, testimonials) and delivery time slots.

Public endpoints are read-only lists of active rows (no pagination, small tables).
Admin endpoints return {"count", "results"} like the other admin config views.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DeliverySlot, GalleryItem, Testimonial
from .throttles import CATALOG_READ_THROTTLES

# How far ahead customers may book a delivery slot.
MAX_SLOT_DAYS_AHEAD = 30


class IsStaffUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class GalleryItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = GalleryItem
        fields = ['id', 'image_url', 'alt_text', 'caption', 'sort_order', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_image_url(self, value):
        value = (value or '').strip()
        if not value.lower().startswith('https://'):
            raise serializers.ValidationError('Image URL must start with https://.')
        return value


class TestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Testimonial
        fields = ['id', 'name', 'text', 'tag', 'rating', 'sort_order', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('Rating must be between 1 and 5.')
        return value


class DeliverySlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliverySlot
        fields = ['id', 'label', 'start_time', 'end_time', 'sort_order', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        start = attrs.get('start_time', getattr(self.instance, 'start_time', None))
        end = attrs.get('end_time', getattr(self.instance, 'end_time', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'end_time': 'End time must be after start time.'})
        return attrs


class SlotError(ValueError):
    pass


def resolve_delivery_slot(data):
    """Validate optional checkout fields delivery_slot_id + delivery_date.

    Returns kwargs for Order.objects.create (empty when no slot was chosen).
    Raises SlotError with a customer-facing message.
    """
    slot_id = data.get('delivery_slot_id')
    raw_date = (str(data.get('delivery_date') or '')).strip()
    if slot_id in (None, '', 0, '0'):
        if raw_date:
            raise SlotError('Choose a delivery time slot for the selected date.')
        return {}
    try:
        slot = DeliverySlot.objects.filter(id=int(slot_id), is_active=True).first()
    except (TypeError, ValueError):
        slot = None
    if not slot:
        raise SlotError('Selected delivery slot is not available.')
    if not raw_date:
        raise SlotError('Choose a delivery date for the selected slot.')
    try:
        from datetime import date as _date
        delivery_date = _date.fromisoformat(raw_date)
    except ValueError:
        raise SlotError('Delivery date is invalid.')
    now = timezone.localtime(timezone=ZoneInfo(getattr(settings, 'STORE_TIME_ZONE', '') or 'Asia/Kolkata'))
    today = now.date()
    if delivery_date < today or delivery_date > today + timedelta(days=MAX_SLOT_DAYS_AHEAD):
        raise SlotError(f'Delivery date must be within the next {MAX_SLOT_DAYS_AHEAD} days.')
    if delivery_date == today and slot.end_time <= now.time():
        raise SlotError('That delivery slot has already passed today. Pick a later slot or date.')
    return {
        'delivery_date': delivery_date,
        'delivery_slot': slot,
        'delivery_slot_label': slot.label[:80],
    }


def _public_list(model, serializer_class):
    class _View(APIView):
        permission_classes = [permissions.AllowAny]
        throttle_classes = CATALOG_READ_THROTTLES

        def get(self, request):
            rows = model.objects.filter(is_active=True)
            return Response(serializer_class(rows, many=True).data, status=status.HTTP_200_OK)

    return _View


def _admin_list(model, serializer_class):
    class _View(APIView):
        permission_classes = [IsStaffUser]

        def get(self, request):
            rows = model.objects.all()
            return Response({'count': rows.count(), 'results': serializer_class(rows, many=True).data})

        def post(self, request):
            serializer = serializer_class(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(serializer_class(serializer.save()).data, status=status.HTTP_201_CREATED)

    return _View


def _admin_detail(model, serializer_class):
    class _View(APIView):
        permission_classes = [IsStaffUser]

        def _get(self, pk):
            return model.objects.filter(pk=pk).first()

        def get(self, request, pk):
            obj = self._get(pk)
            if not obj:
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
            return Response(serializer_class(obj).data)

        def patch(self, request, pk):
            obj = self._get(pk)
            if not obj:
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
            serializer = serializer_class(obj, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            return Response(serializer_class(serializer.save()).data)

        def delete(self, request, pk):
            obj = self._get(pk)
            if not obj:
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
            obj.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

    return _View


PublicGalleryView = _public_list(GalleryItem, GalleryItemSerializer)
PublicTestimonialView = _public_list(Testimonial, TestimonialSerializer)
PublicDeliverySlotView = _public_list(DeliverySlot, DeliverySlotSerializer)
AdminGalleryListView = _admin_list(GalleryItem, GalleryItemSerializer)
AdminGalleryDetailView = _admin_detail(GalleryItem, GalleryItemSerializer)
AdminTestimonialListView = _admin_list(Testimonial, TestimonialSerializer)
AdminTestimonialDetailView = _admin_detail(Testimonial, TestimonialSerializer)
AdminDeliverySlotListView = _admin_list(DeliverySlot, DeliverySlotSerializer)
AdminDeliverySlotDetailView = _admin_detail(DeliverySlot, DeliverySlotSerializer)
