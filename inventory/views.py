from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.access import OWNER, STAFF, ClinicScopedMixin

from .models import StockItem, StockMovement, UsageRule
from .serializers import (
    CountSerializer, StockInSerializer, StockItemSerializer, StockMovementSerializer, UsageRuleSerializer,
)
from .services import change_stock, stock_alerts


class StockItemViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """Item master. Staff can view and log deliveries; only the doctor edits items."""

    queryset = StockItem.objects.all()
    serializer_class = StockItemSerializer
    sets_clinic = True
    read_roles = STAFF
    write_roles = OWNER
    action_roles = {"stock_in": STAFF}

    @extend_schema(responses=inline_serializer("StockAlerts", {
        "low_stock": StockItemSerializer(many=True),
        "expiring": StockItemSerializer(many=True),
    }))
    @action(detail=False)
    def alerts(self, request):
        """Low stock and items expiring in 30 days."""
        found = stock_alerts(request.user.clinic)
        ctx = self.get_serializer_context()
        return Response({
            "low_stock": StockItemSerializer(found["low_stock"], many=True, context=ctx).data,
            "expiring": StockItemSerializer(found["expiring"], many=True, context=ctx).data,
        })

    @extend_schema(request=StockInSerializer, responses=StockItemSerializer)
    @action(detail=True, methods=["post"], url_path="stock-in")
    def stock_in(self, request, pk=None):
        """Log a delivery: {"packs": 5} adds 5 x pack_size to stock."""
        item = self.get_object()
        data = StockInSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        packs = data.validated_data["packs"]
        if "batch" in data.validated_data:
            item.batch = data.validated_data["batch"]
        if "expiry_date" in data.validated_data:
            item.expiry_date = data.validated_data["expiry_date"]
        item.save(update_fields=["batch", "expiry_date"])
        item = change_stock(item, packs * item.pack_size, StockMovement.STOCK_IN, request.user, note=f"{packs} pack(s)")
        return Response(self.get_serializer(item).data)

    @extend_schema(request=CountSerializer, responses=inline_serializer("CountResult", {
        "item": serializers.CharField(),
        "quantity": serializers.DecimalField(max_digits=10, decimal_places=2),
        "variance": serializers.DecimalField(max_digits=10, decimal_places=2),
    }))
    @action(detail=True, methods=["post"])
    def count(self, request, pk=None):
        """Monthly stock count: set the real quantity and log the variance."""
        item = self.get_object()
        data = CountSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        variance = data.validated_data["counted"] - item.quantity
        item = change_stock(item, variance, StockMovement.COUNT, request.user, note=data.validated_data["note"])
        return Response({"item": item.name, "quantity": item.quantity, "variance": variance})


class UsageRuleViewSet(ClinicScopedMixin, viewsets.ModelViewSet):
    """Treatment -> material -> average quantity, editable per clinic."""

    queryset = UsageRule.objects.select_related("item")
    serializer_class = UsageRuleSerializer
    sets_clinic = True
    read_roles = STAFF
    write_roles = OWNER


class StockMovementViewSet(ClinicScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Stock history. Filter with ?item=3."""

    queryset = StockMovement.objects.select_related("item")
    serializer_class = StockMovementSerializer
    clinic_lookup = "item__clinic"
    read_roles = OWNER
    filter_params = {"item": "item"}
