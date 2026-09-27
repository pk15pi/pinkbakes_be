import hashlib
import hmac
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Avg, Q, Sum
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .admin_reporting import AdminReportingService
from .models import AdminActivity, DeliveryLocation, Employee, Order, OrderItem, OrderStatusHistory, Payment, Product, ProductView, Review
from .serializers import DeliveryLocationSerializer, EmployeeSerializer, OrderSerializer, PaymentSerializer, ProductSerializer, ReviewSerializer


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
            normalized.append({
                'product': product,
                'quantity': quantity,
                'unit_price': product.discounted_price,
            })
        return normalized


class PaymentCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        items = request.data.get('items') or []
        if not isinstance(items, list) or not items:
            return Response({'detail': 'Your cart is empty.'}, status=status.HTTP_400_BAD_REQUEST)

        required_fields = ['customer_name', 'customer_email', 'customer_mobile', 'shipping_address', 'city', 'state', 'postal_code', 'country']
        missing = [field for field in required_fields if not str(request.data.get(field, '')).strip()]
        if missing:
            return Response({'detail': f'Missing required checkout fields: {", ".join(missing)}.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            order_items = PaymentService.ensure_valid_cart(items)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        subtotal = sum((item['unit_price'] * item['quantity'] for item in order_items), Decimal('0')).quantize(Decimal('0.01'))

        if not settings.PAYMENT_ENABLED:
            return Response({'detail': 'Payments are currently disabled.'}, status=status.HTTP_400_BAD_REQUEST)

        order = Order.objects.create(
            user=request.user,
            order_number=f"PB-{timezone.now().strftime('%Y%m%d')}-{timezone.now().strftime('%H%M%S')}-{request.user.id}",
            customer_name=request.data['customer_name'].strip(),
            customer_email=request.data['customer_email'].strip(),
            customer_mobile=request.data['customer_mobile'].strip(),
            shipping_address=request.data['shipping_address'].strip(),
            shipping_address_2=(request.data.get('shipping_address_2') or '').strip(),
            city=request.data['city'].strip(),
            state=request.data['state'].strip(),
            postal_code=request.data['postal_code'].strip(),
            country=request.data['country'].strip(),
            subtotal_amount=subtotal,
            delivery_fee=Decimal('0'),
            tax_amount=Decimal('0'),
            total_amount=subtotal,
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

        order_number = f"order_{order.id}_{int(timezone.now().timestamp())}"
        payment = Payment.objects.create(
            order=order,
            user=request.user,
            gateway='razorpay',
            gateway_order_id=order_number,
            amount=subtotal,
            currency=settings.RAZORPAY_CURRENCY,
            status='created',
        )

        return Response({
            'payment_order_id': payment.gateway_order_id,
            'amount': int((payment.amount * 100).quantize(Decimal('1'))),
            'currency': payment.currency,
            'gateway': payment.gateway,
            'key_id': settings.RAZORPAY_KEY_ID,
            'order_id': order.id,
            'payment_id': payment.id,
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

        if not PaymentService.verify_signature(order_id, payment_id, signature, settings.RAZORPAY_KEY_SECRET):
            payment.status = 'failed'
            payment.failure_reason = 'Invalid gateway signature.'
            payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
            return Response({'detail': 'Payment verification failed.'}, status=status.HTTP_400_BAD_REQUEST)

        expected_amount = int((payment.amount * 100).quantize(Decimal('1')))
        if not request.data.get('amount'):
            request_amount = expected_amount
        else:
            request_amount = int(request.data.get('amount'))
        if request_amount != expected_amount:
            payment.status = 'failed'
            payment.failure_reason = 'Gateway amount mismatch.'
            payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
            return Response({'detail': 'Payment amount verification failed.'}, status=status.HTTP_400_BAD_REQUEST)

        payment.gateway_payment_id = payment_id
        payment.gateway_signature = signature
        payment.payment_method = request.data.get('payment_method', 'razorpay')
        payment.status = 'paid'
        payment.paid_at = timezone.now()
        payment.save(update_fields=['gateway_payment_id', 'gateway_signature', 'payment_method', 'status', 'paid_at', 'updated_at'])

        order = payment.order
        if order:
            order.payment_status = 'paid'
            order.status = 'ORDER_CONFIRMED'
            order.save(update_fields=['payment_status', 'status', 'updated_at'])
            OrderStatusHistory.objects.create(
                order=order,
                status='ORDER_CONFIRMED',
                message='Payment verified successfully and order confirmed.',
                changed_by=request.user,
            )

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
        payload = request.data
        signature = request.headers.get('X-Razorpay-Signature', '')
        body = request.body.decode('utf-8') if hasattr(request.body, 'decode') else str(request.body)
        expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return Response({'detail': 'Invalid webhook signature.'}, status=status.HTTP_400_BAD_REQUEST)

        event = payload.get('event')
        payment_data = payload.get('payload', {}).get('payment', {}).get('entity') or {}
        order_data = payload.get('payload', {}).get('order', {}).get('entity') or {}
        gateway_order_id = order_data.get('id') or payment_data.get('order_id') or ''
        payment_id = payment_data.get('id') or ''
        if not gateway_order_id and not payment_id:
            return Response({'detail': 'Webhook payload missing payment references.'}, status=status.HTTP_400_BAD_REQUEST)

        payment = Payment.objects.filter(gateway_order_id=gateway_order_id).order_by('-created_at').first() if gateway_order_id else None
        if payment is None and payment_id:
            payment = Payment.objects.filter(gateway_payment_id=payment_id).order_by('-created_at').first()
        if payment is None:
            return Response({'detail': 'Payment record not found for webhook.'}, status=status.HTTP_404_NOT_FOUND)

        if event == 'payment.captured':
            payment.status = 'paid'
            payment.gateway_payment_id = payment_id or payment.gateway_payment_id
            payment.paid_at = timezone.now()
            payment.save(update_fields=['status', 'gateway_payment_id', 'paid_at', 'updated_at'])
            if payment.order:
                payment.order.payment_status = 'paid'
                payment.order.status = 'ORDER_CONFIRMED'
                payment.order.save(update_fields=['payment_status', 'status', 'updated_at'])
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

        payment = Payment.objects.filter(order=order).order_by('-created_at').first()
        if not payment:
            return Response({'detail': 'No payment record found for this order.'}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'order_id': order.id,
            'payment_order_id': payment.gateway_order_id,
            'amount': int((payment.amount * 100).quantize(Decimal('1'))),
            'currency': payment.currency,
            'gateway': payment.gateway,
            'key_id': settings.RAZORPAY_KEY_ID,
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

        for item in items:
            if not isinstance(item, dict):
                return Response({'detail': 'Order items must be objects.'}, status=status.HTTP_400_BAD_REQUEST)
            product_id = item.get('id')
            quantity = item.get('quantity', 1)
            if product_id is None or quantity is None:
                return Response({'detail': 'Each cart item needs an id and quantity.'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                return Response({'detail': 'Quantity must be a number.'}, status=status.HTTP_400_BAD_REQUEST)
            if quantity <= 0:
                return Response({'detail': 'Quantity must be greater than zero.'}, status=status.HTTP_400_BAD_REQUEST)

        required_fields = ['customer_name', 'customer_email', 'customer_mobile', 'shipping_address', 'city', 'state', 'postal_code', 'country']
        missing = [field for field in required_fields if not str(request.data.get(field, '')).strip()]
        if missing:
            return Response({'detail': f'Missing required checkout fields: {", ".join(missing)}.'}, status=status.HTTP_400_BAD_REQUEST)

        subtotal = Decimal('0')
        order_items = []
        for item in items:
            product = Product.objects.filter(id=item['id'], is_active=True).first()
            if not product:
                return Response({'detail': f'Product #{item["id"]} is unavailable.'}, status=status.HTTP_404_NOT_FOUND)
            quantity = int(item.get('quantity', 1))
            unit_price = product.discounted_price
            line_total = (unit_price * quantity).quantize(Decimal('0.01'))
            subtotal += line_total
            order_items.append({
                'product': product,
                'product_name': product.name,
                'product_image': product.main_image,
                'unit_price': unit_price,
                'quantity': quantity,
                'subtotal': line_total,
            })

        order = Order.objects.create(
            user=request.user,
            order_number=f"PB-{timezone.now().strftime('%Y%m%d')}-{timezone.now().strftime('%H%M%S')}-{request.user.id}",
            customer_name=request.data['customer_name'].strip(),
            customer_email=request.data['customer_email'].strip(),
            customer_mobile=request.data['customer_mobile'].strip(),
            shipping_address=request.data['shipping_address'].strip(),
            shipping_address_2=(request.data.get('shipping_address_2') or '').strip(),
            city=request.data['city'].strip(),
            state=request.data['state'].strip(),
            postal_code=request.data['postal_code'].strip(),
            country=request.data['country'].strip(),
            subtotal_amount=subtotal,
            delivery_fee=Decimal('0'),
            tax_amount=Decimal('0'),
            total_amount=subtotal,
            notes=(request.data.get('notes') or '').strip(),
            status='pending',
            payment_status='pending',
        )

        for item in order_items:
            OrderItem.objects.create(
                order=order,
                product=item['product'],
                product_name=item['product_name'],
                product_image=item['product_image'],
                unit_price=item['unit_price'],
                quantity=item['quantity'],
                subtotal=item['subtotal'],
            )

        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


class OrderListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        orders = Order.objects.filter(user=request.user).order_by('-created_at')
        return Response({
            'count': orders.count(),
            'results': OrderSerializer(orders, many=True).data,
        }, status=status.HTTP_200_OK)


class OrderDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, order_id):
        order = Order.objects.filter(id=order_id).select_related('user').prefetch_related('items').first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not request.user.is_staff and order.user_id != request.user.id:
            return Response({'detail': 'You are not allowed to view this order.'}, status=status.HTTP_403_FORBIDDEN)
        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)


class AdminOrderListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        orders = Order.objects.all().order_by('-created_at')
        return Response({
            'count': orders.count(),
            'results': OrderSerializer(orders, many=True).data,
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
            sales['pending_orders'] = Order.objects.filter(status='pending').count()
            sales['completed_orders'] = Order.objects.filter(status='delivered').count()
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
