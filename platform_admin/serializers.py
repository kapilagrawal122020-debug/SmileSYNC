from django.contrib.auth.password_validation import validate_password
from django.db import transaction
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
    admins = serializers.SerializerMethodField()
    # Optional: create the clinic's first admin together with the clinic (all three, or none)
    admin_name = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=100)
    admin_phone = PhoneField(write_only=True, required=False, allow_blank=True)
    admin_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    name = serializers.CharField(max_length=150)
    city = serializers.CharField(max_length=80)
    poc_name = serializers.CharField(max_length=100)
    poc_phone = PhoneField()

    class Meta:
        model = Clinic
        fields = [
            "id", "slug", "name", "address", "city", "state", "pincode", "location_url", "phone", "email",
            "registration_number", "gst_number", "poc_name", "poc_designation", "poc_phone", "poc_email",
            "about", "timings", "admin_notes", "is_active", "created_at", "staff_count", "patient_count", "pending_count",
            "doctors", "admins", "admin_name", "admin_phone", "admin_password",
        ]
        read_only_fields = ["id", "slug", "created_at"]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_admins(self, clinic):
        return [{"id": u.id, "name": u.name, "phone": u.phone, "is_active": u.is_active} for u in clinic.staff.filter(role="clinic_admin")]

    def validate_admin_phone(self, value):
        if value and User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("Phone already in use.")
        return value

    def validate_pincode(self, value):
        if value and not (value.isdigit() and len(value) == 6):
            raise serializers.ValidationError("A pincode has 6 digits.")
        return value

    def validate_phone(self, value):
        if value and not (value.isdigit() and 10 <= len(value) <= 12):
            raise serializers.ValidationError("Enter the phone number with digits only, for example 9876543210 or a landline with the area code.")
        return value

    def validate(self, attrs):
        given = [attrs.get(k) for k in ("admin_name", "admin_phone", "admin_password")]
        if any(given):
            if self.instance is not None:
                raise serializers.ValidationError({"admin_name": "Add an admin to an existing clinic with the Add admin button."})
            for key in ("admin_name", "admin_phone", "admin_password"):
                if not attrs.get(key):
                    raise serializers.ValidationError({key: "Give the admin's name, mobile number and password together."})
            validate_password(attrs["admin_password"])
        name = attrs.get("name", getattr(self.instance, "name", ""))
        city = attrs.get("city", getattr(self.instance, "city", ""))
        same = Clinic.objects.filter(name__iexact=name.strip(), city__iexact=city.strip())
        if self.instance:
            same = same.exclude(pk=self.instance.pk)
        if same.exists():
            raise serializers.ValidationError({"name": "A clinic with this name already exists in this city."})
        return attrs

    def create(self, validated_data):
        admin = {k: validated_data.pop(k, "") for k in ("admin_name", "admin_phone", "admin_password")}
        with transaction.atomic():
            clinic = super().create(validated_data)
            if admin["admin_phone"]:
                User.objects.create_user(
                    phone=admin["admin_phone"], password=admin["admin_password"], name=admin["admin_name"],
                    role=User.CLINIC_ADMIN, clinic=clinic,
                )
        return clinic

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_doctors(self, clinic):
        return [u.name or u.phone for u in clinic.staff.filter(role__in=["clinic_admin", "doctor"], is_active=True)]


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
