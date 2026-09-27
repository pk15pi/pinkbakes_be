from django.urls import path

from .views import MeView, SendVerificationView, SigninView, SignupView, VerifyEmailView, VerifyOtpView

urlpatterns = [
    path('signup/', SignupView.as_view(), name='signup'),
    path('signin/', SigninView.as_view(), name='signin'),
    path('send-verification/', SendVerificationView.as_view(), name='send-verification'),
    path('verify-email/', VerifyEmailView.as_view(), name='verify-email'),
    path('verify-otp/', VerifyOtpView.as_view(), name='verify-otp'),
    path('me/', MeView.as_view(), name='me'),
]
