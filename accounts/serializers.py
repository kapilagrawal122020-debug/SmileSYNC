from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .access import ClinicScopedSerializer, PhoneField
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


class LoginSerializer(serializers.Serializer):
    phone = PhoneField()
    password = serializers.CharField()


class PhoneSerializer(serializers.Serializer):
    phone = PhoneField()


class OtpVerifySerializer(serializers.Serializer):
    phone = PhoneField()
    code = serializers.CharField(max_length=6)
