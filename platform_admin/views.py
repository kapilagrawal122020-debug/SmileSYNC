"""The platform admin dashboard API. Every view here uses IsSuperAdmin, so a doctor, assistant, consultant or
patient gets 403 and an anonymous caller gets 401. Nothing here returns a patient record: the platform admin
manages the platform (lists, clinics, accounts), not clinical data."""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

import audit.services as audit
from accounts.access import IsSuperAdmin
from accounts.models import User
from audit.models import AuditEvent
from clinic.models import Clinic
from masterdata.models import ListDefinition, ListValue
from patients.models import Patient, Visit

from .serializers import (
    AuditEventSerializer, NewAdminSerializer, PlatformClinicSerializer, PlatformUserSerializer, SetPasswordSerializer,
)


class OverviewView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(responses=inline_serializer("PlatformOverview", {
        "clinics": serializers.IntegerField(), "patients": serializers.IntegerField(), "visits_30_days": serializers.IntegerField(),
        "staff_by_role": serializers.DictField(), "super_admins": serializers.IntegerField(),
        "lists": serializers.DictField(), "audit_events_7_days": serializers.IntegerField(),
        "recent_clinics": serializers.ListField(child=serializers.DictField()),
    }))
    def get(self, request):
        now = timezone.now()
        roles = dict(User.objects.exclude(role__in=["patient", "super_admin"]).values_list("role").annotate(n=Count("id")))
        recent = Clinic.objects.annotate(staff_count=Count("staff", distinct=True), patient_count=Count("patients", distinct=True)).order_by("-created_at")[:5]
        return Response({
            "clinics": Clinic.objects.count(),
            "patients": Patient.objects.count(),
            "visits_30_days": Visit.objects.filter(scheduled_at__gte=now - timedelta(days=30)).count(),
            "staff_by_role": roles,
            "super_admins": User.objects.filter(is_superuser=True, is_active=True).count(),
            "lists": {"count": ListDefinition.objects.count(), "values": ListValue.objects.filter(clinic__isnull=True).count()},
            "audit_events_7_days": AuditEvent.objects.filter(created_at__gte=now - timedelta(days=7)).count(),
            "recent_clinics": [{"id": c.id, "name": c.name, "city": c.city, "created_at": c.created_at, "staff_count": c.staff_count, "patient_count": c.patient_count} for c in recent],
        })


class ClinicViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """All clinics with their sizes. The admin can correct a clinic's name, city, address and phone."""

    permission_classes = [IsSuperAdmin]
    serializer_class = PlatformClinicSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        qs = Clinic.objects.annotate(staff_count=Count("staff", distinct=True), patient_count=Count("patients", distinct=True)).order_by("-created_at", "-id")
        q = self.request.query_params.get("search")
        return qs.filter(Q(name__icontains=q) | Q(city__icontains=q)) if q else qs

    def perform_update(self, serializer):
        clinic = serializer.save()
        audit.log("clinic.update", self.request, obj=clinic, clinic=clinic, fields=sorted(serializer.validated_data))


class UserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Staff and admin accounts across clinics (patients are not listed). Turn accounts on or off, reset passwords."""

    permission_classes = [IsSuperAdmin]
    serializer_class = PlatformUserSerializer
    http_method_names = ["get", "patch", "post", "head", "options"]

    @extend_schema(parameters=[
        OpenApiParameter("role", str), OpenApiParameter("clinic", int), OpenApiParameter("search", str), OpenApiParameter("active", bool),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        qs = User.objects.exclude(role="patient").select_related("clinic").order_by("clinic__name", "role", "name")
        p = self.request.query_params
        if p.get("role"):
            qs = qs.filter(role=p["role"])
        if p.get("clinic"):
            qs = qs.filter(clinic_id=p["clinic"])
        if p.get("active") in ("true", "1"):
            qs = qs.filter(is_active=True)
        if p.get("active") in ("false", "0"):
            qs = qs.filter(is_active=False)
        if p.get("search"):
            qs = qs.filter(Q(name__icontains=p["search"]) | Q(phone__icontains=p["search"]))
        return qs

    def perform_update(self, serializer):
        user = serializer.instance
        if serializer.validated_data.get("is_active") is False:
            if user.pk == self.request.user.pk:
                raise ValidationError({"is_active": "You cannot turn off your own account."})
            if user.is_superuser and User.objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk).count() == 0:
                raise ValidationError({"is_active": "At least one platform admin must stay active."})
        user = serializer.save()
        if not user.is_active:
            Token.objects.filter(user=user).delete()  # signs them out everywhere
        audit.log("user.update", self.request, obj=user, clinic=user.clinic, fields=sorted(serializer.validated_data), active=user.is_active)

    @extend_schema(request=SetPasswordSerializer, responses=inline_serializer("PasswordSet", {"detail": serializers.CharField()}))
    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        """Set a new password and sign the person out everywhere."""
        user = self.get_object()
        data = SetPasswordSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user.set_password(data.validated_data["password"])
        user.save(update_fields=["password"])
        Token.objects.filter(user=user).delete()
        audit.log("user.set-password", request, obj=user, clinic=user.clinic)
        return Response({"detail": "Password changed. The person has been signed out."})


class AdminAccountView(APIView):
    """Create another platform admin. Only an existing platform admin can do this."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(request=NewAdminSerializer, responses=PlatformUserSerializer)
    def post(self, request):
        data = NewAdminSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = User.objects.create_superuser(**data.validated_data)
        audit.log("admin.create", request, obj=user)
        return Response(PlatformUserSerializer(user).data, status=201)


class AuditViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Read-only trail of important actions. Filter with ?action=list&q=maria&user=3."""

    permission_classes = [IsSuperAdmin]
    serializer_class = AuditEventSerializer

    @extend_schema(parameters=[OpenApiParameter("action", str, description="Starts with"), OpenApiParameter("q", str), OpenApiParameter("user", int), OpenApiParameter("clinic", int)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        qs = AuditEvent.objects.select_related("clinic")
        p = self.request.query_params
        if p.get("action"):
            qs = qs.filter(action__startswith=p["action"])
        if p.get("user"):
            qs = qs.filter(user_id=p["user"])
        if p.get("clinic"):
            qs = qs.filter(clinic_id=p["clinic"])
        if p.get("q"):
            qs = qs.filter(Q(user_label__icontains=p["q"]) | Q(action__icontains=p["q"]) | Q(object_id__icontains=p["q"]))
        return qs
