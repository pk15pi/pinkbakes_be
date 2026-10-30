from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import wishlist as wishlist_service


def _items(request):
    return request.data.get('items') if isinstance(request.data, dict) else None


class WishlistView(APIView):
    """Signed-in customer's saved wishlist. GET re-validates; PUT replaces; DELETE clears."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(wishlist_service.serialize_wishlist(request.user, request))

    def put(self, request):
        try:
            products, skipped = wishlist_service.parse_ids(_items(request))
        except wishlist_service.WishlistInputError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        wishlist_service.replace_wishlist(request.user, products)
        data = wishlist_service.serialize_wishlist(request.user, request)
        data['removed'] = skipped + data['removed']
        return Response(data)

    def delete(self, request):
        wishlist_service.replace_wishlist(request.user, [])
        return Response(wishlist_service.serialize_wishlist(request.user, request))


class WishlistItemsView(APIView):
    """POST {"id": <product id>} adds one cake."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        raw = request.data.get('id') if isinstance(request.data, dict) else None
        try:
            pid = wishlist_service.parse_product_id(raw)
        except wishlist_service.WishlistInputError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        product = wishlist_service.get_visible_product(pid)
        if product is None:
            return Response({'detail': 'This cake is no longer available.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            wishlist_service.add_item(request.user, product)
        except wishlist_service.WishlistInputError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(wishlist_service.serialize_wishlist(request.user, request), status=status.HTTP_201_CREATED)


class WishlistItemDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, product_id):
        wishlist_service.remove_item(request.user, product_id)
        return Response(wishlist_service.serialize_wishlist(request.user, request))


class WishlistMergeView(APIView):
    """Merge a guest (browser) wishlist into the saved one after sign-in."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            products, skipped = wishlist_service.parse_ids(_items(request), lenient=True)
        except wishlist_service.WishlistInputError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        wishlist_service.merge_into_wishlist(request.user, products)
        data = wishlist_service.serialize_wishlist(request.user, request)
        data['removed'] = skipped + data['removed']
        return Response(data)
