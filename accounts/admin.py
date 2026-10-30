from django.contrib import admin

from .models import CustomerAddress, PasswordResetToken, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "mobile_number", "is_verified", "email_verified", "mobile_verified")
    search_fields = ("user__username", "user__email", "mobile_number")
    # Phase 18: never expose OTPs or verification tokens in admin forms.
    exclude = ("verification_token", "email_verification_token", "otp_code", "otp_hash")
    readonly_fields = ("verification_token_created_at", "email_verification_expires_at", "otp_created_at", "otp_expires_at", "otp_attempts", "otp_last_sent_at", "created_at", "updated_at")


@admin.register(CustomerAddress)
class CustomerAddressAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "full_name", "city", "postal_code", "address_type", "is_default", "updated_at")
    list_filter = ("address_type", "is_default", "state")
    search_fields = ("full_name", "postal_code", "user__username", "mobile_number")


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "expires_at", "used_at", "created_at")
    search_fields = ("user__username",)
    exclude = ("token_hash",)  # Phase 18: never expose token hashes
    readonly_fields = ("expires_at", "used_at", "created_at")
