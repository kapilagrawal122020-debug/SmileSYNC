from django.contrib.auth.password_validation import validate_password
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from accounts.access import PhoneField
from accounts.models import User
from audit.models import AuditEvent
from clinic.models import Clinic


class PlatformClinicSerializer(serializers.ModelSerializer):
    """The clinic directory. Name, city and a point of contact are required, so every clinic can be reached."""

    staff_count = serializers.IntegerField(read_only=True)
    patient_count = serializers.IntegerField(read_only=True)
    doctors = serializers.SerializerMethodField()
    pending_count = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=150)
    city = serializers.CharField(max_length=80)
    poc_name = serializers.CharField(max_length=100)
    poc_phone = PhoneField()

    class Meta:
        model = Clinic
        fields = [
            "id", "name", "address", "city", "state", "pincode", "location_url", "phone", "email",
            "registration_number", "gst_number", "poc_name", "poc_designation", "poc_phone", "poc_email",
            "admin_notes", "is_active", "created_at", "staff_count", "patient_count", "pending_count", "doctors",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_pincode(self, value):
        if value and not (value.isdigit() and len(value) == 6):
            raise serializers.ValidationError("A pincode has 6 digits.")
        return value

    def validate_phone(self, value):
        if value and not (value.isdigit() and 10 <= len(value) <= 12):
            raise serializers.ValidationError("Enter the phone number with digits only, for example 9876543210 or a landline with the area code.")
        return value

    def validate(self, attrs):
        name = attrs.get("name", getattr(self.instance, "name", ""))
        city = attrs.get("city", getattr(self.instance, "city", ""))
        same = Clinic.objects.filter(name__iexact=name.strip(), city__iexact=city.strip())
        if self.instance:
            same = same.exclude(pk=self.instance.pk)
        if same.exists():
            raise serializers.ValidationError({"name": "A clinic with this name already exists in this city."})
        return attrs

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_doctors(self, clinic):
        return [u.name or u.phone for u in clinic.staff.filter(role="doctor", is_active=True)]


class PlatformUserSerializer(serializers.ModelSerializer):
    clinic_name = serializers.CharField(source="clinic.name", read_only=True, default="")
    is_super_admin = serializers.BooleanField(source="is_superuser", read_only=True)
    signup_pending = serializers.SerializerMethodField()  # waiting for approval (not just switched off)

    @extend_schema_field(serializers.BooleanField())
    def get_signup_pending(self, user):
        return user.signup_pending and not user.is_active

    class Meta:
        model = User
        fields = [
            "id", "phone", "name", "role", "clinic", "clinic_name", "designation", "speciality",
            "registration_number", "is_active", "is_super_admin", "last_login", "signup_pending",
        ]
        read_only_fields = ["id", "phone", "role", "clinic", "last_login", "signup_pending"]  # who someone is cannot be changed from here


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
