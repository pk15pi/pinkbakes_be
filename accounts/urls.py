from django.urls import path

from .address_views import AddressDetailView, AddressListCreateView, AddressSetDefaultView
from .views import (
    ForgotPasswordView,
    LogoutView,
    MeView,
    RequestLoginOtpView,
    ResetPasswordView,
    SendVerificationView,
    SigninView,
    SignupView,
    VerifyEmailView,
    VerifyLoginOtpView,
    VerifyOtpView,
    VerifyResetTokenView,
)

urlpatterns = [
    path('signup/', SignupView.as_view(), name='signup'),
    path('signin/', SigninView.as_view(), name='signin'),
    path('send-verification/', SendVerificationView.as_view(), name='send-verification'),
    path('verify-email/', VerifyEmailView.as_view(), name='verify-email'),
    path('verify-otp/', VerifyOtpView.as_view(), name='verify-otp'),
    path('request-login-otp/', RequestLoginOtpView.as_view(), name='request-login-otp'),
    path('verify-login-otp/', VerifyLoginOtpView.as_view(), name='verify-login-otp'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot-password'),
    path('verify-reset-token/', VerifyResetTokenView.as_view(), name='verify-reset-token'),
    path('reset-password/', ResetPasswordView.as_view(), name='reset-password'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('me/', MeView.as_view(), name='me'),
]

# Mounted at /api/ via project urls (alongside catalog).
address_urlpatterns = [
    path('addresses/', AddressListCreateView.as_view(), name='address-list-create'),
    path('addresses/<int:address_id>/', AddressDetailView.as_view(), name='address-detail'),
    path('addresses/<int:address_id>/set-default/', AddressSetDefaultView.as_view(), name='address-set-default'),
]
