from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    mobile_number = models.CharField(max_length=20)
    is_verified = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    mobile_verified = models.BooleanField(default=False)

    verification_token = models.CharField(max_length=128, blank=True, null=True)
    verification_token_created_at = models.DateTimeField(blank=True, null=True)
    email_verification_token = models.CharField(max_length=128, blank=True, null=True)
    email_verification_expires_at = models.DateTimeField(blank=True, null=True)

    otp_code = models.CharField(max_length=6, blank=True, null=True)
    otp_created_at = models.DateTimeField(blank=True, null=True)
    otp_hash = models.CharField(max_length=255, blank=True, null=True)
    otp_expires_at = models.DateTimeField(blank=True, null=True)
    otp_attempts = models.PositiveIntegerField(default=0)
    otp_last_sent_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} profile"
