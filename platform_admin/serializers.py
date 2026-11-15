from django.contrib.auth.password_validation import validate_password
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from accounts.access import PhoneField
from accounts.models import User
from audit.models import AuditEvent
from clinic.models import Clinic


class PlatformClinicSerializer(serializers.ModelSerializer):
    staff_count = serializers.IntegerField(read_only=True)
    patient_count = serializers.IntegerField(read_only=True)
    doctors = serializers.SerializerMethodField()

    class Meta:
        model = Clinic
        fields = ["id", "name", "city", "address", "phone", "created_at", "staff_count", "patient_count", "doctors"]
        read_only_fields = ["id", "created_at"]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_doctors(self, clinic):
        return [u.name or u.phone for u in clinic.staff.filter(role="doctor", is_active=True)]


class PlatformUserSerializer(serializers.ModelSerializer):
    clinic_name = serializers.CharField(source="clinic.name", read_only=True, default="")
    is_super_admin = serializers.BooleanField(source="is_superuser", read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "phone", "name", "role", "clinic", "clinic_name", "designation", "speciality",
            "registration_number", "is_active", "is_super_admin", "last_login",
        ]
        read_only_fields = ["id", "phone", "role", "clinic", "last_login"]  # who someone is cannot be changed from here


class SetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        validate_password(value)
        return value


class NewAdminSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    phone = PhoneField(validators=[UniqueValidator(queryset=User.objects.all(), message="Phone already in use.")])
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        validate_password(value)
        return value


class AuditEventSerializer(serializers.ModelSerializer):
    clinic_name = serializers.CharField(source="clinic.name", read_only=True, default="")

    class Meta:
        model = AuditEvent
        fields = ["id", "created_at", "user", "user_label", "role", "action", "object_type", "object_id", "clinic", "clinic_name", "detail", "ip"]
