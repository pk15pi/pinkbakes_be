import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from notifications import events as notification_events
from notifications.service import notify as notify_event
from .models import PasswordResetToken, UserProfile
from .serializers import SigninSerializer, SignupSerializer

logger = logging.getLogger(__name__)


def _secure_otp():
    return f"{secrets.randbelow(900000) + 100000}"


def _store_otp(profile, otp, *, now=None):
    """Persist OTP hash only — never keep plaintext otp_code."""
    now = now or timezone.now()
    profile.otp_code = None
    profile.otp_hash = make_password(otp)
    profile.otp_created_at = now
    profile.otp_expires_at = now + timedelta(minutes=10)
    profile.otp_attempts = 0
    profile.otp_last_sent_at = now


def _maybe_log_dev_otp(purpose):
    if getattr(settings, "DEBUG", False):
        logger.info("DEV OTP generated for %s (delivered via email/SMS; not in API).", purpose)


def _verification_link(token):
    base_url = getattr(settings, 'FRONTEND_URL', 'https://pinkbakes.com')
    return f"{base_url.rstrip('/')}/verify-email?token={token}"


def _password_reset_link(token):
    base_url = getattr(settings, 'FRONTEND_URL', 'https://pinkbakes.com')
    return f"{base_url.rstrip('/')}/reset-password/{token}"


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


def _normalize_mobile_number(value):
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def _find_user_by_mobile(mobile):
    digits = _normalize_mobile_number(mobile)
    if not digits:
        return None

    for profile in UserProfile.objects.select_related('user').all():
        profile_digits = _normalize_mobile_number(profile.mobile_number)
        if profile_digits == digits:
            return profile.user
        if profile_digits and digits.endswith(profile_digits):
            return profile.user
    return None


class SignupView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        if serializer.is_valid():
            user, profile = serializer.save()
            verification_url = _verification_link(profile.verification_token)
            otp_plain = None
            # OTP was hashed at create; regenerate plaintext only for email/SMS delivery.
            otp_plain = _secure_otp()
            _store_otp(profile, otp_plain)
            profile.save(update_fields=[
                "otp_code", "otp_hash", "otp_created_at", "otp_expires_at",
                "otp_attempts", "otp_last_sent_at",
            ])
            try:
                notify_event(
                    notification_events.EMAIL_VERIFICATION_REQUIRED,
                    user=user,
                    email=user.email,
                    context={
                        "user_name": user.first_name or user.username,
                        "verification_url": verification_url,
                        "otp": otp_plain,
                        "message": "Please verify your PinkBakes account.",
                    },
                    idempotency_key=f"email_verify:{user.pk}:{profile.verification_token[:12]}",
                    reference_type="user",
                    reference_id=str(user.pk),
                )
                notify_event(
                    notification_events.USER_REGISTERED,
                    user=user,
                    context={"message": "Welcome to PinkBakes!", "title": "Welcome"},
                    channels=["in_app"],
                    idempotency_key=f"user_registered:{user.pk}",
                    reference_type="user",
                    reference_id=str(user.pk),
                )
            except Exception:
                pass
            _maybe_log_dev_otp("signup")
            return Response(
                {
                    "message": (
                        "Account created successfully. Please verify your email or mobile number "
                        "before you can sign in. Check your inbox for the verification link and code."
                    ),
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "email": user.email,
                        "mobile_number": profile.mobile_number,
                    },
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class SigninView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = SigninSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            login(request, user)
            token, _ = Token.objects.get_or_create(user=user)
            return Response(
                {
                    "message": "Signed in successfully.",
                    "token": token.key,
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "email": user.email,
                    },
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class SendVerificationView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        method = request.data.get("method", "email")
        generic = {
            "message": (
                "If an account exists for this email, a new verification link and OTP have been sent."
            ),
            "method": method,
            "masked_email": _mask_email(email) if email else "",
        }
        if not email:
            return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response(generic, status=status.HTTP_200_OK)

        profile, _ = UserProfile.objects.get_or_create(user=user)
        now = timezone.now()
        if profile.otp_last_sent_at and now - profile.otp_last_sent_at < timedelta(minutes=1):
            return Response(
                {"detail": "Please wait before requesting another verification code."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        profile.verification_token = secrets.token_urlsafe(32)
        profile.verification_token_created_at = now
        profile.email_verification_token = profile.verification_token
        profile.email_verification_expires_at = now + timedelta(days=1)

        otp = _secure_otp()
        _store_otp(profile, otp, now=now)
        profile.save()

        verification_url = _verification_link(profile.verification_token)
        try:
            notify_event(
                notification_events.EMAIL_VERIFICATION_REQUIRED,
                user=user,
                email=user.email,
                context={
                    "user_name": user.first_name or user.username,
                    "verification_url": verification_url,
                    "otp": otp,
                    "message": "Please verify your PinkBakes account.",
                },
                force=True,
                reference_type="user",
                reference_id=str(user.pk),
            )
            if method == "mobile":
                notify_event(
                    notification_events.MOBILE_VERIFICATION_REQUIRED,
                    user=user,
                    phone=getattr(profile, "mobile_number", "") or "",
                    email=user.email,
                    context={
                        "user_name": user.first_name or user.username,
                        "verification_url": verification_url,
                        "otp": otp,
                        "message": "Mobile verification code generated.",
                    },
                    channels=["sms", "email", "in_app"],
                    force=True,
                    reference_type="user",
                    reference_id=str(user.pk),
                )
        except Exception:
            pass

        _maybe_log_dev_otp("send-verification")
        generic["masked_mobile"] = _mask_mobile(profile.mobile_number)
        return Response(generic, status=status.HTTP_200_OK)



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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

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


class RequestLoginOtpView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        mobile = (request.data.get("mobile") or "").strip()
        generic = {
            "message": "If this mobile number is registered, an OTP has been sent to it."
        }
        if not mobile:
            return Response({"detail": "Mobile number is required."}, status=status.HTTP_400_BAD_REQUEST)

        user = _find_user_by_mobile(mobile)
        if not user:
            return Response(generic, status=status.HTTP_200_OK)

        profile = getattr(user, "profile", None)
        if not profile:
            return Response(generic, status=status.HTTP_200_OK)

        if not user.is_active or not profile.is_verified:
            # Avoid confirming account state to strangers; same generic message.
            return Response(generic, status=status.HTTP_200_OK)

        now = timezone.now()
        if profile.otp_last_sent_at and now - profile.otp_last_sent_at < timedelta(minutes=1):
            return Response(
                {"detail": "Please wait before requesting another OTP."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        otp = _secure_otp()
        _store_otp(profile, otp, now=now)
        profile.save(
            update_fields=[
                "otp_code",
                "otp_hash",
                "otp_created_at",
                "otp_expires_at",
                "otp_attempts",
                "otp_last_sent_at",
            ]
        )

        try:
            notify_event(
                notification_events.LOGIN_OTP_REQUESTED,
                user=user,
                phone=profile.mobile_number,
                email=user.email,
                context={
                    "message": "Your PinkBakes login code was sent to your email/SMS.",
                    "title": "Login OTP",
                    "otp": otp,
                    "user_name": user.first_name or user.username,
                },
                channels=["sms", "email"],
                force=True,
                reference_type="user",
                reference_id=str(user.pk),
            )
        except Exception:
            pass

        _maybe_log_dev_otp("login-otp")
        return Response(generic, status=status.HTTP_200_OK)



class VerifyLoginOtpView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        mobile = (request.data.get("mobile") or "").strip()
        otp = (request.data.get("otp") or "").strip()

        if not mobile or not otp:
            return Response(
                {"detail": "Mobile number and OTP are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = _find_user_by_mobile(mobile)
        invalid = Response(
            {"detail": "Invalid mobile number or OTP."},
            status=status.HTTP_400_BAD_REQUEST,
        )
        if not user:
            return invalid

        profile = getattr(user, "profile", None)
        if not profile:
            return invalid

        if user.is_active is False or profile.is_verified is False:
            return Response(
                {
                    "detail": (
                        "Your account has not been verified. Please verify your email or "
                        "mobile number before signing in."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if profile.otp_expires_at and timezone.now() > profile.otp_expires_at:
            return Response(
                {"detail": "This OTP has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if profile.otp_attempts >= 5:
            return Response(
                {"detail": "Too many OTP attempts. Please request a new code."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if not profile.otp_hash or not check_password(str(otp).strip(), profile.otp_hash):
            profile.otp_attempts += 1
            profile.save(update_fields=["otp_attempts"])
            return invalid

        login(request, user)
        token, _ = Token.objects.get_or_create(user=user)
        profile.otp_code = None
        profile.otp_hash = None
        profile.otp_created_at = None
        profile.otp_expires_at = None
        profile.otp_attempts = 0
        profile.save(
            update_fields=["otp_code", "otp_hash", "otp_created_at", "otp_expires_at", "otp_attempts"]
        )

        return Response(
            {
                "message": "OTP login successful.",
                "token": token.key,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                    "mobile_number": profile.mobile_number,
                },
            },
            status=status.HTTP_200_OK,
        )



class ForgotPasswordView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        email = (request.data.get('email') or '').strip().lower()
        if not email:
            return Response({'detail': 'Email address is required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email__iexact=email).first()
        if user:
            old_tokens = PasswordResetToken.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now())
            for token in old_tokens:
                token.used_at = timezone.now()
                token.save(update_fields=['used_at'])

            token = secrets.token_urlsafe(32)
            PasswordResetToken.objects.create(
                user=user,
                token_hash=PasswordResetToken.hash_token(token),
                expires_at=timezone.now() + timedelta(hours=1),
            )

            reset_url = _password_reset_link(token)
            try:
                notify_event(
                    notification_events.PASSWORD_RESET_REQUESTED,
                    user=user,
                    email=user.email,
                    context={
                        'user_name': user.first_name or user.username,
                        'reset_url': reset_url,
                        'message': 'Password reset requested.',
                    },
                    force=True,
                    reference_type='user',
                    reference_id=str(user.pk),
                )
            except Exception:
                pass

        return Response({
            'message': 'If an account exists with this email address, a password reset link has been sent.'
        }, status=status.HTTP_200_OK)


class VerifyResetTokenView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = (request.data.get('token') or '').strip()
        if not token:
            return Response({'detail': 'Reset token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        token_hash = PasswordResetToken.hash_token(token)
        reset_token = PasswordResetToken.objects.filter(token_hash=token_hash, used_at__isnull=True).first()
        if not reset_token:
            return Response({'detail': 'This password reset link is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        if timezone.now() > reset_token.expires_at:
            return Response({'detail': 'This password reset link is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': 'Reset token is valid.'}, status=status.HTTP_200_OK)


class ResetPasswordView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = (request.data.get('token') or '').strip()
        password = request.data.get('password')
        confirm_password = request.data.get('confirm_password')

        if not token:
            return Response({'detail': 'Reset token is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not password or len(password) < 8:
            return Response({'detail': 'Password must be at least 8 characters long.'}, status=status.HTTP_400_BAD_REQUEST)
        if password != confirm_password:
            return Response({'detail': 'Passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

        token_hash = PasswordResetToken.hash_token(token)
        reset_token = PasswordResetToken.objects.filter(token_hash=token_hash, used_at__isnull=True).first()
        if not reset_token:
            return Response({'detail': 'This password reset link is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        if timezone.now() > reset_token.expires_at:
            reset_token.used_at = timezone.now()
            reset_token.save(update_fields=['used_at'])
            return Response({'detail': 'This password reset link is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        user = reset_token.user
        user.set_password(password)
        user.save(update_fields=['password'])

        reset_token.used_at = timezone.now()
        reset_token.save(update_fields=['used_at'])

        PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        Token.objects.filter(user=user).delete()

        try:
            notify_event(
                notification_events.PASSWORD_CHANGED,
                user=user,
                email=user.email,
                context={'user_name': user.first_name or user.username, 'message': 'Your password was changed.'},
                force=True,
                idempotency_key=f'password_changed:{user.pk}:{int(timezone.now().timestamp())}',
                reference_type='user',
                reference_id=str(user.pk),
            )
        except Exception:
            pass

        return Response({'message': 'Your password has been reset successfully.'}, status=status.HTTP_200_OK)




class LogoutView(APIView):
    """Invalidate the caller's DRF auth token."""

    def post(self, request):
        if request.user.is_authenticated:
            Token.objects.filter(user=request.user).delete()
        return Response({"message": "Signed out successfully."}, status=status.HTTP_200_OK)


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
