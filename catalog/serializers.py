from rest_framework import serializers

from .models import Product, Review


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['id', 'name', 'rating', 'comment', 'created_at']
        read_only_fields = ['id', 'created_at']


class ProductSerializer(serializers.ModelSerializer):
    gallery = serializers.ListField(child=serializers.URLField(), required=False, default=list, allow_empty=True)
    reviews = ReviewSerializer(many=True, read_only=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    rating = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=5)

    class Meta:
        model = Product
        fields = [
            'id',
            'name',
            'category',
            'price',
            'description',
            'short_description',
            'image',
            'gallery',
            'badge',
            'rating',
            'featured',
            'delivery_time',
            'three_d_model',
            'is_active',
            'reviews',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'reviews']

    def validate_gallery(self, value):
        if value is None:
            return []
        return value
