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
    # Inventory: available is sellable; reserved held for unpaid orders; sold after payment.
    available_quantity = models.PositiveIntegerField(default=50)
    reserved_quantity = models.PositiveIntegerField(default=0)
    sold_quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    stock_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-featured', '-created_at']
        indexes = [
            models.Index(fields=['is_active', 'status', 'featured', 'created_at'], name='catalog_prod_list_idx'),
            models.Index(fields=['category', 'is_active', 'status'], name='catalog_prod_cat_idx'),
            models.Index(fields=['availability'], name='catalog_prod_avail_idx'),
        ]

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


class EmployeeCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'employee categories'

    def __str__(self):
        return self.name


class Employee(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('INACTIVE', 'Inactive'),
        ('AVAILABLE', 'Available'),
        ('BUSY', 'Busy'),
        ('ON_LEAVE', 'On Leave'),
    ]

    user = models.OneToOneField(User, related_name='employee_profile', on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey(EmployeeCategory, related_name='employees', on_delete=models.PROTECT)
    employee_id = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    designation = models.CharField(max_length=120, blank=True, default='')
    contact_number = models.CharField(max_length=20)
    email = models.EmailField(blank=True, default='')
    photo = models.URLField(blank=True, default='')
    date_of_joining = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True, default='')
    emergency_contact = models.CharField(max_length=20, blank=True, default='')
    employment_status = models.CharField(
        max_length=20,
        choices=[
            ('ACTIVE', 'Active'),
            ('INACTIVE', 'Inactive'),
            ('ON_LEAVE', 'On Leave'),
            ('TERMINATED', 'Terminated'),
        ],
        default='ACTIVE',
    )
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
    landmark = models.CharField(max_length=120, blank=True, default='')
    shipping_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    shipping_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    address = models.ForeignKey(
        'accounts.CustomerAddress', related_name='orders', on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    delivery_zone = models.ForeignKey(
        'DeliveryZone', related_name='orders', on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    coupon = models.ForeignKey(
        'Coupon', related_name='orders', on_delete=models.SET_NULL, null=True, blank=True,
    )
    coupon_code = models.CharField(max_length=40, blank=True, default='')
    coupon_discount_type = models.CharField(max_length=20, blank=True, default='')
    coupon_discount_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    coupon_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='ORDER_CONFIRMED')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    delivery_employee = models.ForeignKey('Employee', related_name='orders_assigned', on_delete=models.SET_NULL, null=True, blank=True)
    delivery_assigned_at = models.DateTimeField(null=True, blank=True)
    delivery_started_at = models.DateTimeField(null=True, blank=True)
    delivery_completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    cancellation_reason = models.TextField(blank=True, default='')
    cancelled_by = models.ForeignKey(User, related_name='cancelled_orders', on_delete=models.SET_NULL, null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
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

    def is_customer_cancellable(self):
        """Customer may cancel early-stage or unpaid orders."""
        if self.status == 'CANCELLED':
            return False
        if self.status in ('OUT_FOR_DELIVERY', 'DELIVERED'):
            return False
        if self.status in ('PENDING', 'ORDER_CONFIRMED', 'PREPARING'):
            return True
        if self.payment_status in ('pending', 'failed') and self.status not in (
            'DELIVERY_BOY_ASSIGNED', 'PACKING', 'READY_FOR_DELIVERY', 'OUT_FOR_DELIVERY', 'DELIVERED', 'CANCELLED',
        ):
            return True
        return False

    def is_admin_cancellable(self):
        """Admin may cancel any order that is not delivered or already cancelled."""
        return self.status not in ('DELIVERED', 'CANCELLED')


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
        ('refund_pending', 'Refund Pending'),
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



class Refund(models.Model):
    STATUS_CHOICES = [
        ('requested', 'Requested'),
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    INITIATED_BY_TYPE_CHOICES = [
        ('customer', 'Customer'),
        ('admin', 'Admin'),
        ('system', 'System'),
    ]

    order = models.ForeignKey('Order', related_name='refunds', on_delete=models.CASCADE)
    payment = models.ForeignKey('Payment', related_name='refunds', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='refunds', on_delete=models.CASCADE)
    gateway = models.CharField(max_length=40, default='razorpay')
    gateway_refund_id = models.CharField(max_length=120, blank=True, default='')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default='INR')
    reason = models.TextField(blank=True, default='')
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='requested')
    initiated_by = models.ForeignKey(
        User, related_name='initiated_refunds', on_delete=models.SET_NULL, null=True, blank=True,
    )
    initiated_by_type = models.CharField(max_length=20, choices=INITIATED_BY_TYPE_CHOICES, default='customer')
    failure_reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['order', 'created_at']),
            models.Index(fields=['payment', 'status']),
            models.Index(fields=['gateway_refund_id']),
            models.Index(fields=['status', 'created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['gateway', 'gateway_refund_id'],
                condition=~models.Q(gateway_refund_id=''),
                name='unique_gateway_refund_id_when_set',
            ),
        ]

    def __str__(self):
        return f'Refund {self.id} for order {self.order_id} - {self.status}'


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
        ('order_status', 'Order Status Updated'),
        ('order_cancel', 'Order Cancelled'),
        ('refund', 'Refund Action'),
        ('inventory_adjust', 'Inventory Adjusted'),
        ('coupon_create', 'Coupon Created'),
        ('coupon_update', 'Coupon Updated'),
        ('employee_assign', 'Delivery Employee Assigned'),
        ('employee_unassign', 'Delivery Employee Unassigned'),
        ('employee_create', 'Employee Created'),
        ('employee_update', 'Employee Updated'),
        ('employee_deactivate', 'Employee Deactivated'),
        ('employee_category_create', 'Employee Category Created'),
        ('employee_category_update', 'Employee Category Updated'),
        ('customer_status', 'Customer Account Status'),
        ('export', 'Data Export'),
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


class InventoryTransaction(models.Model):
    ADJUSTMENT_CHOICES = [
        ('INITIAL_STOCK', 'Initial Stock'),
        ('RESTOCK', 'Restock'),
        ('MANUAL_ADJUSTMENT', 'Manual Adjustment'),
        ('ORDER_RESERVED', 'Order Reserved'),
        ('ORDER_CONSUMED', 'Order Consumed'),
        ('ORDER_RELEASED', 'Order Released'),
        ('ORDER_RESTORED', 'Order Restored'),
        ('CORRECTION', 'Correction'),
    ]

    product = models.ForeignKey('Product', related_name='inventory_transactions', on_delete=models.CASCADE)
    quantity_change = models.IntegerField(default=0)
    previous_quantity = models.IntegerField(default=0)
    new_quantity = models.IntegerField(default=0)
    adjustment_type = models.CharField(max_length=30, choices=ADJUSTMENT_CHOICES)
    reason = models.TextField(blank=True, default='')
    reference_type = models.CharField(max_length=40, blank=True, default='')
    reference_id = models.CharField(max_length=64, blank=True, default='')
    performed_by = models.ForeignKey(
        User, related_name='inventory_transactions', on_delete=models.SET_NULL, null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product', 'created_at']),
            models.Index(fields=['reference_type', 'reference_id']),
            models.Index(fields=['adjustment_type', 'created_at']),
        ]

    def __str__(self):
        return f'{self.product_id} {self.adjustment_type} {self.quantity_change}'



class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('fixed_amount', 'Fixed Amount'),
    ]
    APPLIES_TO_CHOICES = [
        ('all', 'All products'),
        ('products', 'Specific products'),
        ('categories', 'Specific categories'),
    ]

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120, blank=True, default='')
    description = models.TextField(blank=True, default='')
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='percentage')
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    minimum_order_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    maximum_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    usage_limit_per_user = models.PositiveIntegerField(null=True, blank=True)
    total_used = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    applies_to = models.CharField(max_length=20, choices=APPLIES_TO_CHOICES, default='all')
    products = models.ManyToManyField('Product', related_name='coupons', blank=True)
    # Product.category is a CharField (not an FK model); store matching category names as JSON list.
    category_names = models.JSONField(default=list, blank=True)
    exclude_already_discounted = models.BooleanField(default=False)
    new_customers_only = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User, related_name='created_coupons', on_delete=models.SET_NULL, null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active', 'start_at', 'end_at']),
        ]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        super().save(*args, **kwargs)


class CouponRedemption(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('redeemed', 'Redeemed'),
        ('voided', 'Voided'),
    ]

    coupon = models.ForeignKey('Coupon', related_name='redemptions', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='coupon_redemptions', on_delete=models.CASCADE)
    order = models.ForeignKey(
        'Order', related_name='coupon_redemptions', on_delete=models.SET_NULL, null=True, blank=True,
    )
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['coupon', 'user', 'status']),
            models.Index(fields=['order', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f'{self.coupon_id} / {self.user_id} / {self.status}'


class DeliveryZone(models.Model):
    """PIN-based delivery zone. postal_codes is a JSON list of 6-digit PIN strings."""

    name = models.CharField(max_length=120)
    postal_codes = models.JSONField(default=list, blank=True)
    city = models.CharField(max_length=80, blank=True, default='')
    state = models.CharField(max_length=80, blank=True, default='')
    is_active = models.BooleanField(default=True)
    delivery_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    minimum_order_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    free_delivery_threshold = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    eta_min_minutes = models.PositiveIntegerField(null=True, blank=True)
    eta_max_minutes = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['is_active', 'name']),
        ]

    def __str__(self):
        return self.name

    def normalized_postal_codes(self):
        codes = []
        for raw in (self.postal_codes or []):
            code = ''.join(ch for ch in str(raw) if ch.isdigit())
            if code:
                codes.append(code)
        return codes


class DeliverySettings(models.Model):
    """Singleton row for bakery location, radius, and global delivery defaults."""

    delivery_enabled = models.BooleanField(default=True)
    default_delivery_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    free_delivery_threshold = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    bakery_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    bakery_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    max_delivery_radius_km = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    per_km_charge = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Delivery settings'
        verbose_name_plural = 'Delivery settings'

    def __str__(self):
        return 'Delivery settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
