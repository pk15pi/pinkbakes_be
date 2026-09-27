from decimal import Decimal

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db.models import Avg, Q
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .admin_reporting import AdminReportingService
from .models import AdminActivity, Order, OrderItem, Product, ProductView, Review
from .serializers import OrderSerializer, ProductSerializer, ReviewSerializer


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

        serializer = ReviewSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        review = serializer.save(product=product, user=request.user, name=request.user.get_full_name() or request.user.username)
        rating_avg = product.reviews.filter(status='approved').aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
        product.rating = round(float(rating_avg), 1)
        product.save(update_fields=['rating'])
        return Response({
            'message': 'Review added successfully.',
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

        serializer = ReviewSerializer(review, data=request.data, partial=True, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        product = review.product
        rating_avg = product.reviews.filter(status='approved').aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
        product.rating = round(float(rating_avg), 1)
        product.save(update_fields=['rating'])
        return Response({'message': 'Review updated successfully.', 'review': ReviewSerializer(review).data}, status=status.HTTP_200_OK)

    def delete(self, request, review_id):
        try:
            review = self.get_object(review_id)
        except ValueError:
            return Response({'detail': 'Review not found.'}, status=status.HTTP_404_NOT_FOUND)

        if review.user_id != request.user.id and not request.user.is_staff:
            return Response({'detail': 'You are not allowed to delete this review.'}, status=status.HTTP_403_FORBIDDEN)

        review.delete()
        product = review.product
        rating_avg = product.reviews.filter(status='approved').aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
        product.rating = round(float(rating_avg), 1)
        product.save(update_fields=['rating'])
        return Response({'message': 'Review deleted successfully.'}, status=status.HTTP_200_OK)


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
