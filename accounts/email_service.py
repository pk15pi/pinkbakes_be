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


def build_order_confirmation_email_html(order):
    customer_name = (getattr(order, 'customer_name', None) or 'there').strip() or 'there'
    order_number = getattr(order, 'order_number', '')
    total_amount = getattr(order, 'total_amount', 0)
    items_mgr = getattr(order, 'items', None)
    items = list(items_mgr.all()) if items_mgr is not None and hasattr(items_mgr, 'all') else []
    item_rows = ''
    for item in items:
        item_rows += (
            f"<tr>"
            f"<td style='padding:8px 0; border-bottom:1px solid #f2dfe8;'>{item.product_name} x {item.quantity}</td>"
            f"<td style='padding:8px 0; border-bottom:1px solid #f2dfe8; text-align:right;'>Rs.{item.subtotal}</td>"
            f"</tr>"
        )
    if not item_rows:
        item_rows = "<tr><td colspan='2' style='padding:8px 0;'>Your order items are being prepared.</td></tr>"

    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; background:#fff7fb; padding:24px; color:#3a2a34;">
        <div style="max-width:600px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #f2dfe8;">
          <div style="background:linear-gradient(135deg,#ff5ca8,#ff8bb8); color:#fff; padding:24px 32px;">
            <h2 style="margin:0; font-size:28px;">PinkBakes</h2>
          </div>
          <div style="padding:32px;">
            <p style="font-size:16px; margin:0 0 12px;">Hi {customer_name},</p>
            <p style="font-size:15px; line-height:1.6; margin:0 0 20px;">
              Thank you for your order! Your payment was successful and your order is confirmed.
            </p>
            <p style="font-size:15px; margin:0 0 8px;"><strong>Order number:</strong> {order_number}</p>
            <p style="font-size:15px; margin:0 0 16px;"><strong>Total paid:</strong> Rs.{total_amount}</p>
            <table style="width:100%; border-collapse:collapse; margin:16px 0 8px; font-size:14px;">
              {item_rows}
            </table>
            <p style="font-size:14px; line-height:1.6; color:#5d4753; margin:20px 0 0;">
              We will notify you as your cake moves through preparation and delivery.
            </p>
            <p style="font-size:12px; color:#7b6070; margin-top:20px;">
              Questions? Reply to this email or write to pinkbakes@pinkbakes.com.
            </p>
          </div>
        </div>
      </body>
    </html>
    """


def send_order_confirmation_email(order):
    """Send a one-shot order confirmation email after successful payment."""
    recipient = (getattr(order, 'customer_email', None) or '').strip()
    if not recipient:
        user = getattr(order, 'user', None)
        recipient = (getattr(user, 'email', None) or '').strip() if user else ''
    if not recipient:
        return 0

    subject = f"Order Confirmed — {getattr(order, 'order_number', 'PinkBakes')}"
    html_content = build_order_confirmation_email_html(order)
    return send_html_email(subject, [recipient], html_content)


def _order_recipient(order):
    recipient = (getattr(order, 'customer_email', None) or '').strip()
    if not recipient:
        user = getattr(order, 'user', None)
        recipient = (getattr(user, 'email', None) or '').strip() if user else ''
    return recipient


def build_order_cancellation_email_html(order, reason=''):
    customer_name = (getattr(order, 'customer_name', None) or 'there').strip() or 'there'
    order_number = getattr(order, 'order_number', '')
    reason_line = f"<p style=\"font-size:14px; margin:0 0 12px;\"><strong>Reason:</strong> {reason}</p>" if reason else ''
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; background:#fff7fb; padding:24px; color:#3a2a34;">
        <div style="max-width:600px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #f2dfe8;">
          <div style="background:linear-gradient(135deg,#ff5ca8,#ff8bb8); color:#fff; padding:24px 32px;">
            <h2 style="margin:0; font-size:28px;">PinkBakes</h2>
          </div>
          <div style="padding:32px;">
            <p style="font-size:16px; margin:0 0 12px;">Hi {customer_name},</p>
            <p style="font-size:15px; line-height:1.6; margin:0 0 20px;">
              Your order <strong>{order_number}</strong> has been cancelled.
            </p>
            {reason_line}
            <p style="font-size:14px; line-height:1.6; color:#5d4753; margin:20px 0 0;">
              If a payment was made, any eligible refund will be processed separately and you will receive another email with the refund status.
            </p>
          </div>
        </div>
      </body>
    </html>
    """


def send_order_cancellation_email(order, reason=''):
    recipient = _order_recipient(order)
    if not recipient:
        return 0
    subject = f"Order Cancelled — {getattr(order, 'order_number', 'PinkBakes')}"
    html_content = build_order_cancellation_email_html(order, reason=reason or getattr(order, 'cancellation_reason', '') or '')
    return send_html_email(subject, [recipient], html_content)


def build_refund_email_html(order, refund, stage='initiated'):
    customer_name = (getattr(order, 'customer_name', None) or 'there').strip() or 'there'
    order_number = getattr(order, 'order_number', '')
    amount = getattr(refund, 'amount', 0)
    refund_status = getattr(refund, 'status', '')
    if stage == 'completed':
        body = f"Your refund of <strong>Rs.{amount}</strong> for order <strong>{order_number}</strong> has been completed."
    elif stage == 'failed':
        body = f"We could not complete the refund of <strong>Rs.{amount}</strong> for order <strong>{order_number}</strong>. Our team will review this shortly."
    else:
        body = f"A refund of <strong>Rs.{amount}</strong> for order <strong>{order_number}</strong> has been initiated. Current status: <strong>{refund_status}</strong>."
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; background:#fff7fb; padding:24px; color:#3a2a34;">
        <div style="max-width:600px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #f2dfe8;">
          <div style="background:linear-gradient(135deg,#ff5ca8,#ff8bb8); color:#fff; padding:24px 32px;">
            <h2 style="margin:0; font-size:28px;">PinkBakes</h2>
          </div>
          <div style="padding:32px;">
            <p style="font-size:16px; margin:0 0 12px;">Hi {customer_name},</p>
            <p style="font-size:15px; line-height:1.6; margin:0 0 20px;">{body}</p>
            <p style="font-size:12px; color:#7b6070; margin-top:20px;">
              Refunds typically appear in your original payment method within a few business days, depending on your bank.
            </p>
          </div>
        </div>
      </body>
    </html>
    """


def send_refund_initiated_email(order, refund):
    recipient = _order_recipient(order)
    if not recipient:
        return 0
    subject = f"Refund Initiated — {getattr(order, 'order_number', 'PinkBakes')}"
    return send_html_email(subject, [recipient], build_refund_email_html(order, refund, stage='initiated'))


def send_refund_completed_email(order, refund):
    recipient = _order_recipient(order)
    if not recipient:
        return 0
    subject = f"Refund Completed — {getattr(order, 'order_number', 'PinkBakes')}"
    return send_html_email(subject, [recipient], build_refund_email_html(order, refund, stage='completed'))


def send_refund_failed_email(order, refund):
    recipient = _order_recipient(order)
    if not recipient:
        return 0
    subject = f"Refund Update — {getattr(order, 'order_number', 'PinkBakes')}"
    return send_html_email(subject, [recipient], build_refund_email_html(order, refund, stage='failed'))
