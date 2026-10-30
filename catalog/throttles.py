"""Throttles for the public catalog (BUG-023).

Anonymous shoppers load categories, products and reviews on every page view, so those
safe (read-only) requests get their own, higher "catalog_read" budget instead of using
up the general "anon" budget. Writes on the same views keep the normal "anon" limit, and
the auth/OTP/admin-login/payment scopes are untouched.
"""
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class CatalogReadAnonThrottle(AnonRateThrottle):
    """Anonymous GET/HEAD/OPTIONS on catalog endpoints: THROTTLE_CATALOG_READ."""

    scope = 'catalog_read'

    def allow_request(self, request, view):
        if request.method not in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


class CatalogWriteAnonThrottle(AnonRateThrottle):
    """Anonymous non-read requests on catalog endpoints keep the general THROTTLE_ANON rate."""

    scope = 'anon'

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


CATALOG_READ_THROTTLES = [CatalogReadAnonThrottle, CatalogWriteAnonThrottle, UserRateThrottle]
