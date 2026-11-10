"""Shared access-control helpers used by every app.

Three ideas, all small:
1. RolePermission     - each view lists which roles may read / write.
2. ClinicScopedMixin  - staff only see rows of their own clinic, patients only their own rows.
3. file helpers       - uploaded files are downloaded through the API, never from a public URL.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Model
from django.http import FileResponse, Http404
from rest_framework import serializers
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.reverse import reverse

from .validators import normalize_phone, phone_validator

# Role groups. "doctor" is the clinic owner.
OWNER = {"doctor"}
CLINICAL = {"doctor", "consultant"}
STAFF = {"doctor", "consultant", "assistant"}
ALL = STAFF | {"patient"}


class RolePermission(BasePermission):
    """A view declares `read_roles` and `write_roles`.

    `action_roles = {"stock_in": {...}}` overrides them for one specific action.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        roles = getattr(view, "action_roles", {}).get(getattr(view, "action", None))
        if roles is None:
            roles = view.read_roles if request.method in SAFE_METHODS else view.write_roles
        return user.role in roles


def clinic_of(obj):
    """Which clinic does this object belong to? (Patient-linked rows go via the patient.)"""
    if hasattr(obj, "clinic_id"):
        return obj.clinic_id
    if hasattr(obj, "patient"):
        return obj.patient.clinic_id
    return None


class ClinicScopedMixin:
    """Put this FIRST in the viewset's bases.

    clinic_lookup  - ORM path from the model to its clinic (e.g. "clinic", "patient__clinic")
    patient_lookup - ORM path to the patient's login user; None means patients get nothing
    filter_params  - {query param: ORM lookup} for simple ?patient=3&status=done filtering
    sets_clinic    - True when the model has a direct `clinic` field to fill on create
    """

    permission_classes = [RolePermission]
    read_roles = STAFF
    write_roles = STAFF
    action_roles = {}
    clinic_lookup = "clinic"
    patient_lookup = None
    filter_params = {}
    sets_clinic = False

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.role == "patient":
            qs = qs.filter(**{self.patient_lookup: user}) if self.patient_lookup else qs.none()
        else:
            qs = qs.filter(**{self.clinic_lookup: user.clinic_id})
        for param, lookup in self.filter_params.items():
            value = self.request.query_params.get(param)
            if value:
                try:
                    qs = qs.filter(**{lookup: value})
                except (ValueError, DjangoValidationError):
                    raise serializers.ValidationError({param: "Invalid value."})
        return qs

    def perform_create(self, serializer):
        extra = {"clinic": self.request.user.clinic} if self.sets_clinic else {}
        serializer.save(**extra)


class ClinicScopedSerializer(serializers.ModelSerializer):
    """Rejects related objects (patient, visit, item, ...) that belong to another clinic."""

    def validate(self, attrs):
        user = self.context["request"].user
        if user.role != "patient":
            for name, value in attrs.items():
                if isinstance(value, Model) and clinic_of(value) not in (None, user.clinic_id):
                    raise serializers.ValidationError({name: "Object does not exist."})
        return attrs


class PhoneField(serializers.CharField):
    """Accepts '+91 98765 43210' and stores '9876543210'."""

    def __init__(self, **kwargs):
        kwargs["validators"] = [phone_validator, *kwargs.get("validators", [])]
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        return normalize_phone(super().to_internal_value(data))


def file_response(fieldfile):
    if not fieldfile:
        raise Http404
    return FileResponse(fieldfile.open("rb"))


def file_url(request, view_name, obj, field="file"):
    """Link to the protected download endpoint, or None if the file is missing."""
    if not getattr(obj, field):
        return None
    return reverse(view_name, args=[obj.pk], request=request)
