import hashlib
import random
import secrets
from datetime import timedelta

from django.contrib.auth import login
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UserProfile
from .serializers import SigninSerializer, SignupSerializer


def _verification_link(token):
    return f"http://127.0.0.1:8000/api/accounts/verify-email/?token={token}"


def _mask_email(email):
    if not email:
        return ""
    name, domain = email.split('@', 1)
    visible = name[:2] + ('*' * max(len(name) - 2, 0)) if len(name) > 2 else name[:1]
    return f"{visible}@{domain}"


def _mask_mobile(mobile):
    if not mobile:
        return ""
    digits = ''.join(ch for ch in mobile if ch.isdigit())
    if len(digits) <= 4:
        return digits
    return f"{digits[:2]}******{digits[-2:]}"


class SignupView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        if serializer.is_valid():
            user, profile = serializer.save()
            token, _ = Token.objects.get_or_create(user=user)
            return Response(
                {
                    'message': 'Account created successfully. Please verify your email or mobile number before you can sign in.',
                    'token': token.key,
                    'verification_link': _verification_link(profile.verification_token),
                    'verification_token': profile.verification_token,
                    'otp': profile.otp_code,
                    'user': {
                        'id': user.id,
                        'username': user.username,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'email': user.email,
                        'mobile_number': profile.mobile_number,
                    },
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SigninView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = SigninSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']
            login(request, user)
            token, _ = Token.objects.get_or_create(user=user)
            return Response(
                {
                    'message': 'Signed in successfully.',
                    'token': token.key,
                    'user': {
                        'id': user.id,
                        'username': user.username,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'email': user.email,
                    },
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SendVerificationView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = request.data.get('email')
        method = request.data.get('method', 'email')
        if not email:
            return Response({'detail': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        profile, _ = UserProfile.objects.get_or_create(user=user)
        now = timezone.now()
        if profile.otp_last_sent_at and now - profile.otp_last_sent_at < timedelta(minutes=1):
            return Response({'detail': 'Please wait before requesting another verification code.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        profile.verification_token = secrets.token_urlsafe(32)
        profile.verification_token_created_at = now
        profile.email_verification_token = profile.verification_token
        profile.email_verification_expires_at = now + timedelta(days=1)

        otp = str(random.randint(100000, 999999))
        profile.otp_code = otp
        profile.otp_hash = make_password(otp)
        profile.otp_created_at = now
        profile.otp_expires_at = now + timedelta(minutes=10)
        profile.otp_attempts = 0
        profile.otp_last_sent_at = now
        profile.save()

        return Response(
            {
                'message': 'A new verification link and OTP have been generated.',
                'verification_link': _verification_link(profile.verification_token),
                'verification_token': profile.verification_token,
                'otp': profile.otp_code,
                'method': method,
                'email': email,
                'masked_email': _mask_email(email),
                'masked_mobile': _mask_mobile(profile.mobile_number),
            },
            status=status.HTTP_200_OK,
        )


class VerifyEmailView(APIView):
    authentication_classes = []
    permission_classes = []

    def _verify(self, token):
        if not token:
            return None, Response({'detail': 'Verification token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        profile = UserProfile.objects.filter(email_verification_token=token).first()
        if not profile:
            profile = UserProfile.objects.filter(verification_token=token).first()

        if not profile:
            return None, Response({'detail': 'Invalid or expired verification link.'}, status=status.HTTP_400_BAD_REQUEST)

        if profile.email_verification_expires_at and timezone.now() > profile.email_verification_expires_at:
            return None, Response({'detail': 'This verification link has expired. Please request a new one.'}, status=status.HTTP_400_BAD_REQUEST)

        profile.is_verified = True
        profile.email_verified = True
        profile.user.is_active = True
        profile.verification_token = None
        profile.email_verification_token = None
        profile.email_verification_expires_at = None
        profile.verification_token_created_at = None
        profile.user.save()
        profile.save()
        return profile, Response({'message': 'Email verified successfully. You can now sign in.'}, status=status.HTTP_200_OK)

    def get(self, request):
        token = request.query_params.get('token')
        _, response = self._verify(token)
        return response

    def post(self, request):
        token = request.data.get('token')
        _, response = self._verify(token)
        return response


class VerifyOtpView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = request.data.get('email')
        otp = request.data.get('otp')

        if not email or not otp:
            return Response({'detail': 'Email and OTP are required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        profile = getattr(user, 'profile', None)
        if not profile:
            return Response({'detail': 'User profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        if profile.otp_expires_at and timezone.now() > profile.otp_expires_at:
            return Response({'detail': 'This OTP has expired. Please request a new one.'}, status=status.HTTP_400_BAD_REQUEST)

        if profile.otp_attempts >= 5:
            return Response({'detail': 'Too many OTP attempts. Please request a new code.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        if not profile.otp_hash or not check_password(str(otp).strip(), profile.otp_hash):
            profile.otp_attempts += 1
            profile.save(update_fields=['otp_attempts'])
            return Response({'detail': 'Invalid OTP.'}, status=status.HTTP_400_BAD_REQUEST)

        profile.is_verified = True
        profile.mobile_verified = True
        profile.user.is_active = True
        profile.otp_code = None
        profile.otp_hash = None
        profile.otp_created_at = None
        profile.otp_expires_at = None
        profile.otp_attempts = 0
        profile.user.save()
        profile.save()

        return Response({'message': 'Mobile verification successful. You can now sign in.'}, status=status.HTTP_200_OK)


class MeView(APIView):
    def get(self, request):
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

        user = request.user
        profile = getattr(user, 'profile', None)
        return Response({
            'id': user.id,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'mobile_number': profile.mobile_number if profile else '',
            'is_verified': bool(profile and profile.is_verified),
            'masked_email': _mask_email(user.email),
            'masked_mobile': _mask_mobile(profile.mobile_number if profile else ''),
        })
