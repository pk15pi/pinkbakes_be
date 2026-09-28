from django.contrib import admin

from .models import InventoryTransaction, Order, Payment, Product, Refund


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
