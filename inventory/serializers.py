from decimal import Decimal

from rest_framework import serializers

from accounts.access import ClinicScopedSerializer

from .models import StockItem, StockMovement, UsageRule


class StockItemSerializer(ClinicScopedSerializer):
    is_low = serializers.BooleanField(read_only=True)

    class Meta:
        model = StockItem
        fields = [
            "id", "name", "unit", "pack_size", "shade_or_size", "batch", "expiry_date",
            "reorder_level", "quantity", "is_low",
        ]
        read_only_fields = ["quantity"]  # changes only via stock-in, usage or monthly count


class UsageRuleSerializer(ClinicScopedSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = UsageRule
        fields = ["id", "procedure", "item", "item_name", "quantity"]


class StockMovementSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = StockMovement
        fields = ["id", "item", "item_name", "change", "reason", "note", "created_at"]


class StockInSerializer(serializers.Serializer):
    packs = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))  # e.g. 5 boxes
    batch = serializers.CharField(required=False, allow_blank=True)
    expiry_date = serializers.DateField(required=False)


class CountSerializer(serializers.Serializer):
    counted = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"))
    note = serializers.CharField(required=False, allow_blank=True, default="")


class UseStockSerializer(ClinicScopedSerializer):
    """Body of POST /treatments/{id}/use-stock/. Not a model serializer, but reuses the clinic check."""

    item = serializers.PrimaryKeyRelatedField(queryset=StockItem.objects.all())
    quantity = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))

    class Meta:
        model = StockMovement
        fields = ["item", "quantity"]
