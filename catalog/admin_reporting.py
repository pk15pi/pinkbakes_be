from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from .models import AdminActivity, CouponRedemption, Order, Payment, Product, ProductView, Refund, Review


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
        start, end = AdminReportingService.parse_date_range(from_date, to_date, preset)
        total = User.objects.count()
        verified = User.objects.filter(profile__is_verified=True).count()
        unverified = total - verified
        new_today = User.objects.filter(date_joined__date=timezone.now().date()).count()
        new_week = User.objects.filter(date_joined__gte=timezone.now() - timedelta(days=7)).count()
        new_month = User.objects.filter(date_joined__gte=timezone.now() - timedelta(days=30)).count()
        if from_date or to_date or preset != 'custom':
            new_range = User.objects.filter(date_joined__gte=start, date_joined__lte=end).count()
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
        total = Product.objects.filter(is_active=True).count()
        active = Product.objects.filter(is_active=True, status='published').count()
        inactive = Product.objects.filter(is_active=False).count() + Product.objects.filter(is_active=True, status='archived').count()
        discounted = Product.objects.filter(discount__gt=0).count()
        no_image = Product.objects.filter(Q(main_image='') | Q(main_image__isnull=True)).count()
        with_3d = Product.objects.exclude(three_d_model='').exclude(three_d_model__isnull=True).count()
        in_stock = Product.objects.filter(availability='in_stock').count()
        low_stock = Product.objects.filter(availability='low_stock').count()
        out_of_stock = Product.objects.filter(availability='out_of_stock').count()
        return {
            'total_products': Product.objects.count(),
            'active_products': active,
            'inactive_products': inactive,
            'products_with_discounts': discounted,
            'products_without_images': no_image,
            'products_with_3d_assets': with_3d,
            'in_stock_products': in_stock,
            'low_stock_products': low_stock,
            'out_of_stock_products': out_of_stock,
        }

    @staticmethod
    def review_stats():
        total = Review.objects.count()
        avg_rating = Review.objects.filter(status='approved').aggregate(avg=Avg('rating'))['avg'] or 0
        distribution = {str(i): Review.objects.filter(status='approved', rating=i).count() for i in range(1, 6)}
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
        # total_sales recomputed below to include paid/refund_pending/refunded/partially_refunded
        total_sales = 0.0
        failed_payments = payments.filter(status='failed').count()
        pending_payments = payments.filter(status='pending').count()
        refunded_payments = payments.filter(status__in=['refunded', 'partially_refunded']).count()
        successful_payments = payments.filter(status__in=['paid', 'refund_pending', 'refunded', 'partially_refunded']).count()
        # Successful capture total still based on originally paid amount (paid + refund states).
        paid_like = payments.filter(status__in=['paid', 'refund_pending', 'refunded', 'partially_refunded'])
        total_sales = float((paid_like.aggregate(total=Sum('amount'))['total'] or 0))
        refunds_qs = Refund.objects.filter(status='completed', created_at__gte=start, created_at__lte=end)
        refunds_total = float((refunds_qs.aggregate(total=Sum('amount'))['total'] or 0))
        net_sales = total_sales - refunds_total

        sales = {
            'total_orders': total_orders,
            'orders_today': order_queryset.filter(created_at__date=timezone.now().date()).count(),
            'orders_this_week': order_queryset.filter(created_at__gte=timezone.now() - timedelta(days=7)).count(),
            'orders_this_month': order_queryset.filter(created_at__gte=timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)).count(),
            'total_sales': total_sales,
            'gross_sales': total_sales,
            'discounts': float((order_queryset.filter(payment_status__in=['paid', 'refunded']).aggregate(total=Sum('discount_amount'))['total'] or 0)),
            'delivery_charges': float((order_queryset.filter(payment_status__in=['paid', 'refunded']).aggregate(total=Sum('delivery_fee'))['total'] or 0)),
            'today_sales': float((paid_like.filter(created_at__date=timezone.now().date()).aggregate(total=Sum('amount'))['total'] or 0)),
            'monthly_sales': float((paid_like.filter(created_at__gte=timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)).aggregate(total=Sum('amount'))['total'] or 0)),
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
            'note': 'Payment and order data are now integrated into revenue reporting. net_sales = successful payments - completed refunds.',
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
        return {
            'user_stats': AdminReportingService.user_stats(from_date, to_date, preset),
            'product_stats': AdminReportingService.product_stats(),
            'review_stats': AdminReportingService.review_stats(),
            'sales_stats': AdminReportingService.sales_summary(from_date, to_date, preset),
        }

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
