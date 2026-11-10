from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .access import ClinicScopedSerializer, PhoneField
from clinic.models import Clinic

from .models import User


class UserSerializer(ClinicScopedSerializer):
    phone = PhoneField(validators=[UniqueValidator(queryset=User.objects.all(), message="Phone already in use.")])
    password = serializers.CharField(write_only=True, min_length=6, required=False)

    class Meta:
        model = User
        fields = [
            "id", "phone", "name", "role", "designation", "speciality",
            "registration_number", "is_active", "password",
        ]

    def validate_role(self, value):
        if value == User.PATIENT:
            raise serializers.ValidationError("Patients register through the patient endpoint.")
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


class RegisterSerializer(serializers.Serializer):
    """Self sign-up: a doctor creates their clinic and their own login in one step."""

    name = serializers.CharField(max_length=100)
    phone = PhoneField(validators=[UniqueValidator(queryset=User.objects.all(), message="Phone already in use.")])
    password = serializers.CharField(write_only=True)
    clinic_name = serializers.CharField(max_length=150)
    city = serializers.CharField(max_length=80, required=False, allow_blank=True)
    designation = serializers.CharField(max_length=100, required=False, allow_blank=True)
    speciality = serializers.CharField(max_length=100, required=False, allow_blank=True)
    registration_number = serializers.CharField(max_length=50, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=[User.DOCTOR], required=False)  # only doctors can sign up

    def validate_password(self, value):
        validate_password(value)  # Django's rules: 8+ characters, not a common password
        return value

    def create(self, validated_data):
        validated_data.pop("role", None)
        clinic = Clinic.objects.create(
            name=validated_data.pop("clinic_name"), city=validated_data.pop("city", "")
        )
        return User.objects.create_user(role=User.DOCTOR, clinic=clinic, **validated_data)


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
