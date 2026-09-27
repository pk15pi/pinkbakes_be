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
    name = models.CharField(max_length=80, blank=True, default='')
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='approved')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product', 'status', 'created_at']),
            models.Index(fields=['rating', 'status']),
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
