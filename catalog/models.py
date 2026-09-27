from django.db import models


class Product(models.Model):
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    description = models.TextField()
    short_description = models.TextField(blank=True, default='')
    image = models.URLField(blank=True, default='')
    gallery = models.JSONField(default=list, blank=True)
    badge = models.CharField(max_length=50, blank=True, default='')
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=4.8)
    featured = models.BooleanField(default=False)
    delivery_time = models.CharField(max_length=40, blank=True, default='24-48 hours')
    three_d_model = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-featured', '-created_at']

    def __str__(self):
        return self.name


class Review(models.Model):
    product = models.ForeignKey('Product', related_name='reviews', on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} review for {self.product.name}'
