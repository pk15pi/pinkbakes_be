from django.contrib import admin

from .models import DeliverySettings, DeliveryZone, Employee, EmployeeCategory, InventoryTransaction, Order, Payment, Product, Refund


@admin.register(EmployeeCategory)
class EmployeeCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('employee_id', 'name', 'category', 'employment_status', 'status', 'contact_number')
    list_filter = ('category', 'employment_status', 'status')
    search_fields = ('employee_id', 'name', 'contact_number', 'email')


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
    exclude = ('gateway_signature',)  # Phase 18: never expose gateway signatures


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

from .models import DeliverySlot, GalleryItem, Testimonial


@admin.register(GalleryItem)
class GalleryItemAdmin(admin.ModelAdmin):
    list_display = ('caption', 'alt_text', 'sort_order', 'is_active')
    list_editable = ('sort_order', 'is_active')


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ('name', 'tag', 'rating', 'sort_order', 'is_active')
    list_editable = ('sort_order', 'is_active')


@admin.register(DeliverySlot)
class DeliverySlotAdmin(admin.ModelAdmin):
    list_display = ('label', 'start_time', 'end_time', 'sort_order', 'is_active')
    list_editable = ('sort_order', 'is_active')


# ---- Phase 18: register every remaining catalog model ----
from .models import (  # noqa: E402
    AdminActivity, Cart, CartItem, DeliveryLocation, OrderItem, OrderStatusHistory,
    ProductView, Review, WishlistItem,
)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'user', 'name', 'rating', 'status', 'created_at', 'approved_at')
    list_filter = ('status', 'rating')
    search_fields = ('name', 'comment', 'product__name', 'user__username')
    ordering = ('-created_at',)
    raw_id_fields = ('product', 'user', 'order', 'order_item', 'approved_by')
    readonly_fields = ('created_at', 'updated_at', 'approved_at', 'rejected_at')


@admin.register(ProductView)
class ProductViewAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'user', 'created_at')
    search_fields = ('product__name', 'user__username')
    ordering = ('-created_at',)
    raw_id_fields = ('product', 'user')
    exclude = ('session_key', 'ip_address')
    readonly_fields = ('created_at',)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'product_name', 'unit_price', 'quantity', 'subtotal', 'created_at')
    search_fields = ('order__order_number', 'product_name')
    ordering = ('-created_at',)
    raw_id_fields = ('order', 'product')
    readonly_fields = ('created_at',)


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'status', 'message', 'changed_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('order__order_number', 'message')
    ordering = ('-created_at',)
    raw_id_fields = ('order', 'changed_by')
    readonly_fields = ('created_at',)


@admin.register(DeliveryLocation)
class DeliveryLocationAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'employee', 'latitude', 'longitude', 'accuracy', 'timestamp')
    search_fields = ('order__order_number', 'employee__name')
    ordering = ('-timestamp',)
    raw_id_fields = ('order', 'employee')
    readonly_fields = ('created_at',)


@admin.register(AdminActivity)
class AdminActivityAdmin(admin.ModelAdmin):
    """Audit log: read-only in admin."""
    list_display = ('id', 'admin_user', 'action', 'entity_type', 'entity_id', 'created_at')
    list_filter = ('action', 'entity_type')
    search_fields = ('description', 'admin_user__username', 'entity_type')
    ordering = ('-created_at',)
    exclude = ('ip_address',)
    readonly_fields = ('admin_user', 'action', 'entity_type', 'entity_id', 'description', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    raw_id_fields = ('product',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'updated_at')
    search_fields = ('user__username', 'user__email')
    ordering = ('-updated_at',)
    raw_id_fields = ('user',)
    readonly_fields = ('created_at', 'updated_at')
    inlines = (CartItemInline,)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'cart', 'product', 'quantity', 'updated_at')
    search_fields = ('product__name', 'cart__user__username')
    ordering = ('-updated_at',)
    raw_id_fields = ('cart', 'product')
    readonly_fields = ('options_key', 'created_at', 'updated_at')


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'product', 'created_at')
    search_fields = ('user__username', 'product__name')
    ordering = ('-created_at',)
    raw_id_fields = ('user', 'product')
    readonly_fields = ('created_at',)