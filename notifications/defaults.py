"""Default channel-per-event config and fallback email templates."""

from . import events as E

# (event, channel) -> enabled by default
DEFAULT_CHANNEL_MAP = {
    # Auth / critical — email
    (E.EMAIL_VERIFICATION_REQUIRED, E.CHANNEL_EMAIL): True,
    (E.MOBILE_VERIFICATION_REQUIRED, E.CHANNEL_EMAIL): True,
    (E.PASSWORD_RESET_REQUESTED, E.CHANNEL_EMAIL): True,
    (E.PASSWORD_CHANGED, E.CHANNEL_EMAIL): True,
    (E.LOGIN_OTP_REQUESTED, E.CHANNEL_SMS): True,
    (E.LOGIN_OTP_REQUESTED, E.CHANNEL_EMAIL): True,
    (E.USER_REGISTERED, E.CHANNEL_IN_APP): True,
    # Orders / payments
    (E.ORDER_CREATED, E.CHANNEL_IN_APP): True,
    (E.ORDER_CONFIRMED, E.CHANNEL_EMAIL): True,
    (E.ORDER_CONFIRMED, E.CHANNEL_IN_APP): True,
    (E.ORDER_CANCELLED, E.CHANNEL_EMAIL): True,
    (E.ORDER_CANCELLED, E.CHANNEL_IN_APP): True,
    (E.ORDER_STATUS_CHANGED, E.CHANNEL_IN_APP): True,
    (E.PAYMENT_INITIATED, E.CHANNEL_IN_APP): True,
    (E.PAYMENT_SUCCESS, E.CHANNEL_IN_APP): True,
    (E.PAYMENT_FAILED, E.CHANNEL_EMAIL): True,
    (E.PAYMENT_FAILED, E.CHANNEL_IN_APP): True,
    (E.PAYMENT_CANCELLED, E.CHANNEL_IN_APP): True,
    (E.REFUND_REQUESTED, E.CHANNEL_EMAIL): True,
    (E.REFUND_REQUESTED, E.CHANNEL_IN_APP): True,
    (E.REFUND_PROCESSING, E.CHANNEL_IN_APP): True,
    (E.REFUND_COMPLETED, E.CHANNEL_EMAIL): True,
    (E.REFUND_COMPLETED, E.CHANNEL_IN_APP): True,
    (E.REFUND_FAILED, E.CHANNEL_EMAIL): True,
    (E.REFUND_FAILED, E.CHANNEL_IN_APP): True,
    # Delivery
    (E.DELIVERY_ASSIGNED, E.CHANNEL_EMAIL): True,
    (E.DELIVERY_ASSIGNED, E.CHANNEL_IN_APP): True,
    (E.ORDER_READY_FOR_DELIVERY, E.CHANNEL_IN_APP): True,
    (E.ORDER_OUT_FOR_DELIVERY, E.CHANNEL_EMAIL): True,
    (E.ORDER_OUT_FOR_DELIVERY, E.CHANNEL_IN_APP): True,
    (E.DELIVERY_COMPLETED, E.CHANNEL_EMAIL): True,
    (E.DELIVERY_COMPLETED, E.CHANNEL_IN_APP): True,
    # Reviews
    (E.REVIEW_SUBMITTED, E.CHANNEL_IN_APP): True,
    (E.REVIEW_APPROVED, E.CHANNEL_EMAIL): True,
    (E.REVIEW_APPROVED, E.CHANNEL_IN_APP): True,
    (E.REVIEW_REJECTED, E.CHANNEL_EMAIL): True,
    (E.REVIEW_REJECTED, E.CHANNEL_IN_APP): True,
    # Admin operational
    (E.NEW_ORDER, E.CHANNEL_IN_APP): True,
    (E.LOW_STOCK, E.CHANNEL_IN_APP): True,
    (E.LOW_STOCK, E.CHANNEL_EMAIL): True,
    (E.ADMIN_PAYMENT_FAILED, E.CHANNEL_IN_APP): True,
    (E.ADMIN_REFUND_FAILED, E.CHANNEL_IN_APP): True,
}

# Simple {{var}} email fallbacks when no DB template row / dedicated helper.
DEFAULT_EMAIL_TEMPLATES = {
    E.PASSWORD_CHANGED: {
        'subject': 'Your PinkBakes password was changed',
        'body_html': (
            '<p>Hi {{user_name}},</p>'
            '<p>Your PinkBakes password was changed successfully. '
            'If you did not do this, reset your password immediately or contact support.</p>'
            '<p>pinkbakes@pinkbakes.com</p>'
        ),
        'body_text': 'Hi {{user_name}}, your PinkBakes password was changed successfully.',
    },
    E.PAYMENT_FAILED: {
        'subject': 'Payment failed — {{order_number}}',
        'body_html': (
            '<p>Hi {{user_name}},</p>'
            '<p>We could not complete payment for order <strong>{{order_number}}</strong>. '
            'You can retry payment from your orders page.</p>'
        ),
        'body_text': 'Payment failed for order {{order_number}}. Please retry from your orders page.',
    },
    E.DELIVERY_ASSIGNED: {
        'subject': 'Delivery assigned — {{order_number}}',
        'body_html': (
            '<p>Hi {{user_name}},</p>'
            '<p>A delivery partner has been assigned for order <strong>{{order_number}}</strong>.</p>'
            '<p>{{tracking_message}}</p>'
            '<p><a href="{{tracking_url}}">Track your order</a></p>'
        ),
        'body_text': 'Delivery assigned for {{order_number}}. Track: {{tracking_url}}',
    },
    E.ORDER_OUT_FOR_DELIVERY: {
        'subject': 'Out for delivery — {{order_number}}',
        'body_html': (
            '<p>Hi {{user_name}},</p>'
            '<p>Your order <strong>{{order_number}}</strong> is out for delivery.</p>'
            '<p><a href="{{tracking_url}}">Track your order</a></p>'
        ),
        'body_text': 'Order {{order_number}} is out for delivery. Track: {{tracking_url}}',
    },
    E.DELIVERY_COMPLETED: {
        'subject': 'Delivered — {{order_number}}',
        'body_html': (
            '<p>Hi {{user_name}},</p>'
            '<p>Your order <strong>{{order_number}}</strong> has been delivered. Enjoy!</p>'
        ),
        'body_text': 'Order {{order_number}} has been delivered. Enjoy!',
    },
    E.REVIEW_APPROVED: {
        'subject': 'Your review was approved',
        'body_html': '<p>Hi {{user_name}},</p><p>Your review for <strong>{{product_name}}</strong> was approved. Thank you!</p>',
        'body_text': 'Your review for {{product_name}} was approved. Thank you!',
    },
    E.REVIEW_REJECTED: {
        'subject': 'Update on your review',
        'body_html': '<p>Hi {{user_name}},</p><p>Your review for <strong>{{product_name}}</strong> was not approved.{{admin_comment_line}}</p>',
        'body_text': 'Your review for {{product_name}} was not approved.',
    },
    E.LOW_STOCK: {
        'subject': 'Low stock alert — {{product_name}}',
        'body_html': (
            '<p>Product <strong>{{product_name}}</strong> (SKU {{product_id}}) is low on stock. '
            'Available: {{available_quantity}} (threshold {{threshold}}).</p>'
        ),
        'body_text': 'Low stock: {{product_name}} available={{available_quantity}} threshold={{threshold}}',
    },
}


def seed_channel_configs():
    from .models import NotificationChannelConfig
    for (event, channel), enabled in DEFAULT_CHANNEL_MAP.items():
        NotificationChannelConfig.objects.update_or_create(
            event=event,
            channel=channel,
            defaults={'is_enabled': enabled},
        )


def seed_email_templates():
    from .models import NotificationTemplate
    for event, tpl in DEFAULT_EMAIL_TEMPLATES.items():
        NotificationTemplate.objects.update_or_create(
            event=event,
            channel='email',
            defaults={
                'subject': tpl['subject'],
                'body_html': tpl['body_html'],
                'body_text': tpl.get('body_text', ''),
                'is_active': True,
            },
        )
