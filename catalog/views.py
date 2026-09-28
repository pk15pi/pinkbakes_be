import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings
from django.http import RawPostDataException
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Avg, F, Q, Sum
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .admin_reporting import AdminReportingService
from . import coupons as coupon_service
from . import delivery as delivery_service
from . import inventory as inventory_service
from .coupons import CouponError
from .delivery import DeliveryError
from .inventory import InsufficientStock, InventoryError
from .admin_ops import (
    log_admin_activity,
    paginate_queryset,
    validate_order_status_transition,
)
from .models import AdminActivity, Coupon, CouponRedemption, DeliveryLocation, DeliverySettings, DeliveryZone, Employee, InventoryTransaction, Order, OrderItem, OrderStatusHistory, Payment, Product, ProductView, Refund, Review
from .serializers import CouponRedemptionSerializer, CouponSerializer, DeliveryLocationSerializer, DeliverySettingsSerializer, DeliveryZoneSerializer, EmployeeSerializer, OrderSerializer, PaymentSerializer, ProductSerializer, RefundSerializer, ReviewSerializer
from notifications import events as notification_events
from notifications.service import (
    notify as notify_event,
    notify_order_cancelled,
    notify_order_confirmed,
    notify_payment_success,
    notify_staff_new_order,
    tracking_url_for_order,
)


class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request.user, 'is_staff', False))


class AdminLoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        username = (request.data.get('username') or request.data.get('email') or '').strip()
        password = request.data.get('password')

        if not username or not password:
            return Response({'detail': 'Username/email and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = None
        if '@' in username:
            user = User.objects.filter(email__iexact=username).first()
        if user is None:
            user = authenticate(username=username, password=password)
        if user is None and '@' not in username:
            user = User.objects.filter(username__iexact=username).first()
            if user and not user.check_password(password):
                user = None

        if not user or not user.is_staff:
            return Response({'detail': 'Invalid admin credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        login(request, user)
        token, _ = Token.objects.get_or_create(user=user)
        AdminActivity.objects.create(
            admin_user=user,
            action='login',
            entity_type='admin',
            entity_id=user.id,
            description='Admin login successful.',
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        return Response({
            'message': 'Admin login successful.',
            'token': token.key,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_staff': user.is_staff,
            },
        }, status=status.HTTP_200_OK)


class AdminLogoutView(APIView):
    def post(self, request):
        if request.user.is_authenticated:
            logout(request)
        return Response({'message': 'Admin logged out successfully.'}, status=status.HTTP_200_OK)


class PaymentService:
    @staticmethod
    def build_signature(order_id, payment_id, secret):
        payload = f"{order_id}|{payment_id}".encode()
        return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    @staticmethod
    def verify_signature(order_id, payment_id, signature, secret):
        expected = PaymentService.build_signature(order_id, payment_id, secret)
        return hmac.compare_digest(expected, signature or '')

    @staticmethod
    def calculate_order_amount(items):
        subtotal = Decimal('0')
        for item in items:
            product = Product.objects.filter(id=item['id'], is_active=True).first()
            if not product:
                raise ValueError(f'Product #{item["id"]} is unavailable.')
            quantity = int(item.get('quantity', 1))
            if quantity <= 0:
                raise ValueError('Quantity must be greater than zero.')
            subtotal += (product.discounted_price * quantity)
        return subtotal.quantize(Decimal('0.01'))

    @staticmethod
    def ensure_valid_cart(items):
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Order items must be objects.')
            product_id = item.get('id')
            quantity = item.get('quantity', 1)
            if product_id is None:
                raise ValueError('Each cart item needs an id and quantity.')
            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                raise ValueError('Quantity must be a number.')
            if quantity <= 0:
                raise ValueError('Quantity must be greater than zero.')
            product = Product.objects.filter(id=product_id, is_active=True).first()
            if not product:
                raise ValueError(f'Product #{product_id} is unavailable.')
            # Aggregate later for multi-line same product; provisional per-line check.
            available = int(getattr(product, 'available_quantity', 0) or 0)
            if available < quantity or product.availability == 'out_of_stock':
                raise ValueError(
                    f'Only {available} units of "{product.name}" are currently available.'
                )
            normalized.append({
                'product': product,
                'quantity': quantity,
                'unit_price': product.discounted_price,
            })
        # Re-check aggregated quantities under current stock.
        totals = {}
        for row in normalized:
            pid = row['product'].id
            totals[pid] = totals.get(pid, 0) + row['quantity']
        for row in normalized:
            pid = row['product'].id
            available = int(getattr(row['product'], 'available_quantity', 0) or 0)
            if totals[pid] > available:
                raise ValueError(
                    f'Only {available} units of "{row["product"].name}" are currently available.'
                )
        return normalized

    @staticmethod
    def should_use_live_razorpay():
        """Call Razorpay Orders API only when payments are enabled and real keys are configured."""
        if not getattr(settings, 'PAYMENT_ENABLED', False):
            return False
        key_id = (getattr(settings, 'RAZORPAY_KEY_ID', '') or '').strip()
        key_secret = (getattr(settings, 'RAZORPAY_KEY_SECRET', '') or '').strip()
        if not key_id or not key_secret:
            return False
        # Keep local deterministic ids for placeholder/default secrets used in tests and .env.example.
        if key_id in ('rzp_test_default_key',) or key_secret in ('test_razorpay_secret',):
            return False
        return True

    @staticmethod
    def create_razorpay_order(amount_paise, receipt, notes=None):
        """
        Create a Razorpay order when PAYMENT_ENABLED and keys are set.
        Otherwise return a deterministic local gateway_order_id for tests/dev.
        Signature verification still uses RAZORPAY_KEY_SECRET from settings.
        """
        amount_paise = int(amount_paise)
        receipt = str(receipt)[:40]
        notes = notes or {}

        if PaymentService.should_use_live_razorpay():
            import razorpay
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            order = client.order.create({
                'amount': amount_paise,
                'currency': getattr(settings, 'RAZORPAY_CURRENCY', 'INR'),
                'receipt': receipt,
                'notes': notes,
                'payment_capture': 1,
            })
            return order['id']

        return f"order_{receipt}_{int(timezone.now().timestamp())}"

    @staticmethod
    def amount_paise(amount):
        return int((Decimal(amount) * 100).quantize(Decimal('1')))

    @staticmethod
    def mark_payment_paid(payment, gateway_payment_id, signature=None, source='verify', changed_by=None, payment_method=None):
        """
        Idempotent: set Payment paid, Order payment_status=paid, confirm order,
        write OrderStatusHistory once, and send confirmation email once.
        """
        already_paid = payment.status == 'paid'

        update_fields = ['updated_at']
        if gateway_payment_id and payment.gateway_payment_id != gateway_payment_id:
            payment.gateway_payment_id = gateway_payment_id
            update_fields.append('gateway_payment_id')
        if signature and payment.gateway_signature != signature:
            payment.gateway_signature = signature
            update_fields.append('gateway_signature')
        if payment_method and payment.payment_method != payment_method:
            payment.payment_method = payment_method
            update_fields.append('payment_method')
        elif not payment.payment_method:
            payment.payment_method = 'razorpay'
            update_fields.append('payment_method')

        if not already_paid:
            payment.status = 'paid'
            payment.paid_at = timezone.now()
            payment.failure_reason = ''
            update_fields.extend(['status', 'paid_at', 'failure_reason'])

        payment.save(update_fields=list(dict.fromkeys(update_fields)))

        order = payment.order
        if not order:
            return payment

        order_was_unpaid = order.payment_status != 'paid'
        if order_was_unpaid:
            order.payment_status = 'paid'
            # Keep existing status names; only lift PENDING into ORDER_CONFIRMED.
            if order.status in ('PENDING', 'pending'):
                order.status = 'ORDER_CONFIRMED'
            order.save(update_fields=['payment_status', 'status', 'updated_at'])

            has_confirm_history = OrderStatusHistory.objects.filter(
                order=order, status='ORDER_CONFIRMED'
            ).exists()
            if not has_confirm_history:
                OrderStatusHistory.objects.create(
                    order=order,
                    status='ORDER_CONFIRMED',
                    message=f'Payment confirmed via {source} and order confirmed.',
                    changed_by=changed_by,
                )

        # Notify only on first successful transition to paid (idempotent vs verify+webhook).
        if not already_paid and order_was_unpaid:
            try:
                notify_order_confirmed(order)
                notify_payment_success(order, payment)
                notify_staff_new_order(order)
            except Exception:
                # Do not fail payment confirmation if notification delivery fails.
                pass

        # Finalize coupon reservation → redeemed (idempotent; only pending rows).
        try:
            coupon_service.finalize_redemption_for_order(order)
        except Exception:
            pass

        # Convert reserved stock → sold exactly once (idempotent via InventoryTransaction).
        try:
            inventory_service.consume(order.id, user=changed_by)
        except Exception:
            # Stock consume must not roll back a confirmed payment; log-worthy but soft-fail.
            pass

        return payment


    @staticmethod
    def refundable_amount(payment):
        completed = Refund.objects.filter(
            payment=payment, status='completed'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        return (Decimal(payment.amount) - Decimal(completed)).quantize(Decimal('0.01'))

    @staticmethod
    def apply_refund_completion(refund, send_email=True):
        """Mark refund completed and update Payment / Order payment_status. Idempotent."""
        payment = refund.payment
        order = refund.order
        if refund.status != 'completed':
            refund.status = 'completed'
            refund.processed_at = timezone.now()
            refund.failure_reason = ''
            refund.save(update_fields=['status', 'processed_at', 'failure_reason', 'updated_at'])

        remaining = PaymentService.refundable_amount(payment)
        if remaining <= Decimal('0'):
            payment.status = 'refunded'
            if order and order.payment_status != 'refunded':
                order.payment_status = 'refunded'
                order.save(update_fields=['payment_status', 'updated_at'])
        else:
            payment.status = 'partially_refunded'
            # Keep order.payment_status as paid for partial refunds (choices have no partially_refunded).
        payment.save(update_fields=['status', 'updated_at'])

        if send_email and order:
            try:
                notify_event(
                    notification_events.REFUND_COMPLETED,
                    user=getattr(order, 'user', None),
                    email=getattr(order, 'customer_email', None),
                    context={'order': order, 'refund': refund, 'message': f'Refund completed for {order.order_number}.'},
                    idempotency_key=f'refund_completed:{refund.pk}',
                    reference_type='refund',
                    reference_id=str(refund.pk),
                )
            except Exception:
                pass
        return refund

    @staticmethod
    def create_refund(payment, amount=None, reason='', initiated_by=None, initiated_by_type='customer'):
        """
        Create a Refund and call Razorpay when live keys are configured.
        Does NOT hold a DB transaction across the gateway HTTP call.
        Full refund amount defaults to payment.amount (backend-calculated).
        """
        if payment.status not in ('paid', 'refund_pending', 'partially_refunded'):
            raise ValueError('Only paid payments can be refunded.')

        amount = Decimal(amount if amount is not None else payment.amount).quantize(Decimal('0.01'))
        if amount <= Decimal('0'):
            raise ValueError('Refund amount must be greater than zero.')

        refundable = PaymentService.refundable_amount(payment)
        if amount > refundable:
            raise ValueError(f'Refund amount exceeds refundable balance of {refundable}.')

        # Idempotency: block another non-failed refund that would cover the same full remaining amount
        # when a matching open/completed refund already exists for this payment+amount.
        open_or_done = Refund.objects.filter(
            payment=payment,
            amount=amount,
            status__in=['requested', 'pending', 'processing', 'completed'],
        )
        if open_or_done.exists():
            raise ValueError('A refund for this amount is already in progress or completed.')

        order = payment.order
        if not order:
            raise ValueError('Payment is not linked to an order.')

        refund = Refund.objects.create(
            order=order,
            payment=payment,
            user=payment.user,
            gateway=payment.gateway or 'razorpay',
            amount=amount,
            currency=payment.currency or getattr(settings, 'RAZORPAY_CURRENCY', 'INR'),
            reason=(reason or '')[:2000],
            status='requested',
            initiated_by=initiated_by,
            initiated_by_type=initiated_by_type or 'customer',
        )

        # Mark payment refund_pending while gateway processes (keep paid until completion preferred;
        # refund_pending communicates in-flight state without inventing Order statuses).
        if payment.status == 'paid':
            payment.status = 'refund_pending'
            payment.save(update_fields=['status', 'updated_at'])

        try:
            notify_event(
                notification_events.REFUND_REQUESTED,
                user=getattr(order, 'user', None),
                email=getattr(order, 'customer_email', None),
                context={'order': order, 'refund': refund, 'message': f'Refund initiated for {order.order_number}.'},
                idempotency_key=f'refund_requested:{refund.pk}',
                reference_type='refund',
                reference_id=str(refund.pk),
            )
        except Exception:
            pass

        # Gateway call OUTSIDE any caller transaction.
        try:
            if PaymentService.should_use_live_razorpay() and payment.gateway_payment_id:
                import razorpay
                client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
                payload = {
                    'amount': PaymentService.amount_paise(amount),
                    'notes': {
                        'order_id': str(order.id),
                        'order_number': order.order_number,
                        'refund_id': str(refund.id),
                        'reason': (reason or '')[:200],
                    },
                }
                result = client.payment.refund(payment.gateway_payment_id, payload)
                refund.gateway_refund_id = str(result.get('id') or '')
                gateway_status = (result.get('status') or '').lower()
                if gateway_status in ('processed', 'completed'):
                    refund.status = 'completed'
                    refund.processed_at = timezone.now()
                    refund.save(update_fields=['gateway_refund_id', 'status', 'processed_at', 'updated_at'])
                    PaymentService.apply_refund_completion(refund, send_email=True)
                else:
                    refund.status = 'processing'
                    refund.save(update_fields=['gateway_refund_id', 'status', 'updated_at'])
            else:
                # Local/test simulation: complete immediately.
                refund.gateway_refund_id = f"rfnd_sim_{refund.id}_{int(timezone.now().timestamp())}"
                refund.status = 'completed'
                refund.processed_at = timezone.now()
                refund.save(update_fields=['gateway_refund_id', 'status', 'processed_at', 'updated_at'])
                PaymentService.apply_refund_completion(refund, send_email=True)
        except Exception as exc:
            refund.status = 'failed'
            refund.failure_reason = str(exc)[:1000]
            refund.save(update_fields=['status', 'failure_reason', 'updated_at'])
            # On Razorpay failure: Payment stays paid (or revert from refund_pending), Order stays CANCELLED if already set.
            if payment.status == 'refund_pending':
                payment.status = 'paid'
                payment.save(update_fields=['status', 'updated_at'])
            try:
                notify_event(
                    notification_events.REFUND_FAILED,
                    user=getattr(order, 'user', None),
                    email=getattr(order, 'customer_email', None),
                    context={'order': order, 'refund': refund, 'message': f'Refund failed for {order.order_number}.'},
                    idempotency_key=f'refund_failed:{refund.pk}',
                    reference_type='refund',
                    reference_id=str(refund.pk),
                )
                try:
                    notify_event(
                        notification_events.ADMIN_REFUND_FAILED,
                        context={'order': order, 'refund': refund, 'title': 'Refund failed', 'message': f'Refund failed for {order.order_number}'},
                        admin=True,
                        idempotency_key=f'admin_refund_failed:{refund.pk}',
                        reference_type='refund',
                        reference_id=str(refund.pk),
                    )
                except Exception:
                    pass
            except Exception:
                pass

        refund.refresh_from_db()
        payment.refresh_from_db()
        return refund

    @staticmethod
    def sync_refund_from_webhook(refund_entity, event):
        """Idempotently apply refund.processed / refund.failed webhook payloads."""
        gateway_refund_id = str(refund_entity.get('id') or '')
        gateway_payment_id = str(refund_entity.get('payment_id') or '')
        amount_paise = refund_entity.get('amount')
        refund = None
        if gateway_refund_id:
            refund = Refund.objects.filter(gateway_refund_id=gateway_refund_id).first()
        if refund is None and gateway_payment_id:
            payment = Payment.objects.filter(gateway_payment_id=gateway_payment_id).order_by('-created_at').first()
            if payment:
                refund = Refund.objects.filter(
                    payment=payment,
                    status__in=['requested', 'pending', 'processing'],
                ).order_by('-created_at').first()
                if refund and gateway_refund_id and not refund.gateway_refund_id:
                    refund.gateway_refund_id = gateway_refund_id
                    refund.save(update_fields=['gateway_refund_id', 'updated_at'])

        if refund is None:
            return None

        if event in ('refund.processed', 'payment.refunded'):
            if refund.status == 'completed':
                return refund  # idempotent
            if amount_paise is not None:
                try:
                    expected = PaymentService.amount_paise(refund.amount)
                    if int(amount_paise) != expected:
                        # Do not fail hard; store note but still complete if gateway says processed.
                        refund.failure_reason = (refund.failure_reason or '') + f' amount note:{amount_paise}'
                except (TypeError, ValueError):
                    pass
            return PaymentService.apply_refund_completion(refund, send_email=True)

        if event == 'refund.failed':
            if refund.status == 'completed':
                return refund
            refund.status = 'failed'
            notes = refund_entity.get('notes')
            note_reason = notes.get('reason') if isinstance(notes, dict) else None
            refund.failure_reason = str(
                refund_entity.get('error_description')
                or note_reason
                or refund_entity.get('status')
                or 'Refund failed at gateway.'
            )[:1000]
            refund.save(update_fields=['status', 'failure_reason', 'updated_at'])
            payment = refund.payment
            if payment.status == 'refund_pending':
                payment.status = 'paid'
                payment.save(update_fields=['status', 'updated_at'])
            try:
                notify_event(
                    notification_events.REFUND_FAILED,
                    user=getattr(refund.order, 'user', None),
                    email=getattr(refund.order, 'customer_email', None),
                    context={'order': refund.order, 'refund': refund, 'message': f'Refund failed for {refund.order.order_number}.'},
                    idempotency_key=f'refund_failed:{refund.pk}',
                    reference_type='refund',
                    reference_id=str(refund.pk),
                )
            except Exception:
                pass
            return refund

        return refund

    @staticmethod
    def cancel_order(order, actor, reason='', initiated_by_type='customer', allow_admin=False):
        """
        Cancel an order with ownership/eligibility already checked by the view.
        Unpaid: cancel pending payments, no Razorpay refund.
        Paid: create Refund after DB cancel (gateway call outside atomic block).
        Idempotent: raises ValueError if already CANCELLED.
        """
        if order.status == 'CANCELLED':
            raise ValueError('Order is already cancelled.')

        if allow_admin:
            if not order.is_admin_cancellable():
                raise ValueError('This order cannot be cancelled (delivered or already cancelled).')
        else:
            if not order.is_customer_cancellable():
                raise ValueError('This order can no longer be cancelled.')

        reason = (reason or '').strip()[:2000]
        paid_payment = (
            Payment.objects.filter(order=order, status__in=['paid', 'refund_pending', 'partially_refunded'])
            .order_by('-created_at')
            .first()
        )

        with transaction.atomic():
            order.status = 'CANCELLED'
            order.cancellation_reason = reason
            order.cancelled_by = actor
            order.cancelled_at = timezone.now()
            order.save(update_fields=[
                'status', 'cancellation_reason', 'cancelled_by', 'cancelled_at', 'updated_at',
            ])
            OrderStatusHistory.objects.create(
                order=order,
                status='CANCELLED',
                message=reason or f'Order cancelled by {initiated_by_type}.',
                changed_by=actor,
            )
            # Cancel open unpaid payment attempts.
            for prior in Payment.objects.filter(order=order, status__in=['created', 'pending', 'authorized']):
                prior.status = 'cancelled'
                prior.save(update_fields=['status', 'updated_at'])

        try:
            notify_order_cancelled(order, reason=reason)
        except Exception:
            pass

        # Coupons: unpaid cancel voids pending redemption and releases global usage.
        # Per-user usage is not restored by default (COUPON_RESTORE_ON_CANCEL=false).
        if not paid_payment:
            try:
                coupon_service.void_redemption_for_order(order)
            except Exception:
                pass

        # Inventory: unpaid → release reservation; paid → restore sold units (idempotent).
        try:
            if paid_payment:
                inventory_service.restore(order.id, user=actor)
            else:
                inventory_service.release(order.id, user=actor)
        except Exception:
            pass

        refund = None
        if paid_payment:
            refund = PaymentService.create_refund(
                paid_payment,
                amount=paid_payment.amount,
                reason=reason or 'Order cancelled',
                initiated_by=actor,
                initiated_by_type=initiated_by_type,
            )

        order.refresh_from_db()
        return order, refund




def _checkout_contact_fields(request, shipping):
    """Merge request contact fields with shipping snapshot (address may supply name/mobile)."""
    customer_name = (request.data.get("customer_name") or shipping.get("customer_name") or "").strip()
    customer_email = (request.data.get("customer_email") or getattr(request.user, "email", "") or "").strip()
    customer_mobile = (request.data.get("customer_mobile") or shipping.get("customer_mobile") or "").strip()
    missing = []
    if not customer_name:
        missing.append("customer_name")
    if not customer_email:
        missing.append("customer_email")
    if not customer_mobile:
        missing.append("customer_mobile")
    if missing:
        raise DeliveryError(f"Missing required checkout fields: {', '.join(missing)}.")
    return customer_name, customer_email, customer_mobile


def _apply_delivery_totals(subtotal, discount_amount, shipping):
    merchandise_after_coupon = (Decimal(subtotal) - Decimal(discount_amount or 0)).quantize(Decimal("0.01"))
    if merchandise_after_coupon < 0:
        merchandise_after_coupon = Decimal("0.00")
    delivery = delivery_service.require_delivery(
        merchandise_after_coupon,
        shipping["postal_code"],
        latitude=shipping.get("shipping_latitude"),
        longitude=shipping.get("shipping_longitude"),
    )
    totals = coupon_service.apply_totals(subtotal, discount_amount, delivery_fee=delivery["charge"])
    return totals, delivery



class PaymentCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        items = request.data.get('items') or []
        if not isinstance(items, list) or not items:
            return Response({'detail': 'Your cart is empty.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            shipping = delivery_service.resolve_shipping_from_request(request.user, request.data)
            customer_name, customer_email, customer_mobile = _checkout_contact_fields(request, shipping)
        except DeliveryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            order_items = PaymentService.ensure_valid_cart(items)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        subtotal = sum((item['unit_price'] * item['quantity'] for item in order_items), Decimal('0')).quantize(Decimal('0.01'))
        coupon_code = (request.data.get('coupon_code') or '').strip()
        discount_amount = Decimal('0.00')

        try:
            totals, delivery = _apply_delivery_totals(subtotal, discount_amount, shipping)
        except DeliveryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                order_number=f"PB-{timezone.now().strftime('%Y%m%d')}-{timezone.now().strftime('%H%M%S%f')}-{request.user.id}",
                customer_name=customer_name,
                customer_email=customer_email,
                customer_mobile=customer_mobile,
                shipping_address=shipping['shipping_address'],
                shipping_address_2=shipping.get('shipping_address_2') or '',
                landmark=shipping.get('landmark') or '',
                city=shipping['city'],
                state=shipping['state'],
                postal_code=shipping['postal_code'],
                country=shipping['country'],
                shipping_latitude=shipping.get('shipping_latitude'),
                shipping_longitude=shipping.get('shipping_longitude'),
                address=shipping.get('address'),
                delivery_zone_id=delivery.get('zone_id'),
                subtotal_amount=totals['subtotal_amount'],
                discount_amount=totals['discount_amount'],
                delivery_fee=totals['delivery_fee'],
                tax_amount=totals['tax_amount'],
                total_amount=totals['total_amount'],
                notes=(request.data.get('notes') or '').strip(),
                status='PENDING',
                payment_status='pending',
            )

            for item in order_items:
                product = item['product']
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=product.name,
                    product_image=product.main_image,
                    unit_price=item['unit_price'],
                    quantity=item['quantity'],
                    subtotal=(item['unit_price'] * item['quantity']).quantize(Decimal('0.01')),
                )

            if coupon_code:
                try:
                    discount_amount, _coupon = coupon_service.reserve_coupon_for_order(
                        request.user, coupon_code, order_items, order,
                    )
                except CouponError as exc:
                    transaction.set_rollback(True)
                    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    totals, delivery = _apply_delivery_totals(subtotal, discount_amount, shipping)
                except DeliveryError as exc:
                    transaction.set_rollback(True)
                    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                order.subtotal_amount = totals['subtotal_amount']
                order.discount_amount = totals['discount_amount']
                order.delivery_fee = totals['delivery_fee']
                order.tax_amount = totals['tax_amount']
                order.total_amount = totals['total_amount']
                order.delivery_zone_id = delivery.get('zone_id')
                order.save(update_fields=[
                    'subtotal_amount', 'discount_amount', 'delivery_fee', 'tax_amount',
                    'total_amount', 'delivery_zone', 'updated_at',
                ])

            try:
                inventory_service.reserve_order(order, user=request.user)
            except InsufficientStock as exc:
                transaction.set_rollback(True)
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            payable = order.total_amount
            amount_paise = PaymentService.amount_paise(payable)
            try:
                gateway_order_id = PaymentService.create_razorpay_order(
                    amount_paise=amount_paise,
                    receipt=f"pb{order.id}",
                    notes={'order_id': str(order.id), 'order_number': order.order_number},
                )
            except Exception as exc:
                transaction.set_rollback(True)
                return Response({'detail': f'Unable to create payment order: {exc}'}, status=status.HTTP_502_BAD_GATEWAY)

            payment = Payment.objects.create(
                order=order,
                user=request.user,
                gateway='razorpay',
                gateway_order_id=gateway_order_id,
                amount=payable,
                currency=settings.RAZORPAY_CURRENCY,
                status='created',
            )

        return Response({
            'payment_order_id': payment.gateway_order_id,
            'amount': amount_paise,
            'currency': payment.currency,
            'gateway': payment.gateway,
            'key_id': settings.RAZORPAY_KEY_ID,
            'order_id': order.id,
            'payment_id': payment.id,
            'delivery_fee': float(order.delivery_fee),
            'total_amount': float(order.total_amount),
        }, status=status.HTTP_200_OK)


class PaymentVerifyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        order_id = (request.data.get('razorpay_order_id') or '').strip()
        payment_id = (request.data.get('razorpay_payment_id') or '').strip()
        signature = (request.data.get('razorpay_signature') or '').strip()

        if not order_id or not payment_id or not signature:
            return Response({'detail': 'Missing Razorpay payment identifiers.'}, status=status.HTTP_400_BAD_REQUEST)

        payment = Payment.objects.filter(user=request.user, gateway_order_id=order_id).order_by('-created_at').first()
        if not payment:
            return Response({'detail': 'Payment order not found.'}, status=status.HTTP_404_NOT_FOUND)

        if payment.order_id and payment.order.user_id != request.user.id and not request.user.is_staff:
            return Response({'detail': 'You are not allowed to verify this payment.'}, status=status.HTTP_403_FORBIDDEN)

        if not PaymentService.verify_signature(order_id, payment_id, signature, settings.RAZORPAY_KEY_SECRET):
            if payment.status != 'paid':
                payment.status = 'failed'
                payment.failure_reason = 'Invalid gateway signature.'
                payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
            return Response({'detail': 'Payment verification failed.'}, status=status.HTTP_400_BAD_REQUEST)

        expected_amount = PaymentService.amount_paise(payment.amount)
        raw_amount = request.data.get('amount', None)
        if raw_amount is None or raw_amount == '':
            request_amount = expected_amount
        else:
            try:
                request_amount = int(raw_amount)
            except (TypeError, ValueError):
                return Response({'detail': 'Payment amount verification failed.'}, status=status.HTTP_400_BAD_REQUEST)
        if request_amount != expected_amount:
            if payment.status != 'paid':
                payment.status = 'failed'
                payment.failure_reason = 'Gateway amount mismatch.'
                payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
            return Response({'detail': 'Payment amount verification failed.'}, status=status.HTTP_400_BAD_REQUEST)

        # Idempotent success for duplicate verify of an already-paid payment.
        if payment.status == 'paid':
            if payment.gateway_payment_id and payment.gateway_payment_id != payment_id:
                return Response({'detail': 'Payment already settled with a different gateway payment id.'}, status=status.HTTP_400_BAD_REQUEST)
            order = payment.order
            return Response({
                'status': 'paid',
                'message': 'Payment already verified.',
                'order_id': order.id if order else None,
                'payment_id': payment.gateway_payment_id or payment_id,
                'gateway': payment.gateway,
            }, status=status.HTTP_200_OK)

        PaymentService.mark_payment_paid(
            payment,
            gateway_payment_id=payment_id,
            signature=signature,
            source='verify',
            changed_by=request.user,
            payment_method=request.data.get('payment_method', 'razorpay'),
        )
        order = payment.order

        return Response({
            'status': 'paid',
            'message': 'Payment verified successfully.',
            'order_id': order.id if order else None,
            'payment_id': payment.gateway_payment_id,
            'gateway': payment.gateway,
        }, status=status.HTTP_200_OK)


class PaymentWebhookView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        # Read raw body first (before request.data) so HMAC matches the provider payload.
        signature = request.headers.get('X-Razorpay-Signature', '')
        try:
            body_bytes = request.body
            body = body_bytes.decode('utf-8') if isinstance(body_bytes, (bytes, bytearray)) else str(body_bytes)
        except RawPostDataException:
            body = ''
        expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not body or not hmac.compare_digest(expected, signature or ''):
            return Response({'detail': 'Invalid webhook signature.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = json.loads(body) if body else (request.data or {})
        except ValueError:
            payload = request.data
        event = payload.get('event')
        payment_data = payload.get('payload', {}).get('payment', {}).get('entity') or {}
        order_data = payload.get('payload', {}).get('order', {}).get('entity') or {}
        gateway_order_id = order_data.get('id') or payment_data.get('order_id') or ''
        payment_id = payment_data.get('id') or ''

        if event in ('refund.processed', 'refund.failed', 'payment.refunded'):
            refund_entity = payload.get('payload', {}).get('refund', {}).get('entity') or {}
            if not refund_entity and payment_data:
                refund_entity = {
                    'id': '',
                    'payment_id': payment_data.get('id') or '',
                    'amount': payment_data.get('amount_refunded') or payment_data.get('amount'),
                    'status': payment_data.get('status'),
                }
            synced = PaymentService.sync_refund_from_webhook(refund_entity, event)
            if synced is None:
                return Response({'detail': 'Refund record not found for webhook.'}, status=status.HTTP_404_NOT_FOUND)
            return Response({'status': 'ok'}, status=status.HTTP_200_OK)

        if not gateway_order_id and not payment_id:
            return Response({'detail': 'Webhook payload missing payment references.'}, status=status.HTTP_400_BAD_REQUEST)

        payment = Payment.objects.filter(gateway_order_id=gateway_order_id).order_by('-created_at').first() if gateway_order_id else None
        if payment is None and payment_id:
            payment = Payment.objects.filter(gateway_payment_id=payment_id).order_by('-created_at').first()
        if payment is None:
            return Response({'detail': 'Payment record not found for webhook.'}, status=status.HTTP_404_NOT_FOUND)

        if event in ('payment.captured', 'payment.authorized'):
            PaymentService.mark_payment_paid(
                payment,
                gateway_payment_id=payment_id or payment.gateway_payment_id,
                signature=None,
                source='webhook',
                changed_by=None,
                payment_method=payment_data.get('method') or payment.payment_method or 'razorpay',
            )
        elif event == 'payment.failed':
            if payment.status not in ('paid', 'refund_pending', 'refunded', 'partially_refunded'):
                payment.status = 'failed'
                payment.gateway_payment_id = payment_id or payment.gateway_payment_id
                payment.failure_reason = (
                    payment_data.get('error_description')
                    or payment_data.get('error_reason')
                    or 'Payment failed at gateway.'
                )
                payment.save(update_fields=['status', 'gateway_payment_id', 'failure_reason', 'updated_at'])
                if payment.order and payment.order.payment_status != 'paid':
                    payment.order.payment_status = 'failed'
                    payment.order.save(update_fields=['payment_status', 'updated_at'])

        return Response({'status': 'ok'}, status=status.HTTP_200_OK)


class PaymentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, payment_id):
        payment = Payment.objects.filter(id=payment_id, user=request.user).first()
        if not payment:
            return Response({'detail': 'Payment not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_200_OK)


class OrderPaymentListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, order_id):
        order = Order.objects.filter(id=order_id).first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not request.user.is_staff and order.user_id != request.user.id:
            return Response({'detail': 'You are not allowed to view these payment details.'}, status=status.HTTP_403_FORBIDDEN)
        payments = Payment.objects.filter(order=order).order_by('-created_at')
        return Response(PaymentSerializer(payments, many=True).data, status=status.HTTP_200_OK)


class RetryPaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, order_id):
        order = Order.objects.filter(id=order_id, user=request.user).first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        if order.payment_status == 'paid':
            return Response({'detail': 'This order has already been paid.'}, status=status.HTTP_400_BAD_REQUEST)

        prior_payments = Payment.objects.filter(order=order).order_by('-created_at')
        if not prior_payments.exists():
            return Response({'detail': 'No payment record found for this order.'}, status=status.HTTP_404_NOT_FOUND)

        # Cancel prior open attempts without wiping the order (leave failed/paid/refunded as-is).
        for prior in prior_payments.filter(status__in=['created', 'pending', 'authorized']):
            prior.status = 'cancelled'
            prior.save(update_fields=['status', 'updated_at'])

        amount = order.total_amount
        amount_paise = PaymentService.amount_paise(amount)
        try:
            gateway_order_id = PaymentService.create_razorpay_order(
                amount_paise=amount_paise,
                receipt=f"pbr{order.id}",
                notes={'order_id': str(order.id), 'order_number': order.order_number, 'retry': '1'},
            )
        except Exception as exc:
            return Response({'detail': f'Unable to create retry payment order: {exc}'}, status=status.HTTP_502_BAD_GATEWAY)

        payment = Payment.objects.create(
            order=order,
            user=request.user,
            gateway='razorpay',
            gateway_order_id=gateway_order_id,
            amount=amount,
            currency=settings.RAZORPAY_CURRENCY,
            status='created',
        )

        if order.payment_status == 'failed':
            order.payment_status = 'pending'
            order.save(update_fields=['payment_status', 'updated_at'])

        return Response({
            'order_id': order.id,
            'payment_order_id': payment.gateway_order_id,
            'amount': amount_paise,
            'currency': payment.currency,
            'gateway': payment.gateway,
            'key_id': settings.RAZORPAY_KEY_ID,
            'payment_id': payment.id,
        }, status=status.HTTP_200_OK)


class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = Product.objects.filter(is_active=True, status='published')
        category = self.request.query_params.get('category')
        if category and category != 'All Cakes':
            queryset = queryset.filter(category__iexact=category)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(short_description__icontains=search))
        return queryset.order_by('-featured', '-created_at')


class ProductDetailView(generics.RetrieveAPIView):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer
    permission_classes = [permissions.AllowAny]

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        ProductView.objects.create(
            product=instance,
            user=request.user if request.user.is_authenticated else None,
            session_key=request.session.session_key or '',
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class AdminProductListCreateView(generics.ListCreateAPIView):
    queryset = Product.objects.all().order_by('-featured', '-created_at')
    serializer_class = ProductSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        product = serializer.save()
        AdminActivity.objects.create(
            admin_user=self.request.user,
            action='product_create',
            entity_type='product',
            entity_id=product.id,
            description=f"Created product {product.name}",
            ip_address=self.request.META.get('REMOTE_ADDR'),
        )


class AdminProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsAdminUser]

    def perform_update(self, serializer):
        product = serializer.save()
        AdminActivity.objects.create(
            admin_user=self.request.user,
            action='product_update',
            entity_type='product',
            entity_id=product.id,
            description=f"Updated product {product.name}",
            ip_address=self.request.META.get('REMOTE_ADDR'),
        )

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.status = 'archived'
        instance.save(update_fields=['is_active', 'status'])
        AdminActivity.objects.create(
            admin_user=self.request.user,
            action='product_delete',
            entity_type='product',
            entity_id=instance.id,
            description=f"Deleted product {instance.name}",
            ip_address=self.request.META.get('REMOTE_ADDR'),
        )


def update_product_rating(product):
    rating_avg = product.reviews.filter(status='approved').aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
    product.rating = round(float(rating_avg), 1)
    product.save(update_fields=['rating'])
    return product.rating


class ProductReviewListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, product_id):
        product = Product.objects.filter(id=product_id, is_active=True).first()
        if not product:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
        reviews = Review.objects.filter(product=product, status='approved').order_by('-created_at')
        return Response(ReviewSerializer(reviews, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, product_id):
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required to submit a review.'}, status=status.HTTP_401_UNAUTHORIZED)

        product = Product.objects.filter(id=product_id, is_active=True).first()
        if not product:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)

        eligible_items = OrderItem.objects.filter(
            product=product,
            order__user=request.user,
            order__status__iexact='DELIVERED',
        ).order_by('id')

        if not eligible_items.exists():
            return Response({'detail': 'You can review this product only after your order has been delivered.'}, status=status.HTTP_400_BAD_REQUEST)

        order_item_id = request.data.get('order_item')
        if order_item_id:
            eligible_item = eligible_items.filter(id=order_item_id).first()
            if not eligible_item:
                return Response({'detail': 'This product was not part of the delivered order selected for review.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            eligible_item = eligible_items.first()

        if Review.objects.filter(user=request.user, order_item=eligible_item).exists():
            return Response({'detail': 'You have already submitted a review for this product in this order.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ReviewSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        review = Review.objects.create(
            product=product,
            user=request.user,
            order=eligible_item.order,
            order_item=eligible_item,
            name=request.user.get_full_name() or request.user.username,
            rating=int(serializer.validated_data['rating']),
            comment=serializer.validated_data['comment'],
            status='pending',
        )

        update_product_rating(product)
        try:
            notify_event(
                notification_events.REVIEW_SUBMITTED,
                user=request.user,
                context={
                    'review': review,
                    'product': product,
                    'title': 'Review submitted',
                    'message': f'Your review for {product.name} is pending approval.',
                },
                channels=['in_app'],
                reference_type='review',
                reference_id=str(review.pk),
            )
            notify_event(
                notification_events.REVIEW_SUBMITTED,
                context={
                    'review': review,
                    'product': product,
                    'title': 'New review pending',
                    'message': f'New review for {product.name} awaits approval.',
                },
                admin=True,
                channels=['in_app'],
                reference_type='review',
                reference_id=str(review.pk),
            )
        except Exception:
            pass
        return Response({
            'message': 'Review submitted successfully and is pending admin approval.',
            'review': ReviewSerializer(review).data,
            'rating': product.rating,
        }, status=status.HTTP_201_CREATED)


class ReviewDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, review_id):
        review = Review.objects.filter(id=review_id).first()
        if not review:
            raise ValueError('Review not found.')
        return review

    def put(self, request, review_id):
        try:
            review = self.get_object(review_id)
        except ValueError:
            return Response({'detail': 'Review not found.'}, status=status.HTTP_404_NOT_FOUND)

        if review.user_id != request.user.id and not request.user.is_staff:
            return Response({'detail': 'You are not allowed to edit this review.'}, status=status.HTTP_403_FORBIDDEN)

        if review.status == 'approved':
            review.status = 'pending'
            review.approved_by = None
            review.approved_at = None
            review.rejected_at = None
            review.admin_comment = ''

        serializer = ReviewSerializer(review, data=request.data, partial=True, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        product = review.product
        update_product_rating(product)
        return Response({'message': 'Review updated successfully.', 'review': ReviewSerializer(review).data}, status=status.HTTP_200_OK)

    def delete(self, request, review_id):
        try:
            review = self.get_object(review_id)
        except ValueError:
            return Response({'detail': 'Review not found.'}, status=status.HTTP_404_NOT_FOUND)

        if review.user_id != request.user.id and not request.user.is_staff:
            return Response({'detail': 'You are not allowed to delete this review.'}, status=status.HTTP_403_FORBIDDEN)

        product = review.product
        review.delete()
        update_product_rating(product)
        return Response({'message': 'Review deleted successfully.'}, status=status.HTTP_200_OK)


class AdminReviewListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        queryset = Review.objects.select_related('product', 'user', 'order', 'order_item').all().order_by('-created_at')
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status__iexact=status_filter)
        product_id = request.query_params.get('product_id')
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        return Response(ReviewSerializer(queryset, many=True).data, status=status.HTTP_200_OK)


class AdminReviewDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, review_id):
        review = Review.objects.filter(id=review_id).select_related('product', 'user', 'order', 'order_item').first()
        if not review:
            return Response({'detail': 'Review not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ReviewSerializer(review).data, status=status.HTTP_200_OK)


class AdminReviewApproveView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, review_id):
        review = Review.objects.filter(id=review_id).first()
        if not review:
            return Response({'detail': 'Review not found.'}, status=status.HTTP_404_NOT_FOUND)
        if review.status == 'approved':
            return Response({'detail': 'This review has already been approved.'}, status=status.HTTP_400_BAD_REQUEST)

        review.status = 'approved'
        review.admin_comment = (request.data.get('admin_comment') or '').strip() or review.admin_comment
        review.approved_by = request.user
        review.approved_at = timezone.now()
        review.rejected_at = None
        review.save(update_fields=['status', 'admin_comment', 'approved_by', 'approved_at', 'rejected_at', 'updated_at'])
        update_product_rating(review.product)
        try:
            notify_event(
                notification_events.REVIEW_APPROVED,
                user=review.user,
                email=getattr(review.user, 'email', None),
                context={
                    'review': review,
                    'product': review.product,
                    'user_name': review.user.first_name or review.user.username,
                    'product_name': review.product.name if review.product else '',
                    'message': 'Your review was approved.',
                },
                idempotency_key=f'review_approved:{review.pk}',
                reference_type='review',
                reference_id=str(review.pk),
            )
        except Exception:
            pass
        log_admin_activity(
            request.user, 'review_moderate', entity_type='review', entity_id=review.id,
            description=f'Approved review {review.id}', request=request,
        )
        return Response({'message': 'Review approved successfully.', 'review': ReviewSerializer(review).data}, status=status.HTTP_200_OK)


class AdminReviewRejectView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, review_id):
        review = Review.objects.filter(id=review_id).first()
        if not review:
            return Response({'detail': 'Review not found.'}, status=status.HTTP_404_NOT_FOUND)
        if review.status == 'rejected':
            return Response({'detail': 'This review has already been rejected.'}, status=status.HTTP_400_BAD_REQUEST)

        review.status = 'rejected'
        review.admin_comment = (request.data.get('admin_comment') or '').strip()
        review.rejected_at = timezone.now()
        review.approved_by = None
        review.approved_at = None
        review.save(update_fields=['status', 'admin_comment', 'rejected_at', 'approved_by', 'approved_at', 'updated_at'])
        update_product_rating(review.product)
        try:
            notify_event(
                notification_events.REVIEW_REJECTED,
                user=review.user,
                email=getattr(review.user, 'email', None),
                context={
                    'review': review,
                    'product': review.product,
                    'user_name': review.user.first_name or review.user.username,
                    'product_name': review.product.name if review.product else '',
                    'message': 'Your review was not approved.',
                },
                idempotency_key=f'review_rejected:{review.pk}',
                reference_type='review',
                reference_id=str(review.pk),
            )
        except Exception:
            pass
        log_admin_activity(
            request.user, 'review_moderate', entity_type='review', entity_id=review.id,
            description=f'Rejected review {review.id}', request=request,
        )
        return Response({'message': 'Review rejected successfully.', 'review': ReviewSerializer(review).data}, status=status.HTTP_200_OK)


class ProductAssetUploadView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, product_id):
        product = Product.objects.filter(id=product_id).first()
        if not product:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)

        if 'image' in request.data:
            product.main_image = request.data.get('image')
            product.images = list(product.images or [])
            if product.main_image and product.main_image not in product.images:
                product.images.insert(0, product.main_image)
            product.save(update_fields=['main_image', 'images'])

        if 'three_d_model' in request.data or 'three_d_assets' in request.data:
            if 'three_d_model' in request.data:
                product.three_d_model = request.data.get('three_d_model', '')
            if 'three_d_assets' in request.data:
                assets = request.data.get('three_d_assets')
                product.three_d_assets = assets if isinstance(assets, list) else [assets]
            product.save(update_fields=['three_d_model', 'three_d_assets'])

        AdminActivity.objects.create(
            admin_user=request.user,
            action='product_image_upload' if 'image' in request.data else 'product_3d_upload',
            entity_type='product',
            entity_id=product.id,
            description=f"Updated assets for {product.name}",
            ip_address=self.request.META.get('REMOTE_ADDR'),
        )

        return Response(ProductSerializer(product).data, status=status.HTTP_200_OK)


class OrderCheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        items = request.data.get('items') or []
        if not isinstance(items, list) or not items:
            return Response({'detail': 'Your cart is empty.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            shipping = delivery_service.resolve_shipping_from_request(request.user, request.data)
            customer_name, customer_email, customer_mobile = _checkout_contact_fields(request, shipping)
        except DeliveryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            validated = PaymentService.ensure_valid_cart(items)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        subtotal = sum((row['unit_price'] * row['quantity'] for row in validated), Decimal('0')).quantize(Decimal('0.01'))
        coupon_code = (request.data.get('coupon_code') or '').strip()
        discount_amount = Decimal('0.00')

        try:
            totals, delivery = _apply_delivery_totals(subtotal, discount_amount, shipping)
        except DeliveryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                order_number=f"PB-{timezone.now().strftime('%Y%m%d')}-{timezone.now().strftime('%H%M%S%f')}-{request.user.id}",
                customer_name=customer_name,
                customer_email=customer_email,
                customer_mobile=customer_mobile,
                shipping_address=shipping['shipping_address'],
                shipping_address_2=shipping.get('shipping_address_2') or '',
                landmark=shipping.get('landmark') or '',
                city=shipping['city'],
                state=shipping['state'],
                postal_code=shipping['postal_code'],
                country=shipping['country'],
                shipping_latitude=shipping.get('shipping_latitude'),
                shipping_longitude=shipping.get('shipping_longitude'),
                address=shipping.get('address'),
                delivery_zone_id=delivery.get('zone_id'),
                subtotal_amount=totals['subtotal_amount'],
                discount_amount=totals['discount_amount'],
                delivery_fee=totals['delivery_fee'],
                tax_amount=totals['tax_amount'],
                total_amount=totals['total_amount'],
                notes=(request.data.get('notes') or '').strip(),
                status='PENDING',
                payment_status='pending',
            )

            for row in validated:
                product = row['product']
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=product.name,
                    product_image=product.main_image,
                    unit_price=row['unit_price'],
                    quantity=row['quantity'],
                    subtotal=(row['unit_price'] * row['quantity']).quantize(Decimal('0.01')),
                )

            if coupon_code:
                try:
                    discount_amount, _coupon = coupon_service.reserve_coupon_for_order(
                        request.user, coupon_code, validated, order,
                    )
                except CouponError as exc:
                    transaction.set_rollback(True)
                    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    totals, delivery = _apply_delivery_totals(subtotal, discount_amount, shipping)
                except DeliveryError as exc:
                    transaction.set_rollback(True)
                    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                order.subtotal_amount = totals['subtotal_amount']
                order.discount_amount = totals['discount_amount']
                order.delivery_fee = totals['delivery_fee']
                order.tax_amount = totals['tax_amount']
                order.total_amount = totals['total_amount']
                order.delivery_zone_id = delivery.get('zone_id')
                order.save(update_fields=[
                    'subtotal_amount', 'discount_amount', 'delivery_fee', 'tax_amount',
                    'total_amount', 'delivery_zone', 'updated_at',
                ])

            try:
                inventory_service.reserve_order(order, user=request.user)
            except InsufficientStock as exc:
                transaction.set_rollback(True)
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OrderSerializer(order, context={'request': request}).data, status=status.HTTP_201_CREATED)


class OrderListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        orders = Order.objects.filter(user=request.user).prefetch_related('items', 'status_history', 'refunds').order_by('-created_at')
        return Response({
            'count': orders.count(),
            'results': OrderSerializer(orders, many=True, context={'request': request}).data,
        }, status=status.HTTP_200_OK)


class OrderDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, order_id):
        order = Order.objects.filter(id=order_id).select_related('user', 'cancelled_by').prefetch_related('items', 'status_history', 'refunds').first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not request.user.is_staff and order.user_id != request.user.id:
            return Response({'detail': 'You are not allowed to view this order.'}, status=status.HTTP_403_FORBIDDEN)
        return Response(OrderSerializer(order, context={'request': request}).data, status=status.HTTP_200_OK)


class AdminOrderListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        orders = Order.objects.all().select_related('user', 'delivery_employee').prefetch_related('items').order_by('-created_at')

        status_filter = (request.query_params.get('status') or '').strip()
        payment_status = (request.query_params.get('payment_status') or '').strip()
        search = (request.query_params.get('search') or '').strip()
        date_from = (request.query_params.get('date_from') or '').strip()
        date_to = (request.query_params.get('date_to') or '').strip()

        if status_filter:
            orders = orders.filter(status=status_filter)
        if payment_status:
            orders = orders.filter(payment_status=payment_status)
        if search:
            orders = orders.filter(
                Q(order_number__icontains=search)
                | Q(customer_name__icontains=search)
                | Q(customer_email__icontains=search)
                | Q(customer_mobile__icontains=search)
            )
        if date_from:
            orders = orders.filter(created_at__date__gte=date_from)
        if date_to:
            orders = orders.filter(created_at__date__lte=date_to)

        rows, meta = paginate_queryset(orders, request)
        return Response({
            **meta,
            'results': OrderSerializer(rows, many=True, context={'request': request}).data,
        }, status=status.HTTP_200_OK)


class AdminPaymentListView(APIView):
    """List payments for admins without exposing gateway secrets/signatures."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        payments = Payment.objects.select_related('order', 'user').order_by('-created_at')
        status_filter = (request.query_params.get('status') or '').strip()
        search = (request.query_params.get('search') or '').strip()
        if status_filter:
            payments = payments.filter(status=status_filter)
        if search:
            payments = payments.filter(
                Q(gateway_order_id__icontains=search)
                | Q(gateway_payment_id__icontains=search)
                | Q(order__order_number__icontains=search)
                | Q(order__customer_name__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user__email__icontains=search)
            )

        date_from = (request.query_params.get('date_from') or '').strip()
        date_to = (request.query_params.get('date_to') or '').strip()
        if date_from:
            payments = payments.filter(created_at__date__gte=date_from)
        if date_to:
            payments = payments.filter(created_at__date__lte=date_to)

        page_rows, meta = paginate_queryset(payments, request)
        results = []
        for payment in page_rows:
            order = payment.order
            results.append({
                'id': payment.id,
                'order_id': order.id if order else None,
                'order_number': order.order_number if order else None,
                'customer_name': order.customer_name if order else (payment.user.get_full_name() or payment.user.username),
                'customer_email': order.customer_email if order else payment.user.email,
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

        return Response({
            **meta,
            'results': results,
        }, status=status.HTTP_200_OK)


class EmployeeManagementView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, employee_id=None):
        if employee_id is not None:
            employee = Employee.objects.filter(id=employee_id).first()
            if not employee:
                return Response({'detail': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)
            return Response(EmployeeSerializer(employee).data, status=status.HTTP_200_OK)

        employees = Employee.objects.all().order_by('name')
        search = request.query_params.get('search', '').strip()
        status_filter = request.query_params.get('status')
        if search:
            employees = employees.filter(name__icontains=search) | employees.filter(employee_id__icontains=search)
        if status_filter:
            employees = employees.filter(status=status_filter)
        return Response(EmployeeSerializer(employees, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = EmployeeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        employee = serializer.save()
        return Response(EmployeeSerializer(employee).data, status=status.HTTP_201_CREATED)

    def put(self, request, employee_id):
        employee = Employee.objects.filter(id=employee_id).first()
        if not employee:
            return Response({'detail': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = EmployeeSerializer(employee, data=request.data, partial=False)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(EmployeeSerializer(employee).data, status=status.HTTP_200_OK)

    def patch(self, request, employee_id):
        employee = Employee.objects.filter(id=employee_id).first()
        if not employee:
            return Response({'detail': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = EmployeeSerializer(employee, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(EmployeeSerializer(employee).data, status=status.HTTP_200_OK)

    def delete(self, request, employee_id):
        employee = Employee.objects.filter(id=employee_id).first()
        if not employee:
            return Response({'detail': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)
        if employee.orders_assigned.exists():
            employee.status = 'INACTIVE'
            employee.save(update_fields=['status'])
            return Response({'message': 'Employee deactivated instead of being deleted because they are linked to historical orders.'}, status=status.HTTP_200_OK)
        employee.delete()
        return Response({'message': 'Employee deleted successfully.'}, status=status.HTTP_200_OK)


class AdminAssignDeliveryView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, order_id):
        order = Order.objects.filter(id=order_id).select_related('delivery_employee').first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        employee_id = request.data.get('employee_id')
        if not employee_id:
            return Response({'detail': 'Employee is required.'}, status=status.HTTP_400_BAD_REQUEST)

        employee = Employee.objects.filter(id=employee_id).first()
        if not employee:
            return Response({'detail': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)
        if employee.status not in ['ACTIVE', 'AVAILABLE']:
            return Response({'detail': 'Employee is not available for delivery assignments.'}, status=status.HTTP_400_BAD_REQUEST)

        order.delivery_employee = employee
        order.delivery_assigned_at = timezone.now()
        order.status = 'DELIVERY_BOY_ASSIGNED'
        order.save(update_fields=['delivery_employee', 'delivery_assigned_at', 'status', 'updated_at'])

        employee.status = 'BUSY'
        employee.save(update_fields=['status'])

        OrderStatusHistory.objects.create(
            order=order,
            status='DELIVERY_BOY_ASSIGNED',
            message='Delivery boy assigned by admin.',
            changed_by=request.user,
        )
        log_admin_activity(
            request.user, 'employee_assign', entity_type='order', entity_id=order.id,
            description=f'Assigned employee {employee.employee_id} to {order.order_number}',
            request=request,
        )

        try:
            notify_event(
                notification_events.DELIVERY_ASSIGNED,
                user=getattr(order, 'user', None),
                email=getattr(order, 'customer_email', None),
                context={
                    'order': order,
                    'user_name': order.customer_name,
                    'order_number': order.order_number,
                    'tracking_url': tracking_url_for_order(order),
                    'tracking_message': 'You can track your order from your account.',
                    'message': f'Delivery partner assigned for {order.order_number}.',
                },
                idempotency_key=f'delivery_assigned:{order.pk}:{employee.pk}',
                reference_type='order',
                reference_id=str(order.pk),
            )
        except Exception:
            pass

        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)


class AdminOrderStatusUpdateView(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, order_id):
        order = Order.objects.filter(id=order_id).first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        new_status = (request.data.get('status') or '').strip()
        if not new_status:
            return Response({'detail': 'Order status is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if new_status not in dict(Order.STATUS_CHOICES):
            return Response({'detail': 'Invalid order status.'}, status=status.HTTP_400_BAD_REQUEST)

        ok, err = validate_order_status_transition(order.status, new_status)
        if not ok:
            return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)

        previous_status = order.status
        order.status = new_status
        if new_status == 'OUT_FOR_DELIVERY':
            order.delivery_started_at = timezone.now()
        elif new_status == 'DELIVERED':
            order.delivery_completed_at = timezone.now()
            if order.delivery_employee:
                order.delivery_employee.status = 'AVAILABLE'
                order.delivery_employee.save(update_fields=['status'])
        order.save(update_fields=['status', 'delivery_started_at', 'delivery_completed_at', 'updated_at'])

        OrderStatusHistory.objects.create(
            order=order,
            status=new_status,
            message=request.data.get('message', f'Order status updated to {new_status}.'),
            changed_by=request.user,
        )
        log_admin_activity(
            request.user, 'order_status', entity_type='order', entity_id=order.id,
            description=f'Status {previous_status} -> {new_status} for {order.order_number}',
            request=request,
        )

        try:
            ctx = {
                'order': order,
                'user_name': order.customer_name,
                'order_number': order.order_number,
                'tracking_url': tracking_url_for_order(order),
                'message': f'Order {order.order_number} status: {new_status}.',
            }
            if new_status == 'OUT_FOR_DELIVERY':
                notify_event(
                    notification_events.ORDER_OUT_FOR_DELIVERY,
                    user=getattr(order, 'user', None),
                    email=getattr(order, 'customer_email', None),
                    context=ctx,
                    idempotency_key=f'out_for_delivery:{order.pk}',
                    reference_type='order',
                    reference_id=str(order.pk),
                )
            elif new_status == 'DELIVERED':
                notify_event(
                    notification_events.DELIVERY_COMPLETED,
                    user=getattr(order, 'user', None),
                    email=getattr(order, 'customer_email', None),
                    context=ctx,
                    idempotency_key=f'delivery_completed:{order.pk}',
                    reference_type='order',
                    reference_id=str(order.pk),
                )
            elif new_status == 'READY_FOR_DELIVERY':
                notify_event(
                    notification_events.ORDER_READY_FOR_DELIVERY,
                    user=getattr(order, 'user', None),
                    email=getattr(order, 'customer_email', None),
                    context=ctx,
                    idempotency_key=f'ready_for_delivery:{order.pk}',
                    reference_type='order',
                    reference_id=str(order.pk),
                )
            else:
                notify_event(
                    notification_events.ORDER_STATUS_CHANGED,
                    user=getattr(order, 'user', None),
                    context=ctx,
                    channels=['in_app'],
                    reference_type='order',
                    reference_id=str(order.pk),
                )
        except Exception:
            pass

        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)


class OrderTrackingView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, order_id):
        order = Order.objects.filter(id=order_id).select_related('delivery_employee').prefetch_related('status_history').first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not request.user.is_staff and order.user_id != request.user.id:
            return Response({'detail': 'You are not allowed to view this order tracking information.'}, status=status.HTTP_403_FORBIDDEN)

        latest_location = order.delivery_locations.order_by('-timestamp').first()
        payload = {
            'order_number': order.order_number,
            'status': order.status,
            'shipping_snapshot': {
                'customer_name': order.customer_name,
                'customer_mobile': order.customer_mobile,
                'shipping_address': order.shipping_address,
                'shipping_address_2': order.shipping_address_2,
                'landmark': getattr(order, 'landmark', '') or '',
                'city': order.city,
                'state': order.state,
                'postal_code': order.postal_code,
                'country': order.country,
                'latitude': float(order.shipping_latitude) if order.shipping_latitude is not None else None,
                'longitude': float(order.shipping_longitude) if order.shipping_longitude is not None else None,
                'delivery_fee': float(order.delivery_fee or 0),
            },
            'status_history': [
                {'status': item.status, 'timestamp': item.created_at.isoformat(), 'message': item.message}
                for item in order.status_history.all()
            ],
            'delivery_employee': None,
            'location': None,
        }

        if order.delivery_employee:
            payload['delivery_employee'] = {
                'employee_id': order.delivery_employee.employee_id,
                'name': order.delivery_employee.name,
                'photo': order.delivery_employee.photo,
                'contact_number': order.delivery_employee.contact_number,
            }

        if latest_location:
            payload['location'] = {
                'latitude': latest_location.latitude,
                'longitude': latest_location.longitude,
                'accuracy': latest_location.accuracy,
                'updated_at': latest_location.timestamp.isoformat(),
            }

        return Response(payload, status=status.HTTP_200_OK)


class DeliveryLocationUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, order_id):
        order = Order.objects.filter(id=order_id).select_related('delivery_employee').first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        employee = order.delivery_employee
        if not employee:
            return Response({'detail': 'This order has no assigned delivery employee.'}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.is_staff and request.user.id != order.user_id:
            return Response({'detail': 'You are not authorized to update this delivery location.'}, status=status.HTTP_403_FORBIDDEN)

        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')
        if latitude is None or longitude is None:
            return Response({'detail': 'Latitude and longitude are required.'}, status=status.HTTP_400_BAD_REQUEST)

        location = DeliveryLocation.objects.create(
            order=order,
            employee=employee,
            latitude=float(latitude),
            longitude=float(longitude),
            accuracy=float(request.data.get('accuracy') or 0),
        )
        return Response(DeliveryLocationSerializer(location).data, status=status.HTTP_201_CREATED)


class AdminReportSummaryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        preset = request.query_params.get('preset', 'custom')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        return Response(AdminReportingService.summary(from_date, to_date, preset), status=status.HTTP_200_OK)


class AdminReportUsersView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response({'users': []}, status=status.HTTP_200_OK)


class AdminReportProductsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response({'products': []}, status=status.HTTP_200_OK)


class AdminReportRevenueView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        preset = request.query_params.get('preset', 'custom')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        sales = AdminReportingService.sales_summary(from_date, to_date, preset)
        if Order.objects.exists():
            sales['total_orders'] = Order.objects.count()
            sales['total_sales'] = float(Order.objects.aggregate(total=Sum('total_amount'))['total'] or 0)
            sales['average_order_value'] = float(Order.objects.aggregate(avg=Avg('total_amount'))['avg'] or 0)
            sales['pending_orders'] = Order.objects.filter(status='PENDING').count()
            sales['completed_orders'] = Order.objects.filter(status='DELIVERED').count()
            sales['note'] = 'Live order totals are available.'
        return Response(sales, status=status.HTTP_200_OK)


class AdminReportReviewsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(AdminReportingService.review_stats(), status=status.HTTP_200_OK)


class AdminReportProductPerformanceView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        preset = request.query_params.get('preset', 'custom')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        return Response(AdminReportingService.product_performance(from_date, to_date, preset), status=status.HTTP_200_OK)


class AdminReportActivityView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        preset = request.query_params.get('preset', 'custom')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        return Response(AdminReportingService.admin_activity(from_date, to_date, preset), status=status.HTTP_200_OK)


class OrderCancelView(APIView):
    """Customer cancel: POST /api/orders/<id>/cancel/"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, order_id):
        order = Order.objects.filter(id=order_id).first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if order.user_id != request.user.id:
            return Response({'detail': 'You are not allowed to cancel this order.'}, status=status.HTTP_403_FORBIDDEN)
        if order.status == 'CANCELLED':
            return Response({'detail': 'Order is already cancelled.'}, status=status.HTTP_409_CONFLICT)

        reason = (request.data.get('reason') or '').strip()
        try:
            order, refund = PaymentService.cancel_order(
                order,
                actor=request.user,
                reason=reason,
                initiated_by_type='customer',
                allow_admin=False,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = OrderSerializer(order, context={'request': request}).data
        if refund is not None:
            payload['refund'] = RefundSerializer(refund).data
        return Response(payload, status=status.HTTP_200_OK)


class AdminOrderCancelView(APIView):
    """Admin cancel: POST /api/admin/orders/<id>/cancel/"""
    permission_classes = [IsAdminUser]

    def post(self, request, order_id):
        order = Order.objects.filter(id=order_id).first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if order.status == 'CANCELLED':
            return Response({'detail': 'Order is already cancelled.'}, status=status.HTTP_409_CONFLICT)

        reason = (request.data.get('reason') or '').strip()
        try:
            order, refund = PaymentService.cancel_order(
                order,
                actor=request.user,
                reason=reason,
                initiated_by_type='admin',
                allow_admin=True,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = OrderSerializer(order, context={'request': request}).data
        if refund is not None:
            payload['refund'] = RefundSerializer(refund).data
        return Response(payload, status=status.HTTP_200_OK)


class AdminOrderRefundView(APIView):
    """Admin partial/full refund without requiring cancel: POST /api/admin/orders/<id>/refund/"""
    permission_classes = [IsAdminUser]

    def post(self, request, order_id):
        order = Order.objects.filter(id=order_id).first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        payment = (
            Payment.objects.filter(order=order, status__in=['paid', 'refund_pending', 'partially_refunded'])
            .order_by('-created_at')
            .first()
        )
        if not payment:
            return Response({'detail': 'No refundable paid payment found for this order.'}, status=status.HTTP_400_BAD_REQUEST)

        raw_amount = request.data.get('amount', None)
        reason = (request.data.get('reason') or '').strip()
        amount = None
        if raw_amount is not None and raw_amount != '':
            try:
                amount = Decimal(str(raw_amount)).quantize(Decimal('0.01'))
            except Exception:
                return Response({'detail': 'Invalid refund amount.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            refund = PaymentService.create_refund(
                payment,
                amount=amount,
                reason=reason or 'Admin refund',
                initiated_by=request.user,
                initiated_by_type='admin',
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'order': OrderSerializer(order, context={'request': request}).data,
            'refund': RefundSerializer(refund).data,
        }, status=status.HTTP_200_OK)


class AdminRefundListView(APIView):
    """Minimal admin refund list: GET /api/admin/refunds/"""
    permission_classes = [IsAdminUser]

    def get(self, request):
        refunds = Refund.objects.select_related('order', 'payment', 'user').order_by('-created_at')
        status_filter = (request.query_params.get('status') or '').strip()
        if status_filter:
            refunds = refunds.filter(status=status_filter)
        order_id = (request.query_params.get('order_id') or '').strip()
        if order_id:
            refunds = refunds.filter(order_id=order_id)
        rows, meta = paginate_queryset(refunds, request)
        return Response({
            **meta,
            'results': RefundSerializer(rows, many=True).data,
        }, status=status.HTTP_200_OK)


class CartValidateView(APIView):
    """Validate cart quantities against live available stock (FE cart is client-side)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        items = request.data.get('items') or []
        if not isinstance(items, list) or not items:
            return Response({'detail': 'Your cart is empty.', 'valid': False, 'items': []}, status=status.HTTP_400_BAD_REQUEST)
        try:
            results, errors = inventory_service.validate_cart_items(items)
        except InventoryError as exc:
            return Response({'detail': str(exc), 'valid': False, 'items': []}, status=status.HTTP_400_BAD_REQUEST)
        valid = not errors
        payload = {
            'valid': valid,
            'items': results,
            'detail': errors[0] if errors else 'Cart quantities are available.',
        }
        return Response(payload, status=status.HTTP_200_OK if valid else status.HTTP_400_BAD_REQUEST)


class AdminInventoryListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = Product.objects.all().order_by('name')
        availability = (request.query_params.get('availability') or '').strip()
        search = (request.query_params.get('search') or '').strip()
        status_filter = (request.query_params.get('status') or '').strip()
        if availability:
            qs = qs.filter(availability=availability)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(category__icontains=search))
        data = ProductSerializer(qs, many=True, context={'request': request}).data
        return Response({'count': qs.count(), 'results': data}, status=status.HTTP_200_OK)


class AdminInventoryDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, product_id):
        product = Product.objects.filter(id=product_id).first()
        if not product:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ProductSerializer(product, context={'request': request}).data, status=status.HTTP_200_OK)


class AdminInventoryAdjustView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, product_id):
        product = Product.objects.filter(id=product_id).first()
        if not product:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)

        action = (request.data.get('action') or 'adjust').strip().lower()
        reason = (request.data.get('reason') or '').strip()
        threshold = request.data.get('low_stock_threshold', None)
        try:
            quantity = int(request.data.get('quantity', 0))
        except (TypeError, ValueError):
            return Response({'detail': 'quantity must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)

        if threshold is not None:
            try:
                threshold = int(threshold)
            except (TypeError, ValueError):
                return Response({'detail': 'low_stock_threshold must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            product = inventory_service.adjust(
                product,
                action=action,
                quantity=quantity,
                reason=reason,
                admin=request.user,
                low_stock_threshold=threshold,
            )
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminActivity.objects.create(
            admin_user=request.user,
            action='inventory_adjust',
            entity_type='product',
            entity_id=product.id,
            description=f'Inventory {action} qty={quantity}: {reason}'[:500],
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        return Response(ProductSerializer(product, context={'request': request}).data, status=status.HTTP_200_OK)


class AdminInventoryHistoryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, product_id):
        product = Product.objects.filter(id=product_id).first()
        if not product:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
        txns = InventoryTransaction.objects.filter(product=product).select_related('performed_by').order_by('-created_at')[:200]
        results = [
            {
                'id': t.id,
                'product_id': t.product_id,
                'quantity_change': t.quantity_change,
                'previous_quantity': t.previous_quantity,
                'new_quantity': t.new_quantity,
                'adjustment_type': t.adjustment_type,
                'reason': t.reason,
                'reference_type': t.reference_type,
                'reference_id': t.reference_id,
                'performed_by': t.performed_by_id,
                'performed_by_username': t.performed_by.username if t.performed_by else None,
                'created_at': t.created_at,
            }
            for t in txns
        ]
        return Response({'count': len(results), 'results': results}, status=status.HTTP_200_OK)


class AdminInventoryLowStockView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = Product.objects.filter(
            Q(availability='low_stock') | Q(availability='out_of_stock') | Q(available_quantity__lte=F('low_stock_threshold'))
        ).order_by('available_quantity', 'name')
        data = ProductSerializer(qs, many=True, context={'request': request}).data
        return Response({'count': qs.count(), 'results': data}, status=status.HTTP_200_OK)



class CouponValidateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get('code') or ''
        items = request.data.get('items') or []
        if not isinstance(items, list):
            return Response({'detail': 'items must be a list.'}, status=status.HTTP_400_BAD_REQUEST)
        payload = coupon_service.public_validate_payload(request.user, code, items)
        http_status = status.HTTP_200_OK if payload.get('valid') else status.HTTP_400_BAD_REQUEST
        return Response(payload, status=http_status)


class AdminCouponListCreateView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = Coupon.objects.all().prefetch_related('products').order_by('-created_at')
        active = (request.query_params.get('is_active') or '').strip().lower()
        if active in ('1', 'true', 'yes'):
            qs = qs.filter(is_active=True)
        elif active in ('0', 'false', 'no'):
            qs = qs.filter(is_active=False)
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(code__icontains=search) | Q(name__icontains=search))
        return Response({
            'count': qs.count(),
            'results': CouponSerializer(qs, many=True, context={'request': request}).data,
        }, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = CouponSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        coupon = serializer.save()
        return Response(CouponSerializer(coupon, context={'request': request}).data, status=status.HTTP_201_CREATED)


class AdminCouponDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get_object(self, coupon_id):
        return Coupon.objects.filter(id=coupon_id).prefetch_related('products').first()

    def get(self, request, coupon_id):
        coupon = self.get_object(coupon_id)
        if not coupon:
            return Response({'detail': 'Coupon not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(CouponSerializer(coupon, context={'request': request}).data, status=status.HTTP_200_OK)

    def patch(self, request, coupon_id):
        coupon = self.get_object(coupon_id)
        if not coupon:
            return Response({'detail': 'Coupon not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = CouponSerializer(coupon, data=request.data, partial=True, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        coupon = serializer.save()
        return Response(CouponSerializer(coupon, context={'request': request}).data, status=status.HTTP_200_OK)


class AdminCouponRedemptionsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, coupon_id):
        coupon = Coupon.objects.filter(id=coupon_id).first()
        if not coupon:
            return Response({'detail': 'Coupon not found.'}, status=status.HTTP_404_NOT_FOUND)
        qs = CouponRedemption.objects.filter(coupon=coupon).select_related('user', 'order', 'coupon').order_by('-created_at')
        return Response({
            'count': qs.count(),
            'results': CouponRedemptionSerializer(qs, many=True).data,
        }, status=status.HTTP_200_OK)


class DeliveryQuoteView(APIView):
    """Preview delivery charge/eligibility before payment. Auth required."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        items = request.data.get('items') or []
        coupon_code = (request.data.get('coupon_code') or '').strip()

        try:
            shipping = delivery_service.resolve_shipping_from_request(request.user, request.data)
        except DeliveryError as exc:
            # Allow quoting with only postal_code when no full address yet
            postal = (request.data.get('postal_code') or '').strip()
            if not postal:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            shipping = {
                'postal_code': delivery_service.normalize_postal_code(postal),
                'shipping_latitude': request.data.get('shipping_latitude') or request.data.get('latitude'),
                'shipping_longitude': request.data.get('shipping_longitude') or request.data.get('longitude'),
            }

        subtotal = Decimal('0.00')
        discount_amount = Decimal('0.00')
        if isinstance(items, list) and items:
            try:
                rows = PaymentService.ensure_valid_cart(items)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            subtotal = sum((r['unit_price'] * r['quantity'] for r in rows), Decimal('0')).quantize(Decimal('0.01'))
            if coupon_code:
                try:
                    result = coupon_service.validate_coupon(request.user, coupon_code, rows, lock=False)
                    discount_amount = result['discount_amount']
                except CouponError as exc:
                    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        merchandise = (subtotal - discount_amount).quantize(Decimal('0.01'))
        if merchandise < 0:
            merchandise = Decimal('0.00')

        lat = shipping.get('shipping_latitude')
        lng = shipping.get('shipping_longitude')
        quote = delivery_service.compute_delivery(
            merchandise,
            shipping.get('postal_code'),
            latitude=lat,
            longitude=lng,
        )
        totals = coupon_service.apply_totals(subtotal, discount_amount, delivery_fee=quote['charge'] if quote['eligible'] else Decimal('0.00'))

        return Response({
            'eligible': quote['eligible'],
            'delivery_fee': float(quote['charge']),
            'message': quote['message'],
            'min_order_ok': quote['min_order_ok'],
            'eta_min_minutes': quote['eta_min_minutes'],
            'eta_max_minutes': quote['eta_max_minutes'],
            'zone_id': quote['zone_id'],
            'zone_name': quote['zone'].name if quote['zone'] else None,
            'distance_km': quote['distance_km'],
            'free_delivery_applied': quote['free_delivery_applied'],
            'subtotal_amount': float(totals['subtotal_amount']),
            'discount_amount': float(totals['discount_amount']),
            'total_amount': float(totals['total_amount']) if quote['eligible'] else None,
            'postal_code': delivery_service.normalize_postal_code(shipping.get('postal_code')),
        }, status=status.HTTP_200_OK)


class AdminDeliveryZoneListCreateView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = DeliveryZone.objects.all().order_by('name')
        active = (request.query_params.get('is_active') or '').strip().lower()
        if active in ('1', 'true', 'yes'):
            qs = qs.filter(is_active=True)
        elif active in ('0', 'false', 'no'):
            qs = qs.filter(is_active=False)
        return Response({
            'count': qs.count(),
            'results': DeliveryZoneSerializer(qs, many=True).data,
        }, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = DeliveryZoneSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        zone = serializer.save()
        return Response(DeliveryZoneSerializer(zone).data, status=status.HTTP_201_CREATED)


class AdminDeliveryZoneDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get_object(self, zone_id):
        return DeliveryZone.objects.filter(id=zone_id).first()

    def get(self, request, zone_id):
        zone = self.get_object(zone_id)
        if not zone:
            return Response({'detail': 'Delivery zone not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(DeliveryZoneSerializer(zone).data, status=status.HTTP_200_OK)

    def patch(self, request, zone_id):
        zone = self.get_object(zone_id)
        if not zone:
            return Response({'detail': 'Delivery zone not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = DeliveryZoneSerializer(zone, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        zone = serializer.save()
        return Response(DeliveryZoneSerializer(zone).data, status=status.HTTP_200_OK)

    def delete(self, request, zone_id):
        zone = self.get_object(zone_id)
        if not zone:
            return Response({'detail': 'Delivery zone not found.'}, status=status.HTTP_404_NOT_FOUND)
        zone.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminDeliverySettingsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        settings_row = DeliverySettings.get_solo()
        return Response(DeliverySettingsSerializer(settings_row).data, status=status.HTTP_200_OK)

    def patch(self, request):
        settings_row = DeliverySettings.get_solo()
        serializer = DeliverySettingsSerializer(settings_row, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        settings_row = serializer.save()
        return Response(DeliverySettingsSerializer(settings_row).data, status=status.HTTP_200_OK)

