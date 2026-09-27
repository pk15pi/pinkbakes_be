from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models
from django.utils.text import slugify


User = get_user_model()


class Product(models.Model):
    AVAILABILITY_CHOICES = [
        ('in_stock', 'In Stock'),
        ('low_stock', 'Low Stock'),
        ('out_of_stock', 'Out of Stock'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ]

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=150, unique=True, blank=True)
    category = models.CharField(max_length=80, default='Birthday Cakes')
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.PositiveSmallIntegerField(default=0)
    short_description = models.TextField(blank=True, default='')
    description = models.TextField(blank=True, default='')
    main_image = models.URLField(blank=True, default='')
    images = models.JSONField(default=list, blank=True)
    three_d_model = models.URLField(blank=True, default='')
    three_d_assets = models.JSONField(default=list, blank=True)
    availability = models.CharField(max_length=30, choices=AVAILABILITY_CHOICES, default='in_stock')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='published')
    badge = models.CharField(max_length=50, blank=True, default='')
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0)
    featured = models.BooleanField(default=False)
    delivery_time = models.CharField(max_length=40, blank=True, default='24-48 hours')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-featured', '-created_at']

    def __str__(self):
        return self.name

    @property
    def discounted_price(self):
        discount_value = Decimal(self.discount or 0) / Decimal(100)
        return (self.price * (Decimal(1) - discount_value)).quantize(Decimal('0.01'))

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            index = 1
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base_slug}-{index}'
                index += 1
            self.slug = slug
        if self.discount and self.discount > 100:
            self.discount = 100
        super().save(*args, **kwargs)


class Review(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    product = models.ForeignKey('Product', related_name='reviews', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='reviews', on_delete=models.CASCADE, null=True, blank=True)
    order = models.ForeignKey('Order', related_name='reviews', on_delete=models.SET_NULL, null=True, blank=True)
    order_item = models.ForeignKey('OrderItem', related_name='reviews', on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=80, blank=True, default='')
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_comment = models.TextField(blank=True, default='')
    approved_by = models.ForeignKey(User, related_name='approved_reviews', on_delete=models.SET_NULL, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'order_item'], name='unique_review_per_order_item'),
        ]
        indexes = [
            models.Index(fields=['product', 'status', 'created_at']),
            models.Index(fields=['rating', 'status']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['order', 'status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        customer_name = self.name or (self.user.username if self.user else 'Customer')
        return f'{customer_name} review for {self.product.name}'


class ProductView(models.Model):
    product = models.ForeignKey('Product', related_name='product_views', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='product_views', on_delete=models.SET_NULL, null=True, blank=True)
    session_key = models.CharField(max_length=40, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product', 'created_at']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f'View for {self.product.name}'


class Employee(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('INACTIVE', 'Inactive'),
        ('AVAILABLE', 'Available'),
        ('BUSY', 'Busy'),
        ('ON_LEAVE', 'On Leave'),
    ]

    user = models.OneToOneField(User, related_name='employee_profile', on_delete=models.SET_NULL, null=True, blank=True)
    employee_id = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    contact_number = models.CharField(max_length=20)
    email = models.EmailField(blank=True, default='')
    photo = models.URLField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.employee_id})'


class Order(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ORDER_CONFIRMED', 'Order Confirmed'),
        ('PREPARING', 'Preparing'),
        ('DELIVERY_BOY_ASSIGNED', 'Delivery Boy Assigned'),
        ('PACKING', 'Packing'),
        ('READY_FOR_DELIVERY', 'Ready for Delivery'),
        ('OUT_FOR_DELIVERY', 'Out for Delivery'),
        ('DELIVERED', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    ]

    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    user = models.ForeignKey(User, related_name='orders', on_delete=models.CASCADE)
    order_number = models.CharField(max_length=40, unique=True)
    customer_name = models.CharField(max_length=120)
    customer_email = models.EmailField()
    customer_mobile = models.CharField(max_length=20)
    shipping_address = models.CharField(max_length=255)
    shipping_address_2 = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=80)
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='ORDER_CONFIRMED')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    delivery_employee = models.ForeignKey('Employee', related_name='orders_assigned', on_delete=models.SET_NULL, null=True, blank=True)
    delivery_assigned_at = models.DateTimeField(null=True, blank=True)
    delivery_started_at = models.DateTimeField(null=True, blank=True)
    delivery_completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['order_number']),
            models.Index(fields=['delivery_employee', 'created_at']),
        ]

    def __str__(self):
        return f'{self.order_number} - {self.customer_name}'


class OrderItem(models.Model):
    order = models.ForeignKey('Order', related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey('Product', related_name='order_items', on_delete=models.SET_NULL, null=True, blank=True)
    product_name = models.CharField(max_length=160)
    product_image = models.URLField(blank=True, default='')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField(default=1)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.product_name} x {self.quantity}'


class Payment(models.Model):
    STATUS_CHOICES = [
        ('created', 'Created'),
        ('pending', 'Pending'),
        ('authorized', 'Authorized'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
    ]

    order = models.ForeignKey('Order', related_name='payments', on_delete=models.CASCADE, null=True, blank=True)
    user = models.ForeignKey(User, related_name='payments', on_delete=models.CASCADE)
    gateway = models.CharField(max_length=40, default='razorpay')
    gateway_order_id = models.CharField(max_length=120, unique=True)
    gateway_payment_id = models.CharField(max_length=120, blank=True, default='')
    gateway_signature = models.CharField(max_length=255, blank=True, default='')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default='INR')
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='created')
    payment_method = models.CharField(max_length=40, blank=True, default='')
    failure_reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['order', 'status']),
            models.Index(fields=['gateway_order_id']),
            models.Index(fields=['gateway_payment_id']),
        ]

    def __str__(self):
        return f'{self.user.username} - {self.gateway_order_id} - {self.status}'


class OrderStatusHistory(models.Model):
    order = models.ForeignKey('Order', related_name='status_history', on_delete=models.CASCADE)
    status = models.CharField(max_length=40)
    message = models.CharField(max_length=255, blank=True, default='')
    changed_by = models.ForeignKey(User, related_name='order_status_updates', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['order', 'created_at']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f'{self.order.order_number} - {self.status}'


class DeliveryLocation(models.Model):
    order = models.ForeignKey('Order', related_name='delivery_locations', on_delete=models.CASCADE)
    employee = models.ForeignKey('Employee', related_name='location_updates', on_delete=models.CASCADE)
    latitude = models.FloatField()
    longitude = models.FloatField()
    accuracy = models.FloatField(default=0)
    timestamp = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['order', 'timestamp']),
            models.Index(fields=['employee', 'timestamp']),
        ]

    def __str__(self):
        return f'{self.employee.name} - {self.latitude}, {self.longitude}'


class AdminActivity(models.Model):
    ACTION_CHOICES = [
        ('login', 'Login'),
        ('product_create', 'Product Created'),
        ('product_update', 'Product Updated'),
        ('product_delete', 'Product Deleted'),
        ('product_image_upload', 'Product Image Upload'),
        ('product_3d_upload', '3D Asset Upload'),
        ('review_moderate', 'Review Moderated'),
        ('settings_update', 'Settings Updated'),
    ]

    admin_user = models.ForeignKey(User, related_name='admin_activities', on_delete=models.CASCADE)
    action = models.CharField(max_length=40, choices=ACTION_CHOICES, default='login')
    entity_type = models.CharField(max_length=40, blank=True, default='')
    entity_id = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['admin_user', 'created_at']),
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['entity_type', 'entity_id']),
        ]

    def __str__(self):
        return f'{self.admin_user.username} - {self.action}'
