from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

import audit.services as audit
from accounts.access import IsSuperAdmin

from . import services
from .models import ListDefinition, ListValue
from .serializers import (
    ListDetailSerializer, ListSummarySerializer, ListValueSerializer, ReorderSerializer,
)


class PublicListsView(APIView):
    """Every signed-in user: the active values the app needs for its dropdowns.

    GET /api/lists/ returns all lists; add ?keys=visit-statuses,allergies for a few.
    """

    @extend_schema(parameters=[OpenApiParameter("keys", str, description="Comma separated list keys")],
                   responses=inline_serializer("PublicLists", {"lists": serializers.DictField()}))
    def get(self, request):
        keys = [k for k in request.query_params.get("keys", "").split(",") if k]
        return Response(services.public_lists(ListValue, request.user.clinic_id, keys or None))


class ListViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Platform admin: browse and edit the master lists. Values are never deleted, only turned off."""

    permission_classes = [IsSuperAdmin]
    queryset = ListDefinition.objects.all()
    lookup_field = "key"
    pagination_class = None
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_serializer_class(self):
        return ListSummarySerializer if self.action == "list" else ListDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list":
            glob = Q(values__clinic__isnull=True)
            qs = qs.annotate(value_count=Count("values", filter=glob), active_count=Count("values", filter=glob & Q(values__is_active=True)))
        return qs

    def perform_update(self, serializer):
        instance = serializer.save()
        audit.log("list.update", self.request, obj=instance, key=instance.key, fields=sorted(serializer.validated_data))

    @extend_schema(request=ListValueSerializer, responses=ListValueSerializer)
    @action(detail=True, methods=["post"], url_path="values")
    def add_value(self, request, key=None):
        """Add a value to this list. The code is permanent."""
        definition = self.get_object()
        data = ListValueSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        code = data.validated_data["code"]
        if ListValue.objects.filter(list=definition, clinic__isnull=True, code=code).exists():
            raise ValidationError({"code": "This code already exists in the list."})
        last = definition.values.filter(clinic__isnull=True).order_by("-sort_order").first()
        value = data.save(list=definition, sort_order=data.validated_data.get("sort_order") or ((last.sort_order if last else 0) + 1))
        audit.log("list.value.create", request, obj=value, list=definition.key, code=value.code)
        return Response(ListValueSerializer(value).data, status=201)

    @extend_schema(request=ReorderSerializer, responses=inline_serializer("Reordered", {"count": serializers.IntegerField()}))
    @action(detail=True, methods=["post"])
    def reorder(self, request, key=None):
        """Send the value ids in the order you want: {"order": [12, 7, 9]}."""
        definition = self.get_object()
        data = ReorderSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ids = data.validated_data["order"]
        values = {v.id: v for v in definition.values.filter(clinic__isnull=True)}
        if set(ids) != set(values):
            raise ValidationError({"order": "Send every value of this list exactly once."})
        with transaction.atomic():
            for position, value_id in enumerate(ids, 1):
                if values[value_id].sort_order != position:
                    ListValue.objects.filter(pk=value_id).update(sort_order=position)
        audit.log("list.reorder", request, obj=definition, list=definition.key)
        return Response({"count": len(ids)})

    @extend_schema(parameters=[OpenApiParameter("type", str, description="json (default) or csv")], responses=None)
    @action(detail=True, methods=["get"])
    def export(self, request, key=None):
        definition = self.get_object()
        if request.query_params.get("type") == "csv":
            response = HttpResponse(services.export_csv(definition), content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{definition.key}.csv"'
            return response
        return Response(services.export_list(definition))

    @extend_schema(
        request=inline_serializer("ImportBody", {"lists": serializers.ListField(child=serializers.DictField(), required=False)}),
        parameters=[OpenApiParameter("dry_run", bool, description="Only count what would change")],
        responses=inline_serializer("ImportResult", {
            "ok": serializers.BooleanField(), "lists_created": serializers.IntegerField(), "lists_updated": serializers.IntegerField(),
            "values_created": serializers.IntegerField(), "values_updated": serializers.IntegerField(),
        }),
    )
    @action(detail=False, methods=["post"], url_path="import")
    def import_lists(self, request):
        """Create or update lists from JSON in the export shape. Nothing is saved if any row is invalid."""
        dry_run = request.query_params.get("dry_run") in ("1", "true", "yes")
        result = services.import_lists(request.data, ListDefinition, ListValue, overwrite=True, dry_run=dry_run)
        if not result["ok"]:
            return Response({"problems": result["problems"]}, status=400)
        if not dry_run:
            audit.log("list.import", request, **{k: v for k, v in result.items() if k != "ok"})
        return Response(result)


class ListValueViewSet(mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Platform admin: change one value (label, translations, order, colour, active). The code cannot change."""

    permission_classes = [IsSuperAdmin]
    queryset = ListValue.objects.filter(clinic__isnull=True).select_related("list")
    serializer_class = ListValueSerializer
    http_method_names = ["patch", "options"]

    def perform_update(self, serializer):
        value = serializer.save()
        audit.log("list.value.update", self.request, obj=value, list=value.list.key, code=value.code, fields=sorted(serializer.validated_data))
