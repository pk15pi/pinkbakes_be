from django.contrib import admin

from .models import DeliverySettings, DeliveryZone, InventoryTransaction, Order, Payment, Product, Refund


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'payment', 'amount', 'status', 'initiated_by_type', 'created_at', 'processed_at')
    list_filter = ('status', 'initiated_by_type', 'gateway')
    search_fields = ('gateway_refund_id', 'order__order_number', 'reason')
    readonly_fields = ('created_at', 'updated_at', 'processed_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer_name', 'status', 'payment_status', 'total_amount', 'cancelled_at', 'created_at')
    list_filter = ('status', 'payment_status')
    search_fields = ('order_number', 'customer_email', 'customer_mobile')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'user', 'amount', 'status', 'gateway_order_id', 'gateway_payment_id', 'created_at')
    list_filter = ('status', 'gateway')
    search_fields = ('gateway_order_id', 'gateway_payment_id')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'category', 'price', 'availability', 'status',
        'available_quantity', 'reserved_quantity', 'sold_quantity', 'low_stock_threshold',
    )
    list_filter = ('availability', 'status', 'category')
    search_fields = ('name', 'slug')


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'product', 'adjustment_type', 'quantity_change',
        'previous_quantity', 'new_quantity', 'reference_type', 'reference_id', 'created_at',
    )
    list_filter = ('adjustment_type', 'reference_type')
    search_fields = ('product__name', 'reference_id', 'reason')
    readonly_fields = ('created_at',)

from .models import Coupon, CouponRedemption

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'discount_value', 'is_active', 'total_used', 'usage_limit')
    search_fields = ('code', 'name')
    list_filter = ('is_active', 'discount_type', 'applies_to')


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'coupon', 'user', 'order', 'discount_amount', 'status', 'created_at')
    list_filter = ('status',)



@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'delivery_charge', 'minimum_order_amount', 'free_delivery_threshold', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'city', 'state')


@admin.register(DeliverySettings)
class DeliverySettingsAdmin(admin.ModelAdmin):
    list_display = ('delivery_enabled', 'default_delivery_charge', 'free_delivery_threshold', 'max_delivery_radius_km', 'updated_at')
