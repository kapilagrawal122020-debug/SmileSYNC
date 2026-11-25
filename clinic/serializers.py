from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.access import file_url
from accounts.validators import UPLOAD_VALIDATORS

from .models import Chair, Clinic, ClinicDocument


def listed_clinicians(staff):
    """Who a clinic shows as its doctors: doctors and consultants, plus a clinic admin who gave a designation
    (the owner of a one-doctor clinic is the admin and the doctor)."""
    return [
        u for u in staff
        if u.is_active and (u.role in ("doctor", "consultant") or (u.role == "clinic_admin" and u.designation))
    ]


class ClinicSerializer(serializers.ModelSerializer):
    """The clinic's own profile, edited by its clinic admin. Also what a patient sees in their app."""

    doctors = serializers.SerializerMethodField()

    class Meta:
        model = Clinic
        fields = [
            "id", "slug", "name", "address", "city", "state", "pincode", "location_url", "phone", "email",
            "google_review_url", "website_url", "social_url", "about", "timings", "doctors",
        ]
        # The name and web address belong to the platform directory. The point of contact and admin notes are not exposed here at all.
        read_only_fields = ["name", "slug", "state", "pincode"]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_doctors(self, clinic):
        return [
            {
                "name": u.name, "role": "doctor" if u.role == "clinic_admin" else u.role, "designation": u.designation,
                "speciality": u.speciality, "registration_number": u.registration_number,
            }
            for u in listed_clinicians(clinic.staff.all())
        ]


class PublicClinicSerializer(serializers.ModelSerializer):
    """The marketplace profile of a clinic. Open to everyone, so it only holds what a clinic publishes about itself:
    never the point of contact, internal notes or doctors' registration numbers."""

    doctors = serializers.SerializerMethodField()

    class Meta:
        model = Clinic
        fields = [
            "id", "slug", "name", "city", "state", "pincode", "address", "location_url", "phone", "email",
            "website_url", "social_url", "google_review_url", "about", "timings", "doctors",
        ]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_doctors(self, clinic):
        return [
            {"name": u.name, "designation": u.designation, "speciality": u.speciality}
            for u in listed_clinicians(clinic.staff.all())
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
