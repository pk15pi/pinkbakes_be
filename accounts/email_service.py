from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags


def send_html_email(subject, recipient_list, html_content, text_content=None):
    if not recipient_list:
        return 0

    to_list = recipient_list if isinstance(recipient_list, (list, tuple)) else [recipient_list]
    text_body = text_content or strip_tags(html_content)
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'pinkbakes@pinkbakes.com')

    message = EmailMultiAlternatives(subject, text_body, from_email, to_list)
    message.attach_alternative(html_content, 'text/html')
    return message.send(fail_silently=False)


def build_verification_email_html(user_name, verification_url):
    first_name = (user_name or 'there').strip() or 'there'
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; background:#fff7fb; padding:24px; color:#3a2a34;">
        <div style="max-width:600px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #f2dfe8;">
          <div style="background:linear-gradient(135deg,#ff5ca8,#ff8bb8); color:#fff; padding:24px 32px;">
            <h2 style="margin:0; font-size:28px;">PinkBakes</h2>
          </div>
          <div style="padding:32px;">
            <p style="font-size:16px; margin:0 0 12px;">Hi {first_name},</p>
            <p style="font-size:15px; line-height:1.6; margin:0 0 20px;">
              Welcome to PinkBakes. Please verify your account so you can sign in and order your favorite cakes.
            </p>
            <div style="text-align:center; margin:24px 0;">
              <a href="{verification_url}" style="display:inline-block; background:#ff4da6; color:#fff; padding:14px 24px; border-radius:999px; text-decoration:none; font-weight:bold;">Verify My Account</a>
            </div>
            <p style="font-size:14px; line-height:1.6; color:#5d4753; margin:0 0 12px;">
              If the button does not work, copy and paste this link into your browser:<br>
              <span style="word-break:break-all;">{verification_url}</span>
            </p>
            <p style="font-size:12px; color:#7b6070; margin-top:20px;">
              This verification link expires in 24 hours. If you did not create this account, you can safely ignore this email.
            </p>
          </div>
        </div>
      </body>
    </html>
    """


def build_password_reset_email_html(user_name, reset_url):
    first_name = (user_name or 'there').strip() or 'there'
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; background:#fff7fb; padding:24px; color:#3a2a34;">
        <div style="max-width:600px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #f2dfe8;">
          <div style="background:linear-gradient(135deg,#ff5ca8,#ff8bb8); color:#fff; padding:24px 32px;">
            <h2 style="margin:0; font-size:28px;">PinkBakes</h2>
          </div>
          <div style="padding:32px;">
            <p style="font-size:16px; margin:0 0 12px;">Hi {first_name},</p>
            <p style="font-size:15px; line-height:1.6; margin:0 0 20px;">
              We received a request to reset the password for your PinkBakes account.
            </p>
            <div style="text-align:center; margin:24px 0;">
              <a href="{reset_url}" style="display:inline-block; background:#ff4da6; color:#fff; padding:14px 24px; border-radius:999px; text-decoration:none; font-weight:bold;">Reset My Password</a>
            </div>
            <p style="font-size:14px; line-height:1.6; color:#5d4753; margin:0 0 12px;">
              If the button does not work, copy and paste this link into your browser:<br>
              <span style="word-break:break-all;">{reset_url}</span>
            </p>
            <p style="font-size:12px; color:#7b6070; margin-top:20px;">
              This reset link is valid for 1 hour. If you did not request a password reset, you can safely ignore this email.
            </p>
          </div>
        </div>
      </body>
    </html>
    """
