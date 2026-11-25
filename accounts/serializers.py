from django.contrib.auth.password_validation import validate_password
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .access import ClinicScopedSerializer, PhoneField
from clinic.models import Clinic

from .models import User


class UserSerializer(ClinicScopedSerializer):
    phone = PhoneField(validators=[UniqueValidator(queryset=User.objects.all(), message="Phone already in use.")])
    password = serializers.CharField(write_only=True, min_length=6, required=False)
    is_super_admin = serializers.BooleanField(source="is_superuser", read_only=True)  # the app shows the Admin menu from this
    signup_pending = serializers.SerializerMethodField()  # waiting for approval, not just switched off
    clinic_id = serializers.SerializerMethodField()  # the clinic this session is for
    clinic_name = serializers.SerializerMethodField()
    clinic_slug = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "phone", "name", "role", "designation", "speciality",
            "registration_number", "is_active", "password", "is_super_admin",
            "signup_pending", "clinic_id", "clinic_name", "clinic_slug",
        ]

    def validate_role(self, value):
        if value == User.PATIENT:
            raise serializers.ValidationError("Patients register through the patient endpoint.")
        if value == User.SUPER_ADMIN:
            raise serializers.ValidationError("Choose clinic admin, doctor, consultant or assistant.")
        return value

    def _clinic(self, user):
        """The clinic of this session (the token's), else the person's own clinic. Cached on the user for the request."""
        if not hasattr(user, "_clinic_obj"):
            cid = getattr(user, "session_clinic_id", None) or user.clinic_id
            user._clinic_obj = user.clinic if cid and cid == user.clinic_id else Clinic.objects.filter(pk=cid).first() if cid else None
        return user._clinic_obj

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_clinic_id(self, user):
        c = self._clinic(user)
        return c.id if c else None

    @extend_schema_field(serializers.CharField())
    def get_clinic_name(self, user):
        c = self._clinic(user)
        return c.name if c else ""

    @extend_schema_field(serializers.CharField())
    def get_clinic_slug(self, user):
        c = self._clinic(user)
        return c.slug if c else ""

    @extend_schema_field(serializers.BooleanField())
    def get_signup_pending(self, user):
        return user.signup_pending and not user.is_active

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "This field is required."})
        return super().validate(attrs)

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)  # hashes the password

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save(update_fields=["password"])
        return instance


SIGNUP_ROLES = [User.DOCTOR, User.CONSULTANT, User.ASSISTANT, User.PATIENT]  # clinic admins are created by the platform admin only
STAFF_SIGNUP_ROLES = [User.DOCTOR, User.CONSULTANT, User.ASSISTANT]


class RegisterSerializer(serializers.Serializer):
    """Self sign-up for ONE clinic from the directory the platform admin keeps.

    Staff (doctor, consultant, assistant) are created switched off and wait for the clinic admin's approval, so nobody
    can reach a clinic's patients just by choosing it. A patient is added to that clinic's patient list and signs in
    with a WhatsApp code, so no password and no approval are needed."""

    role = serializers.ChoiceField(choices=SIGNUP_ROLES)
    name = serializers.CharField(max_length=100)
    phone = PhoneField()
    clinic = serializers.PrimaryKeyRelatedField(
        queryset=Clinic.objects.filter(is_active=True),
        error_messages={"does_not_exist": "Choose your clinic from the list.", "incorrect_type": "Choose your clinic from the list.", "required": "Choose your clinic from the list."},
    )
    password = serializers.CharField(write_only=True, required=False)
    designation = serializers.CharField(max_length=100, required=False, allow_blank=True)
    speciality = serializers.CharField(max_length=100, required=False, allow_blank=True)
    registration_number = serializers.CharField(max_length=50, required=False, allow_blank=True)
    consent_data = serializers.BooleanField(required=False, default=False)
    consent_whatsapp = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        existing = User.objects.filter(phone=attrs["phone"]).first()
        if attrs["role"] == User.PATIENT:
            if not attrs["consent_data"]:
                raise serializers.ValidationError({"consent_data": "Please accept the data consent to continue."})
            if existing and existing.role != User.PATIENT:
                raise serializers.ValidationError({"phone": "This number belongs to a staff account."})
        else:
            if existing:
                raise serializers.ValidationError({"phone": "Phone already in use."})
            if not attrs.get("password"):
                raise serializers.ValidationError({"password": "This field is required."})
            validate_password(attrs["password"])  # Django's rules: 8+ characters, not a common password
        return attrs

    def create(self, validated_data):
        from django.utils import timezone

        from patients.models import Patient

        role, clinic = validated_data["role"], validated_data["clinic"]
        if role == User.PATIENT:
            patient, _ = Patient.objects.get_or_create(
                clinic=clinic, phone=validated_data["phone"],
                defaults={
                    "name": validated_data["name"], "consent_data": True, "consent_at": timezone.now(),
                    "consent_whatsapp": validated_data["consent_whatsapp"],
                },
            )
            return patient
        for field in ("consent_data", "consent_whatsapp"):
            validated_data.pop(field)
        return User.objects.create_user(is_active=False, signup_pending=True, **validated_data)


class PendingSignupSerializer(serializers.Serializer):
    detail = serializers.CharField()
    pending = serializers.BooleanField()


class TokenResponseSerializer(serializers.Serializer):
    token = serializers.CharField()
    user = UserSerializer()


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()


class LoginSerializer(serializers.Serializer):
    phone = PhoneField()
    password = serializers.CharField()
    clinic = serializers.IntegerField(required=False, help_text="The clinic you are signing in to. Not needed for the platform admin.")


class OtpRequestSerializer(serializers.Serializer):
    phone = PhoneField()
    clinic = serializers.IntegerField(help_text="The clinic you are a patient of.")


class OtpVerifySerializer(serializers.Serializer):
    phone = PhoneField()
    code = serializers.CharField(max_length=6)
    clinic = serializers.IntegerField(help_text="The clinic you are a patient of.")
