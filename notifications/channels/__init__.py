from .email import EmailChannelAdapter
from .in_app import InAppChannelAdapter
from .sms import SmsChannelAdapter
from .whatsapp import WhatsAppChannelAdapter

ADAPTERS = {
    'email': EmailChannelAdapter(),
    'sms': SmsChannelAdapter(),
    'whatsapp': WhatsAppChannelAdapter(),
    'in_app': InAppChannelAdapter(),
}


def get_adapter(channel: str):
    return ADAPTERS.get(channel)
