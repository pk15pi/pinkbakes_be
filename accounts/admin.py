from django.contrib import admin

from .models import CustomerAddress, PasswordResetToken, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "mobile_number", "is_verified", "email_verified", "mobile_verified")
    search_fields = ("user__username", "user__email", "mobile_number")


@admin.register(CustomerAddress)
class CustomerAddressAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "full_name", "city", "postal_code", "address_type", "is_default", "updated_at")
    list_filter = ("address_type", "is_default", "state")
    search_fields = ("full_name", "postal_code", "user__username", "mobile_number")


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "expires_at", "used_at", "created_at")
    search_fields = ("user__username",)
