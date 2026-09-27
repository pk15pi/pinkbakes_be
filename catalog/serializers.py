from django.db.models import Avg
from django.utils.text import slugify
from rest_framework import serializers

from .models import Product, Review


class ReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    name = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Review
        fields = [
            'id',
            'product',
            'user',
            'user_name',
            'name',
            'rating',
            'comment',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'product', 'user', 'user_name', 'created_at', 'updated_at', 'status']

    def get_user_name(self, obj):
        if obj.user:
            return obj.user.get_full_name() or obj.user.username
        return obj.name or 'Customer'

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('Rating must be between 1 and 5.')
        return value

    def validate_comment(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError('Review comment cannot be empty.')
        if len(cleaned) > 500:
            raise serializers.ValidationError('Review comment must be 500 characters or fewer.')
        return cleaned


class ProductSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    gallery = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    three_d_assets = serializers.ListField(child=serializers.URLField(), required=False, default=list, allow_empty=True)
    reviews = ReviewSerializer(many=True, read_only=True)
    discounted_price = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, default=0)
    rating = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=5, required=False, default=0)
    discount = serializers.IntegerField(min_value=0, max_value=100, required=False, default=0)

    class Meta:
        model = Product
        fields = [
            'id',
            'name',
            'slug',
            'category',
            'price',
            'discount',
            'discounted_price',
            'short_description',
            'description',
            'main_image',
            'image',
            'gallery',
            'images',
            'badge',
            'three_d_model',
            'three_d_assets',
            'availability',
            'status',
            'featured',
            'delivery_time',
            'rating',
            'average_rating',
            'review_count',
            'is_active',
            'reviews',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'discounted_price', 'average_rating', 'review_count', 'created_at', 'updated_at', 'reviews']

    def get_image(self, obj):
        return obj.main_image or (obj.images[0] if obj.images else '')

    def get_gallery(self, obj):
        return obj.images or ([obj.main_image] if obj.main_image else [])

    def get_images(self, obj):
        return obj.images or ([obj.main_image] if obj.main_image else [])

    def to_internal_value(self, data):
        data = data.copy()
        if 'image' in data and 'main_image' not in data:
            data['main_image'] = data['image']
        if 'images' in data and isinstance(data['images'], str):
            data['images'] = [data['images']]
        if 'gallery' in data and 'images' not in data:
            data['images'] = data['gallery']
        if 'gallery' in data and isinstance(data['gallery'], str):
            data['images'] = [data['gallery']]
        if 'three_d' in data and 'three_d_model' not in data:
            data['three_d_model'] = data['three_d']
        if 'three_d_assets' in data and isinstance(data['three_d_assets'], str):
            data['three_d_assets'] = [data['three_d_assets']]
        return super().to_internal_value(data)

    def get_discounted_price(self, obj):
        return float(obj.discounted_price.quantize(__import__('decimal').Decimal('0.01')))

    def get_average_rating(self, obj):
        reviews = obj.reviews.filter(status='approved')
        avg = reviews.aggregate(avg=Avg('rating'))['avg']
        return float(avg or 0)

    def get_review_count(self, obj):
        return obj.reviews.filter(status='approved').count()

    def validate_discount(self, value):
        if value is None:
            return 0
        if value < 0 or value > 100:
            raise serializers.ValidationError('Discount must be between 0 and 100.')
        return value

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Product name is required.')
        return value

    def create(self, validated_data):
        gallery = validated_data.pop('gallery', [])
        images = validated_data.pop('images', gallery)
        if images and not validated_data.get('main_image'):
            validated_data['main_image'] = images[0]
        validated_data['slug'] = slugify(validated_data.get('name', '')) or 'product'
        product = Product.objects.create(**validated_data)
        if images:
            product.images = images
        if 'three_d_assets' in validated_data and validated_data['three_d_assets'] is not None:
            product.three_d_assets = validated_data['three_d_assets']
        product.save(update_fields=['images', 'three_d_assets'])
        return product

    def update(self, instance, validated_data):
        if 'name' in validated_data and validated_data['name']:
            instance.name = validated_data['name']
            instance.slug = slugify(instance.name)
        if 'main_image' in validated_data and validated_data['main_image']:
            instance.main_image = validated_data['main_image']
        if 'images' in validated_data:
            instance.images = validated_data['images']
        if 'three_d_assets' in validated_data:
            instance.three_d_assets = validated_data['three_d_assets']
        for field in ['category', 'price', 'discount', 'short_description', 'description', 'three_d_model', 'availability', 'status', 'badge', 'featured', 'delivery_time', 'is_active']:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.save()
        return instance
