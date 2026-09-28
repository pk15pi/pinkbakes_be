from django.db.models import Avg
from django.utils.text import slugify
from rest_framework import serializers

from .models import Coupon, CouponRedemption, DeliveryLocation, Employee, Order, OrderItem, OrderStatusHistory, Payment, Product, Refund, Review, DeliveryZone, DeliverySettings


class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = [
            'id',
            'employee_id',
            'name',
            'contact_number',
            'email',
            'photo',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_contact_number(self, value):
        value = value.strip()
        if len(value) < 8:
            raise serializers.ValidationError('Contact number must be at least 8 digits.')
        return value


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderStatusHistory
        fields = ['id', 'status', 'message', 'changed_by', 'created_at']
        read_only_fields = fields


class DeliveryLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryLocation
        fields = ['id', 'order', 'employee', 'latitude', 'longitude', 'accuracy', 'timestamp']
        read_only_fields = ['id', 'order', 'employee', 'timestamp']



class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = [
            'id',
            'order',
            'payment',
            'user',
            'gateway',
            'gateway_refund_id',
            'amount',
            'currency',
            'reason',
            'status',
            'initiated_by',
            'initiated_by_type',
            'failure_reason',
            'created_at',
            'updated_at',
            'processed_at',
        ]
        read_only_fields = fields

class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'id',
            'product',
            'product_name',
            'product_image',
            'unit_price',
            'quantity',
            'subtotal',
        ]
        read_only_fields = ['id', 'product_name', 'product_image', 'unit_price', 'subtotal']

    def get_product_name(self, obj):
        return obj.product_name or (obj.product.name if obj.product else 'Product')


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    delivery_employee = EmployeeSerializer(read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)
    cancellable = serializers.SerializerMethodField()
    refunds = serializers.SerializerMethodField()
    refunds_summary = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id',
            'order_number',
            'customer_name',
            'customer_email',
            'customer_mobile',
            'shipping_address',
            'shipping_address_2',
            'landmark',
            'city',
            'state',
            'postal_code',
            'country',
            'shipping_latitude',
            'shipping_longitude',
            'address',
            'delivery_zone',
            'subtotal_amount',
            'discount_amount',
            'delivery_fee',
            'tax_amount',
            'total_amount',
            'coupon',
            'coupon_code',
            'coupon_discount_type',
            'coupon_discount_value',
            'coupon_discount_amount',
            'status',
            'payment_status',
            'delivery_employee',
            'delivery_assigned_at',
            'delivery_started_at',
            'delivery_completed_at',
            'notes',
            'cancellation_reason',
            'cancelled_by',
            'cancelled_at',
            'created_at',
            'updated_at',
            'items',
            'status_history',
            'cancellable',
            'refunds',
            'refunds_summary',
        ]
        read_only_fields = fields

    def get_cancellable(self, obj):
        request = self.context.get('request')
        if request and getattr(request.user, 'is_staff', False):
            return obj.is_admin_cancellable()
        return obj.is_customer_cancellable()

    def get_refunds(self, obj):
        refunds = getattr(obj, 'refunds', None)
        if refunds is None:
            return []
        qs = refunds.all().order_by('-created_at')
        return RefundSerializer(qs, many=True).data

    def get_refunds_summary(self, obj):
        refunds = list(getattr(obj, 'refunds', Refund.objects.none()).all())
        completed = [r for r in refunds if r.status == 'completed']
        pending = [r for r in refunds if r.status in ('requested', 'pending', 'processing')]
        failed = [r for r in refunds if r.status == 'failed']
        completed_total = sum((r.amount for r in completed), __import__('decimal').Decimal('0'))
        return {
            'count': len(refunds),
            'completed_count': len(completed),
            'pending_count': len(pending),
            'failed_count': len(failed),
            'completed_amount': float(completed_total),
            'latest_status': refunds[0].status if refunds else None,
        }


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            'id',
            'order',
            'user',
            'gateway',
            'gateway_order_id',
            'gateway_payment_id',
            'gateway_signature',
            'amount',
            'currency',
            'status',
            'payment_method',
            'failure_reason',
            'created_at',
            'updated_at',
            'paid_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'paid_at', 'gateway_payment_id', 'gateway_signature']


class ReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    name = serializers.CharField(required=False, allow_blank=True)
    order = serializers.PrimaryKeyRelatedField(read_only=True)
    order_item = serializers.PrimaryKeyRelatedField(read_only=True)
    admin_comment = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Review
        fields = [
            'id',
            'product',
            'user',
            'user_name',
            'name',
            'order',
            'order_item',
            'rating',
            'comment',
            'status',
            'admin_comment',
            'approved_by',
            'approved_at',
            'rejected_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'product', 'user', 'user_name', 'order', 'order_item', 'created_at', 'updated_at', 'status', 'admin_comment', 'approved_by', 'approved_at', 'rejected_at']

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
        if len(cleaned) < 5:
            raise serializers.ValidationError('Review comment must be at least 5 characters long.')
        if len(cleaned) > 1000:
            raise serializers.ValidationError('Review comment must be 1000 characters or fewer.')
        return cleaned


class ProductSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    gallery = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    three_d_assets = serializers.ListField(child=serializers.URLField(), required=False, default=list, allow_empty=True)
    reviews = serializers.SerializerMethodField()
    discounted_price = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, default=0)
    rating = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=5, required=False, default=0)
    discount = serializers.IntegerField(min_value=0, max_value=100, required=False, default=0)
    available_quantity = serializers.IntegerField(min_value=0, required=False)
    low_stock_threshold = serializers.IntegerField(min_value=0, required=False)
    stock_remaining = serializers.SerializerMethodField()
    is_low_stock = serializers.SerializerMethodField()
    reserved_quantity = serializers.SerializerMethodField()
    sold_quantity = serializers.SerializerMethodField()

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
            'available_quantity',
            'stock_remaining',
            'is_low_stock',
            'low_stock_threshold',
            'reserved_quantity',
            'sold_quantity',
            'stock_updated_at',
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
        read_only_fields = ['id', 'slug', 'discounted_price', 'average_rating', 'review_count', 'created_at', 'updated_at', 'reviews', 'stock_remaining', 'is_low_stock', 'stock_updated_at', 'reserved_quantity', 'sold_quantity']

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

    def get_reviews(self, obj):
        return ReviewSerializer(obj.reviews.filter(status='approved').order_by('-created_at'), many=True).data

    def get_average_rating(self, obj):
        reviews = obj.reviews.filter(status='approved')
        avg = reviews.aggregate(avg=Avg('rating'))['avg']
        return float(avg or 0)

    def get_review_count(self, obj):
        return obj.reviews.filter(status='approved').count()

    def _is_staff_request(self):
        request = self.context.get('request')
        return bool(request and getattr(request.user, 'is_authenticated', False) and getattr(request.user, 'is_staff', False))

    def get_stock_remaining(self, obj):
        return int(getattr(obj, 'available_quantity', 0) or 0)

    def get_is_low_stock(self, obj):
        available = int(getattr(obj, 'available_quantity', 0) or 0)
        threshold = int(getattr(obj, 'low_stock_threshold', 5) or 5)
        return 0 < available <= threshold

    def get_reserved_quantity(self, obj):
        if not self._is_staff_request():
            return None
        return int(getattr(obj, 'reserved_quantity', 0) or 0)

    def get_sold_quantity(self, obj):
        if not self._is_staff_request():
            return None
        return int(getattr(obj, 'sold_quantity', 0) or 0)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._is_staff_request():
            data.pop('reserved_quantity', None)
            data.pop('sold_quantity', None)
            # Keep low_stock_threshold visible? Prefer hide internal for customers.
            data.pop('low_stock_threshold', None)
        return data


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
        # Default stock for newly created products when not provided.
        if 'available_quantity' not in validated_data:
            availability = validated_data.get('availability', 'in_stock')
            if availability == 'out_of_stock':
                validated_data['available_quantity'] = 0
            elif availability == 'low_stock':
                validated_data['available_quantity'] = 3
            else:
                validated_data['available_quantity'] = 50
        product = Product.objects.create(**validated_data)
        if images:
            product.images = images
        if 'three_d_assets' in validated_data and validated_data['three_d_assets'] is not None:
            product.three_d_assets = validated_data['three_d_assets']
        product.save(update_fields=['images', 'three_d_assets'])
        from .inventory import sync_availability
        sync_availability(product, save=True)
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
        for field in ['category', 'price', 'discount', 'short_description', 'description', 'three_d_model', 'availability', 'status', 'badge', 'featured', 'delivery_time', 'is_active', 'available_quantity', 'low_stock_threshold']:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.save()
        if any(f in validated_data for f in ('available_quantity', 'low_stock_threshold', 'availability')):
            from .inventory import sync_availability
            # If admin manually set availability without qty, leave qty; prefer qty-driven sync.
            if 'available_quantity' in validated_data or 'low_stock_threshold' in validated_data:
                sync_availability(instance, save=True)
        return instance


class CouponSerializer(serializers.ModelSerializer):
    product_ids = serializers.PrimaryKeyRelatedField(
        source='products', many=True, queryset=Product.objects.all(), required=False,
    )

    class Meta:
        model = Coupon
        fields = [
            'id',
            'code',
            'name',
            'description',
            'discount_type',
            'discount_value',
            'minimum_order_amount',
            'maximum_discount_amount',
            'start_at',
            'end_at',
            'usage_limit',
            'usage_limit_per_user',
            'total_used',
            'is_active',
            'applies_to',
            'product_ids',
            'category_names',
            'exclude_already_discounted',
            'new_customers_only',
            'created_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'total_used', 'created_by', 'created_at', 'updated_at']

    def validate_code(self, value):
        value = (value or '').strip().upper()
        if not value:
            raise serializers.ValidationError('Coupon code is required.')
        qs = Coupon.objects.filter(code=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A coupon with this code already exists.')
        return value

    def validate_discount_value(self, value):
        if value is None or value < 0:
            raise serializers.ValidationError('Discount value cannot be negative.')
        return value

    def validate_minimum_order_amount(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('Minimum order amount cannot be negative.')
        return value

    def validate_maximum_discount_amount(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('Maximum discount cannot be negative.')
        return value

    def validate(self, attrs):
        discount_type = attrs.get('discount_type', getattr(self.instance, 'discount_type', 'percentage'))
        discount_value = attrs.get('discount_value', getattr(self.instance, 'discount_value', None))
        start_at = attrs.get('start_at', getattr(self.instance, 'start_at', None))
        end_at = attrs.get('end_at', getattr(self.instance, 'end_at', None))
        if discount_type == 'percentage' and discount_value is not None and discount_value > 100:
            raise serializers.ValidationError({'discount_value': 'Percentage discount cannot exceed 100.'})
        if start_at and end_at and end_at < start_at:
            raise serializers.ValidationError({'end_at': 'end_at must be greater than or equal to start_at.'})
        applies_to = attrs.get('applies_to', getattr(self.instance, 'applies_to', 'all'))
        if applies_to == 'categories':
            names = attrs.get('category_names', getattr(self.instance, 'category_names', []) or [])
            if not names:
                raise serializers.ValidationError({'category_names': 'Provide at least one category name.'})
        return attrs

    def create(self, validated_data):
        products = validated_data.pop('products', [])
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            validated_data['created_by'] = request.user
        coupon = Coupon.objects.create(**validated_data)
        if products:
            coupon.products.set(products)
        return coupon

    def update(self, instance, validated_data):
        products = validated_data.pop('products', None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()
        if products is not None:
            instance.products.set(products)
        return instance


class CouponRedemptionSerializer(serializers.ModelSerializer):
    coupon_code = serializers.CharField(source='coupon.code', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True, allow_null=True)

    class Meta:
        model = CouponRedemption
        fields = [
            'id',
            'coupon',
            'coupon_code',
            'user',
            'username',
            'order',
            'order_number',
            'discount_amount',
            'status',
            'created_at',
            'redeemed_at',
        ]
        read_only_fields = fields


class DeliveryZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = [
            'id',
            'name',
            'postal_codes',
            'city',
            'state',
            'is_active',
            'delivery_charge',
            'minimum_order_amount',
            'free_delivery_threshold',
            'eta_min_minutes',
            'eta_max_minutes',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_postal_codes(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('postal_codes must be a list of PIN strings.')
        cleaned = []
        for raw in value:
            pin = ''.join(ch for ch in str(raw) if ch.isdigit())
            if len(pin) != 6:
                raise serializers.ValidationError(f'Invalid PIN in postal_codes: {raw}')
            cleaned.append(pin)
        return cleaned


class DeliverySettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliverySettings
        fields = [
            'delivery_enabled',
            'default_delivery_charge',
            'free_delivery_threshold',
            'bakery_latitude',
            'bakery_longitude',
            'max_delivery_radius_km',
            'per_km_charge',
            'updated_at',
        ]
        read_only_fields = ['updated_at']

