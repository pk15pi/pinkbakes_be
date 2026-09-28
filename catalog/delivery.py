"""
Delivery eligibility and charge computation.

Calc order (checkout / payment):
  1. Merchandise line totals at discounted product prices
  2. Coupon discount (merchandise only — never applied to delivery unless
     an existing coupon rule says otherwise; default is merchandise-only)
  3. Delivery eligibility + charge from postal zone + optional radius/per-km
  4. Final total = merchandise_after_coupon + delivery_fee (+ tax if any)

Charge formula (simple, documented):
  base = zone.delivery_charge if zone else DeliverySettings.default_delivery_charge
  If bakery coords, customer coords, and per_km_charge are set:
      charge = base + per_km_charge * haversine_km(bakery, customer)
  Else:
      charge = base
  If subtotal_after_coupon >= free_delivery_threshold (zone, else global):
      charge = 0

Eligibility:
  - DeliverySettings.delivery_enabled must be True
  - If any active DeliveryZone rows exist, postal_code must match one
  - Else (no zones configured): fall back to global DeliverySettings defaults
  - Zone minimum_order_amount checked against subtotal_after_coupon
  - If bakery lat/lng + max_delivery_radius_km set AND customer has coords:
      reject when haversine distance > max radius
"""

from __future__ import annotations

import math
from decimal import Decimal

from .models import DeliverySettings, DeliveryZone


class DeliveryError(ValueError):
    """Raised when delivery cannot be offered for the given address/cart."""


def _quantize(amount):
    return Decimal(amount).quantize(Decimal("0.01"))


def normalize_postal_code(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres (WGS84 sphere)."""
    r = 6371.0
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def resolve_zone(postal_code):
    """Return the first active DeliveryZone whose postal_codes list contains the PIN."""
    pin = normalize_postal_code(postal_code)
    if not pin:
        return None
    for zone in DeliveryZone.objects.filter(is_active=True).order_by("id"):
        if pin in zone.normalized_postal_codes():
            return zone
    return None


def get_settings():
    return DeliverySettings.get_solo()


def compute_delivery(
    subtotal_after_coupon,
    postal_code,
    *,
    latitude=None,
    longitude=None,
):
    """
    Returns dict:
      eligible, charge, message, eta_min_minutes, eta_max_minutes,
      zone, zone_id, min_order_ok, distance_km, free_delivery_applied
    """
    settings_row = get_settings()
    subtotal = _quantize(subtotal_after_coupon or 0)
    pin = normalize_postal_code(postal_code)

    result = {
        "eligible": False,
        "charge": Decimal("0.00"),
        "message": "",
        "eta_min_minutes": None,
        "eta_max_minutes": None,
        "zone": None,
        "zone_id": None,
        "min_order_ok": True,
        "distance_km": None,
        "free_delivery_applied": False,
    }

    if not settings_row.delivery_enabled:
        result["message"] = "Delivery is currently unavailable."
        return result

    if not pin:
        result["message"] = "Postal code is required for delivery."
        return result

    if len(pin) != 6:
        result["message"] = "Enter a valid 6-digit Indian postal code."
        return result

    zone = resolve_zone(pin)
    if zone is None:
        # PIN listed only on inactive zones -> temporarily unavailable
        for inactive in DeliveryZone.objects.filter(is_active=False).iterator():
            if pin in inactive.normalized_postal_codes():
                result["message"] = f"Delivery to PIN {pin} is temporarily unavailable."
                return result
        if DeliveryZone.objects.filter(is_active=True).exists():
            result["message"] = f"Sorry, we do not deliver to PIN {pin} yet."
            return result
        # No zones configured at all -> fall back to global DeliverySettings.

    # Radius check when bakery + customer coords are available.
    distance_km = None
    cust_lat = latitude
    cust_lng = longitude
    bakery_lat = settings_row.bakery_latitude
    bakery_lng = settings_row.bakery_longitude
    max_radius = settings_row.max_delivery_radius_km

    if (
        cust_lat is not None
        and cust_lng is not None
        and bakery_lat is not None
        and bakery_lng is not None
        and max_radius is not None
    ):
        try:
            distance_km = haversine_km(bakery_lat, bakery_lng, cust_lat, cust_lng)
            result["distance_km"] = round(distance_km, 3)
            if Decimal(str(distance_km)) > Decimal(max_radius):
                result["message"] = (
                    f"This address is outside our delivery radius "
                    f"({_quantize(max_radius)} km). Distance is about {distance_km:.1f} km."
                )
                return result
        except (TypeError, ValueError):
            distance_km = None

    # Minimum order (zone-level when zone matched).
    min_order = Decimal("0.00")
    if zone is not None and zone.minimum_order_amount is not None:
        min_order = _quantize(zone.minimum_order_amount)
    if min_order > 0 and subtotal < min_order:
        result["min_order_ok"] = False
        result["zone"] = zone
        result["zone_id"] = zone.id
        result["message"] = (
            f"Minimum order amount of Rs.{min_order} required for delivery to this area "
            f"(current merchandise total after coupon: Rs.{subtotal})."
        )
        if zone:
            result["eta_min_minutes"] = zone.eta_min_minutes
            result["eta_max_minutes"] = zone.eta_max_minutes
        return result

    # Base charge
    if zone is not None:
        charge = _quantize(zone.delivery_charge)
        result["zone"] = zone
        result["zone_id"] = zone.id
        result["eta_min_minutes"] = zone.eta_min_minutes
        result["eta_max_minutes"] = zone.eta_max_minutes
    else:
        charge = _quantize(settings_row.default_delivery_charge)

    # Optional per-km add-on when distance known and per_km_charge configured.
    per_km = settings_row.per_km_charge
    if per_km is not None and distance_km is not None:
        charge = _quantize(charge + _quantize(per_km) * Decimal(str(round(distance_km, 3))))

    # Free delivery threshold: zone overrides global when set.
    free_threshold = None
    if zone is not None and zone.free_delivery_threshold is not None:
        free_threshold = _quantize(zone.free_delivery_threshold)
    elif settings_row.free_delivery_threshold is not None:
        free_threshold = _quantize(settings_row.free_delivery_threshold)

    free_applied = False
    if free_threshold is not None and free_threshold > 0 and subtotal >= free_threshold:
        charge = Decimal("0.00")
        free_applied = True

    result["eligible"] = True
    result["charge"] = charge
    result["free_delivery_applied"] = free_applied
    if free_applied:
        result["message"] = "Free delivery applied."
    elif zone:
        result["message"] = f"Delivery available via {zone.name}."
    else:
        result["message"] = "Delivery available."
    return result


def require_delivery(subtotal_after_coupon, postal_code, *, latitude=None, longitude=None):
    """Like compute_delivery but raises DeliveryError when not eligible."""
    result = compute_delivery(
        subtotal_after_coupon,
        postal_code,
        latitude=latitude,
        longitude=longitude,
    )
    if not result["eligible"]:
        raise DeliveryError(result["message"] or "Delivery is not available for this address.")
    return result


def resolve_shipping_from_request(user, data):
    """
    Accept address_id OR inline shipping fields.
    Returns snapshot dict used to create/update Order.
    Raises DeliveryError on ownership / missing fields.
    """
    from accounts.models import CustomerAddress

    address_id = data.get("address_id")
    address_obj = None
    if address_id not in (None, "", 0, "0"):
        try:
            address_id = int(address_id)
        except (TypeError, ValueError):
            raise DeliveryError("Invalid address_id.")
        address_obj = CustomerAddress.objects.filter(id=address_id, user=user).first()
        if not address_obj:
            other = CustomerAddress.objects.filter(id=address_id).exists()
            if other:
                raise DeliveryError("You do not have access to this address.")
            raise DeliveryError("Address not found.")
        return {
            "address": address_obj,
            "address_id": address_obj.id,
            "customer_name": address_obj.full_name,
            "customer_mobile": address_obj.mobile_number or str(data.get("customer_mobile") or "").strip(),
            "shipping_address": address_obj.address_line_1,
            "shipping_address_2": address_obj.address_line_2 or "",
            "landmark": address_obj.landmark or "",
            "city": address_obj.city,
            "state": address_obj.state,
            "postal_code": address_obj.postal_code,
            "country": address_obj.country or "India",
            "shipping_latitude": address_obj.latitude,
            "shipping_longitude": address_obj.longitude,
        }

    required = ["shipping_address", "city", "state", "postal_code", "country"]
    missing = [f for f in required if not str(data.get(f, "")).strip()]
    if missing:
        raise DeliveryError(f"Missing required checkout fields: {', '.join(missing)}.")

    lat = data.get("shipping_latitude", data.get("latitude"))
    lng = data.get("shipping_longitude", data.get("longitude"))
    try:
        lat = Decimal(str(lat)) if lat not in (None, "") else None
        lng = Decimal(str(lng)) if lng not in (None, "") else None
    except Exception:
        lat, lng = None, None

    return {
        "address": None,
        "address_id": None,
        "customer_name": str(data.get("customer_name") or "").strip(),
        "customer_mobile": str(data.get("customer_mobile") or "").strip(),
        "shipping_address": str(data["shipping_address"]).strip(),
        "shipping_address_2": str(data.get("shipping_address_2") or "").strip(),
        "landmark": str(data.get("landmark") or "").strip(),
        "city": str(data["city"]).strip(),
        "state": str(data["state"]).strip(),
        "postal_code": normalize_postal_code(data["postal_code"]),
        "country": str(data["country"]).strip() or "India",
        "shipping_latitude": lat,
        "shipping_longitude": lng,
    }

