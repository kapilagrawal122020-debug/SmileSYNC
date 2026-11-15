from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import ListDefinition, ListValue
from .services import CODE_RE


class ListValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ListValue
        fields = ["id", "code", "label", "labels", "description", "group", "sort_order", "is_active", "is_system", "metadata"]
        read_only_fields = ["id", "is_system"]

    def validate_code(self, value):
        value = value.strip()
        if not CODE_RE.match(value):
            raise serializers.ValidationError("Use letters, digits and . _ + - only, up to 60 characters.")
        if self.instance is not None and value != self.instance.code:
            raise serializers.ValidationError("A code never changes once created. Add a new value instead.")
        return value

    def validate_labels(self, value):
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
            raise serializers.ValidationError("Use an object of language code to text, for example {\"hi\": \"...\"}.")
        return value

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Metadata must be an object.")
        return value

    def validate(self, attrs):
        if self.instance is not None and self.instance.is_system and attrs.get("is_active") is False:
            raise serializers.ValidationError({"is_active": "The app depends on this value, so it cannot be turned off. You can still change its label, order and translations."})
        return attrs


class ListSummarySerializer(serializers.ModelSerializer):
    value_count = serializers.IntegerField(read_only=True)
    active_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ListDefinition
        fields = ["key", "title", "group", "kind", "value_count", "active_count", "updated_at"]


class ListDetailSerializer(serializers.ModelSerializer):
    values = serializers.SerializerMethodField()

    class Meta:
        model = ListDefinition
        fields = ["key", "title", "group", "kind", "used_in", "stored_today", "scope", "about", "admin_note", "updated_at", "values"]
        read_only_fields = ["key", "kind", "used_in", "stored_today", "scope", "updated_at"]  # only title, group, about and admin_note change by hand

    @extend_schema_field(ListValueSerializer(many=True))
    def get_values(self, obj):
        return ListValueSerializer(obj.values.filter(clinic__isnull=True).order_by("sort_order", "id"), many=True).data


class ReorderSerializer(serializers.Serializer):
    order = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


class ImportSerializer(serializers.Serializer):
    """A list object or {"lists": [...]} in the same shape as an export."""
    lists = serializers.ListField(child=serializers.DictField(), required=False)
