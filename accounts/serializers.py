import re
import secrets
from datetime import timedelta

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import serializers

from .models import UserProfile


def generate_otp():
    return f"{secrets.randbelow(900000) + 100000}"


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    mobile_number = serializers.CharField(required=True)

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'username', 'email', 'mobile_number', 'password')

    def validate_email(self, value):
        value = value.strip()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value

    def validate_mobile_number(self, value):
        cleaned = value.strip()
        if not re.fullmatch(r'^[0-9+()\-\s]{10,20}$', cleaned):
            raise serializers.ValidationError('Enter a valid mobile number.')
        if len(re.sub(r'\D', '', cleaned)) < 10:
            raise serializers.ValidationError('Mobile number must be at least 10 digits.')
        return cleaned

    def create(self, validated_data):
        password = validated_data.pop('password')
        mobile_number = validated_data.pop('mobile_number')
        email = validated_data['email'].strip()
        username = validated_data['username'].strip()

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=validated_data.get('first_name', '').strip(),
            last_name=validated_data.get('last_name', '').strip(),
            is_active=False,
        )

        verification_token = secrets.token_urlsafe(32)
        otp_value = generate_otp()
        profile = UserProfile.objects.create(
            user=user,
            mobile_number=mobile_number,
            is_verified=False,
            email_verified=False,
            mobile_verified=False,
            verification_token=verification_token,
            verification_token_created_at=timezone.now(),
            email_verification_token=verification_token,
            email_verification_expires_at=timezone.now() + timedelta(days=1),
            otp_code=None,
            otp_created_at=timezone.now(),
            otp_hash=make_password(otp_value),
            otp_expires_at=timezone.now() + timedelta(minutes=10),
            otp_attempts=0,
            otp_last_sent_at=timezone.now(),
        )
        # Plaintext OTP is only used by the view for email/SMS delivery, not stored.
        profile._plaintext_otp = otp_value
        return user, profile


class SigninSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        username = attrs.get('username')
        password = attrs.get('password')

        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError('Invalid username or password.')

        if not user.is_active:
            raise serializers.ValidationError('Your account has not been verified. Please verify your email or mobile number before signing in.')

        profile = getattr(user, 'profile', None)
        if not profile or not profile.is_verified:
            raise serializers.ValidationError('Your account has not been verified. Please verify your email or mobile number before signing in.')

        attrs['user'] = user
        return attrs
