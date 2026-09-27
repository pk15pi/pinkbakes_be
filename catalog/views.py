from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.db.models import Avg
from rest_framework import generics, permissions, serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, Review
from .serializers import ProductSerializer, ReviewSerializer


class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class AdminLoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')

        if not username or not password:
            return Response({'detail': 'Username and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(username=username, password=password)
        if not user or not user.is_staff:
            return Response({'detail': 'Invalid admin credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        login(request, user)
        token, _ = Token.objects.get_or_create(user=user)
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


class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = Product.objects.filter(is_active=True)
        category = self.request.query_params.get('category')
        if category and category != 'All Cakes':
            queryset = queryset.filter(category__iexact=category)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset.order_by('-featured', '-created_at')


class ProductDetailView(generics.RetrieveAPIView):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer
    permission_classes = [permissions.AllowAny]


class AdminProductListCreateView(generics.ListCreateAPIView):
    queryset = Product.objects.all().order_by('-featured', '-created_at')
    serializer_class = ProductSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        serializer.save()


class AdminProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsAdminUser]


class ProductReviewCreateView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, product_id):
        product = Product.objects.filter(id=product_id, is_active=True).first()
        if not product:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = ReviewSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        review = serializer.save(product=product)
        rating_avg = product.reviews.aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
        product.rating = round(float(rating_avg), 1)
        product.save(update_fields=['rating'])
        return Response({
            'message': 'Review added successfully.',
            'review': ReviewSerializer(review).data,
            'rating': product.rating,
        }, status=status.HTTP_201_CREATED)
