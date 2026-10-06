from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from .models import AdminActivity, CouponRedemption, Order, Payment, Product, Refund, Review


class AdminReportingService:
    @staticmethod
    def parse_date_range(from_date=None, to_date=None, preset='custom'):
        now = timezone.now()
        if preset == 'today':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif preset == 'yesterday':
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif preset == 'last_7_days':
            start = now - timedelta(days=6)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif preset == 'last_30_days':
            start = now - timedelta(days=29)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif preset == 'this_month':
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif preset == 'last_month':
            first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            start = (first_day - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = first_day - timedelta(seconds=1)
        elif preset == 'this_year':
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now
        else:
            start = datetime.strptime(from_date, '%Y-%m-%d').replace(tzinfo=timezone.utc) if from_date else None
            end = datetime.strptime(to_date, '%Y-%m-%d').replace(tzinfo=timezone.utc) if to_date else None
            if end is not None:
                end = end.replace(hour=23, minute=59, second=59, microsecond=999999)

        if not start:
            start = now - timedelta(days=30)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        if not end:
            end = now
        return start, end

    @staticmethod
    def user_stats(from_date=None, to_date=None, preset='custom'):
        """Customer-facing user tallies (excludes staff), aligned with /api/admin/customers/."""
        start, end = AdminReportingService.parse_date_range(from_date, to_date, preset)
        customers = User.objects.filter(is_staff=False)
        total = customers.count()
        verified = customers.filter(profile__is_verified=True).count()
        unverified = total - verified
        new_today = customers.filter(date_joined__date=timezone.now().date()).count()
        new_week = customers.filter(date_joined__gte=timezone.now() - timedelta(days=7)).count()
        new_month = customers.filter(date_joined__gte=timezone.now() - timedelta(days=30)).count()
        if from_date or to_date or preset != 'custom':
            new_range = customers.filter(date_joined__gte=start, date_joined__lte=end).count()
            return {
                'total_users': total,
                'verified_users': verified,
                'unverified_users': unverified,
                'new_users_today': new_today,
                'new_users_this_week': new_week,
                'new_users_this_month': new_month,
                'filtered_new_users': new_range,
            }
        return {
            'total_users': total,
            'verified_users': verified,
            'unverified_users': unverified,
            'new_users_today': new_today,
            'new_users_this_week': new_week,
            'new_users_this_month': new_month,
        }

    @staticmethod
    def product_stats():
        from django.db.models import Count, Q
        total_all = Product.objects.count()
        # Collapsed availability + status tallies where practical.
        avail_map = {
            row['availability']: row['c']
            for row in Product.objects.values('availability').annotate(c=Count('id'))
        }
        active = Product.objects.filter(is_active=True, status='published').count()
        inactive = (
            Product.objects.filter(is_active=False).count()
            + Product.objects.filter(is_active=True, status='archived').count()
        )
        discounted = Product.objects.filter(discount__gt=0).count()
        no_image = Product.objects.filter(Q(main_image='') | Q(main_image__isnull=True)).count()
        with_3d = Product.objects.exclude(three_d_model='').exclude(three_d_model__isnull=True).count()
        return {
            'total_products': total_all,
            'active_products': active,
            'inactive_products': inactive,
            'products_with_discounts': discounted,
            'products_without_images': no_image,
            'products_with_3d_assets': with_3d,
            'in_stock_products': avail_map.get('in_stock', 0),
            'low_stock_products': avail_map.get('low_stock', 0),
            'out_of_stock_products': avail_map.get('out_of_stock', 0),
        }

    @staticmethod
    def review_stats():
        total = Review.objects.count()
        avg_rating = Review.objects.filter(status='approved').aggregate(avg=Avg('rating'))['avg'] or 0
        dist_rows = (
            Review.objects.filter(status='approved')
            .values('rating')
            .annotate(c=Count('id'))
        )
        distribution = {str(row['rating']): row['c'] for row in dist_rows}
        pending = Review.objects.filter(status='pending').count()
        return {
            'total_reviews': total,
            'average_rating': float(avg_rating),
            'five_star_reviews': distribution.get('5', 0),
            'four_star_reviews': distribution.get('4', 0),
            'three_star_reviews': distribution.get('3', 0),
            'two_star_reviews': distribution.get('2', 0),
            'one_star_reviews': distribution.get('1', 0),
            'pending_reviews': pending,
        }

    @staticmethod
    def sales_summary(from_date=None, to_date=None, preset='custom'):
        start, end = AdminReportingService.parse_date_range(from_date, to_date, preset)
        payments = Payment.objects.filter(created_at__gte=start, created_at__lte=end)
        order_queryset = Order.objects.filter(created_at__gte=start, created_at__lte=end)

        total_orders = order_queryset.count()
        failed_payments = payments.filter(status='failed').count()
        pending_payments = payments.filter(status__in=['created', 'pending', 'authorized']).count()
        refunded_payments = payments.filter(status__in=['refunded', 'partially_refunded']).count()
        paid_like = payments.filter(status__in=['paid', 'refund_pending', 'refunded', 'partially_refunded'])

        # Revenue from paid/refunded orders (authoritative). Covers paid orders missing Payment rows.
        paid_orders = order_queryset.filter(payment_status__in=['paid', 'refunded'])
        total_sales = float((paid_orders.aggregate(total=Sum('total_amount'))['total'] or 0))
        payment_capture_total = float((paid_like.aggregate(total=Sum('amount'))['total'] or 0))

        # Successful payment count: Payment rows + paid orders that never got a Payment row.
        paid_order_ids_with_payment = set(
            paid_like.exclude(order_id__isnull=True).values_list('order_id', flat=True)
        )
        orphan_paid_orders = paid_orders.exclude(id__in=paid_order_ids_with_payment).count()
        successful_payments = paid_like.count() + orphan_paid_orders

        refunds_qs = Refund.objects.filter(status='completed', created_at__gte=start, created_at__lte=end)
        refunds_total = float((refunds_qs.aggregate(total=Sum('amount'))['total'] or 0))
        net_sales = total_sales - refunds_total

        today = timezone.now().date()
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        today_sales = float((
            Order.objects.filter(
                payment_status__in=['paid', 'refunded'], created_at__date=today,
            ).aggregate(total=Sum('total_amount'))['total'] or 0
        ))
        monthly_sales = float((
            Order.objects.filter(
                payment_status__in=['paid', 'refunded'], created_at__gte=month_start,
            ).aggregate(total=Sum('total_amount'))['total'] or 0
        ))

        sales = {
            'total_orders': total_orders,
            'orders_today': order_queryset.filter(created_at__date=timezone.now().date()).count(),
            'orders_this_week': order_queryset.filter(created_at__gte=timezone.now() - timedelta(days=7)).count(),
            'orders_this_month': order_queryset.filter(created_at__gte=month_start).count(),
            'total_sales': total_sales,
            'gross_sales': total_sales,
            'payment_capture_total': payment_capture_total,
            'discounts': float((paid_orders.aggregate(total=Sum('discount_amount'))['total'] or 0)),
            'delivery_charges': float((paid_orders.aggregate(total=Sum('delivery_fee'))['total'] or 0)),
            'today_sales': today_sales,
            'monthly_sales': monthly_sales,
            'average_order_value': float((order_queryset.aggregate(avg=Avg('total_amount'))['avg'] or 0)),
            'cancelled_orders': order_queryset.filter(status='CANCELLED').count(),
            'pending_orders': order_queryset.filter(status='PENDING').count(),
            'completed_orders': order_queryset.filter(status='DELIVERED').count(),
            'successful_payments': successful_payments,
            'failed_payments': failed_payments,
            'pending_payments': pending_payments,
            'refunded_payments': refunded_payments,
            'refunds_total': refunds_total,
            'net_sales': net_sales,
            'transactions': payments.count(),
            'currency': 'INR',
            'note': (
                'gross/total_sales = sum of paid/refunded order totals in range '
                '(includes paid orders missing Payment rows); '
                'payment_capture_total = sum of paid-like Payment amounts; '
                'net_sales = gross_sales - completed refunds.'
            ),
            'coupons_used_count': CouponRedemption.objects.filter(
                status='redeemed', created_at__gte=start, created_at__lte=end,
            ).count(),
            'coupon_discounts': float((order_queryset.filter(coupon_discount_amount__gt=0).aggregate(total=Sum('coupon_discount_amount'))['total'] or 0)),
            'total_coupon_discount': float((
                order_queryset.filter(coupon_discount_amount__gt=0).aggregate(
                    total=Sum('coupon_discount_amount')
                )['total'] or 0
            )),
            'orders_with_coupon': order_queryset.exclude(coupon_code='').count(),
        }
        return sales

    @staticmethod
    def summary(from_date=None, to_date=None, preset='custom'):
        # Short TTL for admin report overview only (not payment verify / order mutation paths).
        from .cache_utils import admin_dashboard_cache_key
        key = 'admin:report:summary:' + admin_dashboard_cache_key(preset or 'custom', from_date, to_date)
        cached = cache.get(key)
        if cached is not None:
            return cached
        payload = {
            'user_stats': AdminReportingService.user_stats(from_date, to_date, preset),
            'product_stats': AdminReportingService.product_stats(),
            'review_stats': AdminReportingService.review_stats(),
            'sales_stats': AdminReportingService.sales_summary(from_date, to_date, preset),
        }
        cache.set(key, payload, 20)
        return payload

    @staticmethod
    def product_performance(from_date=None, to_date=None, preset='custom'):
        queryset = Product.objects.filter(is_active=True).annotate(
            review_count=Count('reviews', filter=Q(reviews__status='approved')),
            avg_rating=Avg('reviews__rating', filter=Q(reviews__status='approved')),
            view_count=Count('product_views', filter=Q(product_views__created_at__gte=timezone.now() - timedelta(days=30))),
        ).order_by('-view_count', '-avg_rating', '-review_count')
        if from_date or to_date or preset != 'custom':
            start, end = AdminReportingService.parse_date_range(from_date, to_date, preset)
            queryset = queryset.filter(created_at__gte=start, created_at__lte=end)
        return list(queryset.values('id', 'name', 'price', 'discount', 'availability', 'avg_rating', 'review_count', 'view_count', 'created_at'))

    @staticmethod
    def admin_activity(from_date=None, to_date=None, preset='custom'):
        start, end = AdminReportingService.parse_date_range(from_date, to_date, preset)
        return list(AdminActivity.objects.filter(created_at__gte=start, created_at__lte=end).select_related('admin_user').values(
            'id', 'admin_user__username', 'action', 'entity_type', 'entity_id', 'description', 'ip_address', 'created_at',
        ))
