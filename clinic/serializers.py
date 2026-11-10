from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.access import file_url
from accounts.validators import UPLOAD_VALIDATORS

from .models import Chair, Clinic, ClinicDocument


class ClinicSerializer(serializers.ModelSerializer):
    """Public profile: also what patients see in their app."""

    doctors = serializers.SerializerMethodField()

    class Meta:
        model = Clinic
        fields = [
            "id", "name", "address", "city", "phone",
            "google_review_url", "website_url", "social_url", "doctors",
        ]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_doctors(self, clinic):
        staff = clinic.staff.filter(role__in=["doctor", "consultant"], is_active=True)
        return [
            {
                "name": u.name, "role": u.role, "designation": u.designation,
                "speciality": u.speciality, "registration_number": u.registration_number,
            }
            for u in staff
        ]


class ChairSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chair
        fields = ["id", "name", "status"]
        read_only_fields = ["status"]  # changed by visits and hygiene checks, not by hand


class DocumentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=False, validators=UPLOAD_VALIDATORS)
    file_url = serializers.SerializerMethodField()
    days_left = serializers.IntegerField(read_only=True)

    class Meta:
        model = ClinicDocument
        fields = ["id", "doc_type", "title", "number", "expiry_date", "days_left", "file", "file_url"]

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj):
        return file_url(self.context["request"], "document-file", obj)
