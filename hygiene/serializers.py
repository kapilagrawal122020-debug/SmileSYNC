import hashlib

from rest_framework import serializers

from accounts.access import ClinicScopedSerializer, file_url
from accounts.validators import UPLOAD_VALIDATORS

from .models import AFTER_PATIENT, AFTER_PATIENT_AREAS, DAILY_AREAS, HygieneCheck


class HygieneCheckSerializer(ClinicScopedSerializer):
    photo = serializers.FileField(write_only=True, validators=UPLOAD_VALIDATORS)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = HygieneCheck
        fields = [
            "id", "kind", "area", "visit", "photo", "photo_url", "taken_by", "taken_at",
            "status", "review_note", "reviewed_at",
        ]
        read_only_fields = ["taken_by", "taken_at", "status", "review_note", "reviewed_at"]

    def get_photo_url(self, obj):
        return file_url(self.context["request"], "hygiene-photo", obj, "photo")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        user = self.context["request"].user

        # After-patient checks belong to a visit (that is how the chair gets unlocked).
        if attrs["kind"] == AFTER_PATIENT:
            if attrs.get("visit") is None:
                raise serializers.ValidationError({"visit": "Required for an after-patient check."})
            allowed = AFTER_PATIENT_AREAS
        else:
            allowed = DAILY_AREAS
        if attrs["area"] not in allowed:
            raise serializers.ValidationError({"area": f"Choose one of: {', '.join(allowed)}."})

        # Tamper check: the same photo file cannot be submitted twice.
        digest = hashlib.sha256()
        for chunk in attrs["photo"].chunks():
            digest.update(chunk)
        attrs["photo"].seek(0)
        attrs["photo_hash"] = digest.hexdigest()
        if HygieneCheck.objects.filter(clinic=user.clinic, photo_hash=attrs["photo_hash"]).exists():
            raise serializers.ValidationError({"photo": "This photo was already submitted. Take a new one."})
        return attrs
