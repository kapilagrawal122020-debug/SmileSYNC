from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .access import ClinicScopedSerializer, PhoneField
from clinic.models import Clinic

from .models import User


class UserSerializer(ClinicScopedSerializer):
    phone = PhoneField(validators=[UniqueValidator(queryset=User.objects.all(), message="Phone already in use.")])
    password = serializers.CharField(write_only=True, min_length=6, required=False)
    is_super_admin = serializers.BooleanField(source="is_superuser", read_only=True)  # the app shows the Admin menu from this

    class Meta:
        model = User
        fields = [
            "id", "phone", "name", "role", "designation", "speciality",
            "registration_number", "is_active", "password", "is_super_admin",
        ]

    def validate_role(self, value):
        if value == User.PATIENT:
            raise serializers.ValidationError("Patients register through the patient endpoint.")
        if value == User.SUPER_ADMIN:
            raise serializers.ValidationError("Choose doctor, consultant or assistant.")
        return value

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


SIGNUP_ROLES = [User.DOCTOR]  # sign-up creates doctors only


class RegisterSerializer(serializers.Serializer):
    """Self sign-up: a doctor picks their clinic from the directory the platform admin keeps.
    The account stays switched off until it is approved, so nobody can reach a clinic's patients just by choosing it."""

    name = serializers.CharField(max_length=100)
    phone = PhoneField(validators=[UniqueValidator(queryset=User.objects.all(), message="Phone already in use.")])
    password = serializers.CharField(write_only=True)
    clinic = serializers.PrimaryKeyRelatedField(
        queryset=Clinic.objects.filter(is_active=True),
        error_messages={"does_not_exist": "Choose your clinic from the list.", "incorrect_type": "Choose your clinic from the list.", "required": "Choose your clinic from the list."},
    )
    designation = serializers.CharField(max_length=100, required=False, allow_blank=True)
    speciality = serializers.CharField(max_length=100, required=False, allow_blank=True)
    registration_number = serializers.CharField(max_length=50, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=SIGNUP_ROLES, required=False)  # only doctors can sign up

    def validate_password(self, value):
        validate_password(value)  # Django's rules: 8+ characters, not a common password
        return value

    def create(self, validated_data):
        validated_data.pop("role", None)
        return User.objects.create_user(role=User.DOCTOR, is_active=False, signup_pending=True, **validated_data)


class PublicClinicSerializer(serializers.ModelSerializer):
    """What the sign-up screen may show without a login: the name and the place, nothing about the people."""

    class Meta:
        model = Clinic
        fields = ["id", "name", "city", "state"]


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


class PhoneSerializer(serializers.Serializer):
    phone = PhoneField()


class OtpVerifySerializer(serializers.Serializer):
    phone = PhoneField()
    code = serializers.CharField(max_length=6)
