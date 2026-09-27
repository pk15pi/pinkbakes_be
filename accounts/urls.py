from django.urls import path

from .views import (
    ForgotPasswordView,
    MeView,
    ResetPasswordView,
    SendVerificationView,
    SigninView,
    SignupView,
    VerifyEmailView,
    VerifyOtpView,
    VerifyResetTokenView,
)

urlpatterns = [
    path('signup/', SignupView.as_view(), name='signup'),
    path('signin/', SigninView.as_view(), name='signin'),
    path('send-verification/', SendVerificationView.as_view(), name='send-verification'),
    path('verify-email/', VerifyEmailView.as_view(), name='verify-email'),
    path('verify-otp/', VerifyOtpView.as_view(), name='verify-otp'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot-password'),
    path('verify-reset-token/', VerifyResetTokenView.as_view(), name='verify-reset-token'),
    path('reset-password/', ResetPasswordView.as_view(), name='reset-password'),
    path('me/', MeView.as_view(), name='me'),
]
