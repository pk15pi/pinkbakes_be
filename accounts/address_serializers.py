from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework import serializers

from .models import CustomerAddress


def normalize_mobile(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def normalize_postal(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


class CustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerAddress
        fields = [
            "id",
            "full_name",
            "mobile_number",
            "address_line_1",
            "address_line_2",
            "landmark",
            "city",
            "state",
            "postal_code",
            "country",
            "latitude",
            "longitude",
            "address_type",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_postal_code(self, value):
        pin = normalize_postal(value)
        if len(pin) != 6:
            raise serializers.ValidationError("Enter a valid 6-digit Indian postal code.")
        return pin

    def validate_mobile_number(self, value):
        if value is None or str(value).strip() == "":
            return ""
        digits = normalize_mobile(value)
        if len(digits) != 10:
            raise serializers.ValidationError("Mobile number must be 10 digits.")
        return digits

    def validate_address_type(self, value):
        allowed = {c[0] for c in CustomerAddress.ADDRESS_TYPE_CHOICES}
        value = (value or "HOME").upper()
        if value not in allowed:
            raise serializers.ValidationError("address_type must be HOME, WORK, or OTHER.")
        return value

    def validate_latitude(self, value):
        if value is None or value == "":
            return None
        try:
            dec = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError("Invalid latitude.")
        if dec < Decimal("-90") or dec > Decimal("90"):
            raise serializers.ValidationError("Latitude out of range.")
        return dec

    def validate_longitude(self, value):
        if value is None or value == "":
            return None
        try:
            dec = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError("Invalid longitude.")
        if dec < Decimal("-180") or dec > Decimal("180"):
            raise serializers.ValidationError("Longitude out of range.")
        return dec

    def create(self, validated_data):
        user = self.context["request"].user
        is_default = bool(validated_data.get("is_default", False))
        with transaction.atomic():
            if is_default:
                CustomerAddress.objects.filter(user=user, is_default=True).update(is_default=False)
            elif not CustomerAddress.objects.filter(user=user).exists():
                validated_data["is_default"] = True
            return CustomerAddress.objects.create(user=user, **validated_data)

    def update(self, instance, validated_data):
        is_default = validated_data.get("is_default", instance.is_default)
        with transaction.atomic():
            if is_default and not instance.is_default:
                CustomerAddress.objects.filter(user=instance.user, is_default=True).exclude(
                    pk=instance.pk
                ).update(is_default=False)
            for key, value in validated_data.items():
                setattr(instance, key, value)
            instance.save()
            return instance
