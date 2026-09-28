from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .address_serializers import CustomerAddressSerializer
from .models import CustomerAddress


class AddressListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = CustomerAddress.objects.filter(user=request.user)
        return Response(
            {
                "count": qs.count(),
                "results": CustomerAddressSerializer(qs, many=True, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = CustomerAddressSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        address = serializer.save()
        return Response(
            CustomerAddressSerializer(address, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AddressDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, request, address_id):
        return CustomerAddress.objects.filter(id=address_id, user=request.user).first()

    def get(self, request, address_id):
        address = self.get_object(request, address_id)
        if not address:
            # Hide existence of other users' addresses
            other = CustomerAddress.objects.filter(id=address_id).exists()
            if other:
                return Response({"detail": "You do not have access to this address."}, status=status.HTTP_403_FORBIDDEN)
            return Response({"detail": "Address not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            CustomerAddressSerializer(address, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    def patch(self, request, address_id):
        address = self.get_object(request, address_id)
        if not address:
            other = CustomerAddress.objects.filter(id=address_id).exists()
            if other:
                return Response({"detail": "You do not have access to this address."}, status=status.HTTP_403_FORBIDDEN)
            return Response({"detail": "Address not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = CustomerAddressSerializer(
            address, data=request.data, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        address = serializer.save()
        return Response(
            CustomerAddressSerializer(address, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    def delete(self, request, address_id):
        address = self.get_object(request, address_id)
        if not address:
            other = CustomerAddress.objects.filter(id=address_id).exists()
            if other:
                return Response({"detail": "You do not have access to this address."}, status=status.HTTP_403_FORBIDDEN)
            return Response({"detail": "Address not found."}, status=status.HTTP_404_NOT_FOUND)
        was_default = address.is_default
        user = address.user
        address.delete()
        if was_default:
            nxt = CustomerAddress.objects.filter(user=user).order_by("-updated_at").first()
            if nxt:
                nxt.is_default = True
                nxt.save(update_fields=["is_default", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class AddressSetDefaultView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, address_id):
        address = CustomerAddress.objects.filter(id=address_id, user=request.user).first()
        if not address:
            other = CustomerAddress.objects.filter(id=address_id).exists()
            if other:
                return Response({"detail": "You do not have access to this address."}, status=status.HTTP_403_FORBIDDEN)
            return Response({"detail": "Address not found."}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            CustomerAddress.objects.filter(user=request.user, is_default=True).exclude(pk=address.pk).update(
                is_default=False
            )
            address.is_default = True
            address.save(update_fields=["is_default", "updated_at"])
        return Response(
            CustomerAddressSerializer(address, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )
