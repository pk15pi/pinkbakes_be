"""Admin operational control-center helpers and endpoints.

Extends existing admin APIs — does not replace catalog.views or reporting.
"""
from __future__ import annotations

import csv
import io

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.models import User
from django.db.models import Count, F, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .admin_reporting import AdminReportingService
from .models import (
    AdminActivity,
    Coupon,
    DeliveryLocation,
    DeliverySettings,
    Employee,
    Order,
    OrderStatusHistory,
    Payment,
    Product,
    Refund,
    Review,
)
from .serializers import (
    EmployeeSerializer,
    OrderSerializer,
    ReviewSerializer,
)

# Reuse the same staff gate as catalog.views.IsAdminUser
from rest_framework import permissions


class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request.user, 'is_staff', False))


# Canonical forward transitions (cancellation uses dedicated cancel endpoint).
ORDER_STATUS_TRANSITIONS = {
    'PENDING': {'ORDER_CONFIRMED'},
    'ORDER_CONFIRMED': {'PREPARING'},
    'PREPARING': {'PACKING'},
    'PACKING': {'READY_FOR_DELIVERY'},
    'READY_FOR_DELIVERY': {'DELIVERY_BOY_ASSIGNED'},
    'DELIVERY_BOY_ASSIGNED': {'OUT_FOR_DELIVERY'},
    'OUT_FOR_DELIVERY': {'DELIVERED'},
    'DELIVERED': set(),
    'CANCELLED': set(),
}


def validate_order_status_transition(current: str, new_status: str) -> tuple[bool, str]:
    if new_status == current:
        return False, 'Order is already in this status.'
    if new_status == 'CANCELLED':
        return False, 'Use the cancel endpoint to cancel orders.'
    allowed = ORDER_STATUS_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        return False, (
            f'Invalid status transition from {current} to {new_status}. '
            f'Allowed: {", ".join(sorted(allowed)) or "none"}.'
        )
    return True, ''


def log_admin_activity(user, action, entity_type='', entity_id=None, description='', request=None):
    """Safe metadata-only audit log. Never store secrets."""
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    ip = None
    if request is not None:
        ip = request.META.get('REMOTE_ADDR')
    return AdminActivity.objects.create(
        admin_user=user,
        action=action,
        entity_type=entity_type or '',
        entity_id=entity_id,
        description=(description or '')[:2000],
        ip_address=ip,
    )


def paginate_queryset(qs, request, default_page_size=25, max_page_size=100):
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query_params.get('page_size', default_page_size))
    except (TypeError, ValueError):
        page_size = default_page_size
    page_size = max(1, min(page_size, max_page_size))
    total = qs.count()
    start = (page - 1) * page_size
    rows = list(qs[start:start + page_size])
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return rows, {
        'count': total,
        'page': page,
        'page_size': page_size,
        'total_pages': total_pages,
    }


def _csv_response(filename: str, headers: list, rows: list):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


class AdminDashboardMetricsView(APIView):
    """Single aggregation endpoint for the admin overview KPIs."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        from .cache_utils import ADMIN_DASHBOARD_TTL, admin_dashboard_cache_key

        preset = (request.query_params.get('preset') or 'today').strip() or 'today'
        from_date = (request.query_params.get('from_date') or '').strip() or None
        to_date = (request.query_params.get('to_date') or '').strip() or None
        cache_key = admin_dashboard_cache_key(preset, from_date, to_date)
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

        start, end = AdminReportingService.parse_date_range(from_date, to_date, preset)

        orders_in_range = Order.objects.filter(created_at__gte=start, created_at__lte=end)
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # One grouped query for live status tallies instead of N count() calls.
        status_rows = (
            Order.objects.values('status')
            .annotate(c=Count('id'))
        )
        status_map = {row['status']: row['c'] for row in status_rows}
        order_counts = {
            'today': Order.objects.filter(created_at__gte=today_start).count(),
            'in_range': orders_in_range.count(),
            'pending': status_map.get('PENDING', 0),
            'order_confirmed': status_map.get('ORDER_CONFIRMED', 0),
            'preparing': status_map.get('PREPARING', 0),
            'packing': status_map.get('PACKING', 0),
            'ready_for_delivery': status_map.get('READY_FOR_DELIVERY', 0),
            'delivery_boy_assigned': status_map.get('DELIVERY_BOY_ASSIGNED', 0),
            'out_for_delivery': status_map.get('OUT_FOR_DELIVERY', 0),
            'delivered': status_map.get('DELIVERED', 0),
            'cancelled': status_map.get('CANCELLED', 0),
        }

        payments_qs = Payment.objects.filter(created_at__gte=start, created_at__lte=end)
        paid_like = payments_qs.filter(status__in=['paid', 'refund_pending', 'refunded', 'partially_refunded'])
        gross_sales = float(paid_like.aggregate(total=Sum('amount'))['total'] or 0)
        refunds_pending = Refund.objects.filter(status__in=['requested', 'pending', 'processing']).count()
        refunds_completed_qs = Refund.objects.filter(status='completed', created_at__gte=start, created_at__lte=end)
        refunds_completed_amount = float(refunds_completed_qs.aggregate(total=Sum('amount'))['total'] or 0)
        refunds_completed_count = refunds_completed_qs.count()

        paid_orders = Order.objects.filter(
            created_at__gte=start, created_at__lte=end,
            payment_status__in=['paid', 'refunded'],
        )
        discounts = float(paid_orders.aggregate(total=Sum('discount_amount'))['total'] or 0)
        coupon_discounts = float(paid_orders.aggregate(total=Sum('coupon_discount_amount'))['total'] or 0)
        delivery_charges = float(paid_orders.aggregate(total=Sum('delivery_fee'))['total'] or 0)
        net_sales = gross_sales - refunds_completed_amount

        payment_counts = {
            'successful': payments_qs.filter(status__in=['paid', 'refund_pending', 'refunded', 'partially_refunded']).count(),
            'pending': payments_qs.filter(status__in=['created', 'pending', 'authorized']).count(),
            'failed': payments_qs.filter(status='failed').count(),
            'refunds_pending': refunds_pending,
            'refunds_completed': refunds_completed_count,
        }

        out_of_stock = Product.objects.filter(Q(availability='out_of_stock') | Q(available_quantity=0)).count()
        # Prefer availability flag; also catch threshold breaches without Python loop.
        low_stock_count = Product.objects.filter(
            available_quantity__gt=0
        ).filter(
            Q(availability='low_stock') | Q(available_quantity__lte=F('low_stock_threshold'))
        ).count()
        products = {
            'active': Product.objects.filter(is_active=True, status='published').count(),
            'out_of_stock': out_of_stock,
            'low_stock': low_stock_count,
        }

        customers = {
            'total': User.objects.filter(is_staff=False).count(),
            'new_in_range': User.objects.filter(is_staff=False, date_joined__gte=start, date_joined__lte=end).count(),
        }

        review_rows = Review.objects.values('status').annotate(c=Count('id'))
        review_map = {row['status']: row['c'] for row in review_rows}
        reviews = {
            'pending': review_map.get('pending', 0),
            'approved': review_map.get('approved', 0),
            'rejected': review_map.get('rejected', 0),
        }

        now = timezone.now()
        active_coupons = Coupon.objects.filter(is_active=True).filter(
            Q(start_at__isnull=True) | Q(start_at__lte=now)
        ).filter(
            Q(end_at__isnull=True) | Q(end_at__gte=now)
        )
        coupons = {
            'active': active_coupons.count(),
            'usage_count': int(Coupon.objects.aggregate(total=Sum('total_used'))['total'] or 0),
        }

        delivery = {
            'out_for_delivery': order_counts['out_for_delivery'],
            'assigned': order_counts['delivery_boy_assigned'],
            'ready_for_delivery': order_counts['ready_for_delivery'],
            'active_employees': Employee.objects.filter(status__in=['ACTIVE', 'AVAILABLE', 'BUSY']).count(),
            'busy_employees': Employee.objects.filter(status='BUSY').count(),
        }

        sales_summary = {
            'gross_sales': gross_sales,
            'discounts': discounts,
            'coupon_discounts': coupon_discounts,
            'delivery_charges': delivery_charges,
            'refunds': refunds_completed_amount,
            'net_sales': net_sales,
            'currency': 'INR',
            'definition': (
                'gross_sales = sum of paid/refund* payment amounts in range; '
                'refunds = completed refund amounts in range; '
                'net_sales = gross_sales - refunds; '
                'discounts/coupon_discounts/delivery_charges from paid/refunded orders in range.'
            ),
        }

        payload = {
            'preset': preset,
            'from': start.isoformat(),
            'to': end.isoformat(),
            'orders': order_counts,
            'payments': payment_counts,
            'products': products,
            'customers': customers,
            'reviews': reviews,
            'coupons': coupons,
            'delivery': delivery,
            'sales_summary': sales_summary,
            'revenue': net_sales,
            'pending_orders': order_counts['pending'] + order_counts['order_confirmed'],
            'low_stock': products['low_stock'],
            'active_coupons': coupons['active'],
        }
        # Short TTL only — overview KPIs, not a substitute for live payment/order reads.
        cache.set(cache_key, payload, ADMIN_DASHBOARD_TTL)
        return Response(payload, status=status.HTTP_200_OK)

class AdminOrderDetailView(APIView):
    """Full order detail for admin: customer, items, address, coupon, payment, refund, employee, history."""
    permission_classes = [IsAdminUser]

    def get(self, request, order_id):
        order = (
            Order.objects.filter(id=order_id)
            .select_related('user', 'delivery_employee', 'coupon', 'delivery_zone', 'address', 'cancelled_by')
            .prefetch_related('items', 'status_history', 'payments', 'refunds')
            .first()
        )
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        data = OrderSerializer(order, context={'request': request}).data
        payments = []
        for payment in order.payments.all().order_by('-created_at'):
            payments.append({
                'id': payment.id,
                'amount': str(payment.amount),
                'currency': payment.currency,
                'status': payment.status,
                'gateway': payment.gateway,
                'gateway_order_id': payment.gateway_order_id,
                'gateway_payment_id': payment.gateway_payment_id,
                'payment_method': payment.payment_method,
                'failure_reason': payment.failure_reason,
                'created_at': payment.created_at,
                'paid_at': payment.paid_at,
            })
        latest_location = (
            DeliveryLocation.objects.filter(order=order).order_by('-timestamp').first()
        )
        data['payments'] = payments
        data['latest_location'] = None
        if latest_location:
            data['latest_location'] = {
                'latitude': str(latest_location.latitude),
                'longitude': str(latest_location.longitude),
                'accuracy': latest_location.accuracy,
                'timestamp': latest_location.timestamp,
            }
        return Response(data, status=status.HTTP_200_OK)


class AdminCustomerListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = User.objects.filter(is_staff=False).select_related('profile').order_by('-date_joined')
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(username__icontains=search)
                | Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(profile__mobile_number__icontains=search)
            )
        is_active = (request.query_params.get('is_active') or '').strip().lower()
        if is_active in ('1', 'true', 'yes'):
            qs = qs.filter(is_active=True)
        elif is_active in ('0', 'false', 'no'):
            qs = qs.filter(is_active=False)

        rows, meta = paginate_queryset(qs, request)
        results = []
        for user in rows:
            profile = getattr(user, 'profile', None)
            order_agg = Order.objects.filter(user=user).aggregate(
                order_count=Count('id'),
                total_purchase=Sum('total_amount', filter=Q(payment_status__in=['paid', 'refunded'])),
            )
            results.append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_active': user.is_active,
                'date_joined': user.date_joined,
                'mobile_number': getattr(profile, 'mobile_number', '') if profile else '',
                'is_verified': bool(getattr(profile, 'is_verified', False)) if profile else False,
                'email_verified': bool(getattr(profile, 'email_verified', False)) if profile else False,
                'mobile_verified': bool(getattr(profile, 'mobile_verified', False)) if profile else False,
                'order_count': order_agg['order_count'] or 0,
                'total_purchase': float(order_agg['total_purchase'] or 0),
            })
        return Response({**meta, 'results': results}, status=status.HTTP_200_OK)


class AdminCustomerDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, user_id):
        user = User.objects.filter(id=user_id, is_staff=False).select_related('profile').first()
        if not user:
            return Response({'detail': 'Customer not found.'}, status=status.HTTP_404_NOT_FOUND)
        profile = getattr(user, 'profile', None)
        orders = Order.objects.filter(user=user).order_by('-created_at')[:50]
        reviews = Review.objects.filter(user=user).select_related('product').order_by('-created_at')[:50]
        addresses = []
        try:
            for addr in user.addresses.all().order_by('-is_default', '-updated_at')[:20]:
                addresses.append({
                    'id': addr.id,
                    'full_name': addr.full_name,
                    'mobile_number': addr.mobile_number,
                    'address_line_1': addr.address_line_1,
                    'address_line_2': addr.address_line_2,
                    'city': addr.city,
                    'state': addr.state,
                    'postal_code': addr.postal_code,
                    'address_type': addr.address_type,
                    'is_default': addr.is_default,
                })
        except Exception:
            addresses = []
        order_agg = Order.objects.filter(user=user).aggregate(
            order_count=Count('id'),
            total_purchase=Sum('total_amount', filter=Q(payment_status__in=['paid', 'refunded'])),
        )
        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_active': user.is_active,
            'date_joined': user.date_joined,
            'mobile_number': getattr(profile, 'mobile_number', '') if profile else '',
            'is_verified': bool(getattr(profile, 'is_verified', False)) if profile else False,
            'email_verified': bool(getattr(profile, 'email_verified', False)) if profile else False,
            'mobile_verified': bool(getattr(profile, 'mobile_verified', False)) if profile else False,
            'order_count': order_agg['order_count'] or 0,
            'total_purchase': float(order_agg['total_purchase'] or 0),
            'orders': OrderSerializer(orders, many=True, context={'request': request}).data,
            'reviews': ReviewSerializer(reviews, many=True).data,
            'addresses': addresses,
        }, status=status.HTTP_200_OK)

    def patch(self, request, user_id):
        """Activate/deactivate customer account only. Never elevate roles."""
        user = User.objects.filter(id=user_id, is_staff=False).first()
        if not user:
            return Response({'detail': 'Customer not found.'}, status=status.HTTP_404_NOT_FOUND)
        if 'is_staff' in request.data or 'is_superuser' in request.data or 'password' in request.data:
            return Response({'detail': 'Role or credential changes are not allowed.'}, status=status.HTTP_400_BAD_REQUEST)
        if 'is_active' not in request.data:
            return Response({'detail': 'Only is_active may be updated.'}, status=status.HTTP_400_BAD_REQUEST)
        new_active = bool(request.data.get('is_active'))
        old_active = user.is_active
        user.is_active = new_active
        user.save(update_fields=['is_active'])
        log_admin_activity(
            request.user,
            'customer_status',
            entity_type='user',
            entity_id=user.id,
            description=f'Customer is_active {old_active} -> {new_active}',
            request=request,
        )
        return Response({'id': user.id, 'is_active': user.is_active}, status=status.HTTP_200_OK)


class AdminActiveDeliveriesView(APIView):
    """Monitor out-for-delivery / assigned orders using existing tracking fields."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = (
            Order.objects.filter(status__in=['DELIVERY_BOY_ASSIGNED', 'OUT_FOR_DELIVERY', 'READY_FOR_DELIVERY'])
            .select_related('delivery_employee')
            .order_by('-updated_at')
        )
        status_filter = (request.query_params.get('status') or '').strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        now = timezone.now()
        results = []
        for order in qs[:200]:
            loc = DeliveryLocation.objects.filter(order=order).order_by('-timestamp').first()
            started = order.delivery_started_at or order.delivery_assigned_at
            duration_minutes = None
            delayed = False
            if started:
                duration_minutes = int((now - started).total_seconds() // 60)
                delayed = duration_minutes > 90
            results.append({
                'id': order.id,
                'order_number': order.order_number,
                'status': order.status,
                'customer_name': order.customer_name,
                'customer_mobile': order.customer_mobile,
                'postal_code': order.postal_code,
                'delivery_employee': EmployeeSerializer(order.delivery_employee).data if order.delivery_employee else None,
                'delivery_assigned_at': order.delivery_assigned_at,
                'delivery_started_at': order.delivery_started_at,
                'duration_minutes': duration_minutes,
                'delayed': delayed,
                'last_known_location': {
                    'latitude': str(loc.latitude),
                    'longitude': str(loc.longitude),
                    'accuracy': loc.accuracy,
                    'timestamp': loc.timestamp,
                } if loc else None,
            })
        return Response({'count': len(results), 'results': results}, status=status.HTTP_200_OK)


class AdminUnassignDeliveryView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, order_id):
        order = Order.objects.filter(id=order_id).select_related('delivery_employee').first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if order.status in ('OUT_FOR_DELIVERY', 'DELIVERED', 'CANCELLED'):
            return Response({'detail': 'Cannot unassign delivery for this order status.'}, status=status.HTTP_400_BAD_REQUEST)
        emp = order.delivery_employee
        order.delivery_employee = None
        order.delivery_assigned_at = None
        if order.status == 'DELIVERY_BOY_ASSIGNED':
            order.status = 'READY_FOR_DELIVERY'
        order.save(update_fields=['delivery_employee', 'delivery_assigned_at', 'status', 'updated_at'])
        if emp and emp.status == 'BUSY' and not emp.orders_assigned.filter(status__in=['DELIVERY_BOY_ASSIGNED', 'OUT_FOR_DELIVERY']).exists():
            emp.status = 'AVAILABLE'
            emp.save(update_fields=['status'])
        OrderStatusHistory.objects.create(
            order=order,
            status=order.status,
            message='Delivery employee unassigned by admin.',
            changed_by=request.user,
        )
        log_admin_activity(
            request.user, 'employee_unassign', entity_type='order', entity_id=order.id,
            description=f'Unassigned delivery from order {order.order_number}', request=request,
        )
        return Response(OrderSerializer(order, context={'request': request}).data, status=status.HTTP_200_OK)


class AdminSettingsStatusView(APIView):
    """Operational settings + configured booleans only — never secrets."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        delivery = DeliverySettings.get_solo()
        email_host = bool(getattr(settings, 'EMAIL_HOST', '') or '')
        email_user = bool(getattr(settings, 'EMAIL_HOST_USER', '') or '')
        email_password = bool(getattr(settings, 'EMAIL_HOST_PASSWORD', '') or '')
        smtp_configured = bool(email_host and email_user and email_password)
        twilio_sid = bool(getattr(settings, 'TWILIO_ACCOUNT_SID', '') or '')
        twilio_token = bool(getattr(settings, 'TWILIO_AUTH_TOKEN', '') or '')
        twilio_from = bool(getattr(settings, 'TWILIO_FROM_NUMBER', '') or '')
        sms_configured = bool(twilio_sid and twilio_token and twilio_from)
        wa_url = bool(getattr(settings, 'WHATSAPP_API_URL', '') or '')
        wa_token = bool(getattr(settings, 'WHATSAPP_TOKEN', '') or '')
        wa_phone = bool(getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', '') or '')
        whatsapp_configured = bool(wa_url and wa_token and wa_phone)
        razorpay_key = bool(getattr(settings, 'RAZORPAY_KEY_ID', '') or '')
        razorpay_secret = bool(getattr(settings, 'RAZORPAY_KEY_SECRET', '') or '')
        payment_configured_flag = bool(razorpay_key and razorpay_secret)

        business = {
            'bakery_latitude': str(delivery.bakery_latitude) if delivery.bakery_latitude is not None else None,
            'bakery_longitude': str(delivery.bakery_longitude) if delivery.bakery_longitude is not None else None,
            'delivery_enabled': delivery.delivery_enabled,
            'default_delivery_charge': str(delivery.default_delivery_charge),
            'free_delivery_threshold': str(delivery.free_delivery_threshold) if delivery.free_delivery_threshold is not None else None,
            'max_delivery_radius_km': str(delivery.max_delivery_radius_km) if delivery.max_delivery_radius_km is not None else None,
            'per_km_charge': str(delivery.per_km_charge) if delivery.per_km_charge is not None else None,
            'contact_email': getattr(settings, 'DEFAULT_FROM_EMAIL', '') or getattr(settings, 'EMAIL_HOST_USER', '') or '',
        }
        return Response({
            'business': business,
            'integrations': {
                'smtp_configured': smtp_configured,
                'sms_configured': sms_configured,
                'whatsapp_configured': whatsapp_configured,
                'payment_configured': payment_configured_flag,
                'payment_gateway': getattr(settings, 'PAYMENT_GATEWAY', 'razorpay'),
                'email_backend': 'smtp' if email_host else 'console',
            },
        }, status=status.HTTP_200_OK)

    def patch(self, request):
        """Persist non-secret operational bakery/delivery settings only."""
        allowed = {
            'bakery_latitude', 'bakery_longitude', 'delivery_enabled',
            'default_delivery_charge', 'free_delivery_threshold',
            'max_delivery_radius_km', 'per_km_charge',
        }
        for key in request.data.keys():
            kl = str(key).lower()
            if 'secret' in kl or 'password' in kl or 'token' in kl or 'api_key' in kl:
                return Response({'detail': 'Secrets cannot be set via admin API.'}, status=status.HTTP_400_BAD_REQUEST)
        delivery = DeliverySettings.get_solo()
        data = {k: v for k, v in request.data.items() if k in allowed}
        if not data:
            return Response({'detail': 'No updatable operational fields provided.'}, status=status.HTTP_400_BAD_REQUEST)
        for key, value in data.items():
            setattr(delivery, key, value)
        delivery.save()
        log_admin_activity(
            request.user, 'settings_update', entity_type='delivery_settings', entity_id=1,
            description=f'Updated settings fields: {", ".join(sorted(data.keys()))}', request=request,
        )
        return self.get(request)


class AdminExportView(APIView):
    """CSV exports for orders/payments/refunds/inventory/customers — no sensitive fields."""
    permission_classes = [IsAdminUser]

    def get(self, request, entity):
        entity = (entity or '').strip().lower()
        if entity == 'orders':
            qs = Order.objects.all().order_by('-created_at')
            status_filter = (request.query_params.get('status') or '').strip()
            if status_filter:
                qs = qs.filter(status=status_filter)
            rows = [
                [o.order_number, o.customer_name, o.customer_email, o.customer_mobile, o.status,
                 o.payment_status, o.total_amount, o.coupon_code, o.created_at.isoformat()]
                for o in qs[:5000]
            ]
            return _csv_response('orders.csv', [
                'order_number', 'customer_name', 'customer_email', 'customer_mobile',
                'status', 'payment_status', 'total_amount', 'coupon_code', 'created_at',
            ], rows)
        if entity == 'payments':
            qs = Payment.objects.select_related('order').order_by('-created_at')[:5000]
            rows = [
                [p.id, p.order.order_number if p.order else '', p.amount, p.currency, p.status, p.gateway,
                 p.gateway_order_id, p.gateway_payment_id, p.payment_method, p.failure_reason,
                 p.created_at.isoformat(), p.paid_at.isoformat() if p.paid_at else '']
                for p in qs
            ]
            return _csv_response('payments.csv', [
                'id', 'order_number', 'amount', 'currency', 'status', 'gateway',
                'gateway_order_id', 'gateway_payment_id', 'payment_method', 'failure_reason',
                'created_at', 'paid_at',
            ], rows)
        if entity == 'refunds':
            qs = Refund.objects.select_related('order', 'payment').order_by('-created_at')[:5000]
            rows = [
                [r.id, r.order.order_number, r.amount, r.currency, r.status, r.gateway_refund_id,
                 r.initiated_by_type, r.reason, r.failure_reason, r.created_at.isoformat()]
                for r in qs
            ]
            return _csv_response('refunds.csv', [
                'id', 'order_number', 'amount', 'currency', 'status', 'gateway_refund_id',
                'initiated_by_type', 'reason', 'failure_reason', 'created_at',
            ], rows)
        if entity == 'inventory':
            qs = Product.objects.all().order_by('name')
            rows = [
                [p.id, p.name, p.category, p.available_quantity, p.reserved_quantity, p.sold_quantity,
                 p.low_stock_threshold, p.availability, p.is_active, p.status]
                for p in qs
            ]
            return _csv_response('inventory.csv', [
                'id', 'name', 'category', 'available_quantity', 'reserved_quantity', 'sold_quantity',
                'low_stock_threshold', 'availability', 'is_active', 'status',
            ], rows)
        if entity == 'customers':
            qs = User.objects.filter(is_staff=False).select_related('profile').order_by('-date_joined')[:5000]
            rows = []
            for u in qs:
                profile = getattr(u, 'profile', None)
                rows.append([
                    u.id, u.username, u.email, u.first_name, u.last_name, u.is_active,
                    getattr(profile, 'mobile_number', '') if profile else '',
                    bool(getattr(profile, 'is_verified', False)) if profile else False,
                    u.date_joined.isoformat(),
                ])
            return _csv_response('customers.csv', [
                'id', 'username', 'email', 'first_name', 'last_name', 'is_active',
                'mobile_number', 'is_verified', 'date_joined',
            ], rows)
        return Response({'detail': 'Unknown export entity.'}, status=status.HTTP_404_NOT_FOUND)
