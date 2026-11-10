from django.utils import timezone
from rest_framework import serializers

from accounts.access import ClinicScopedSerializer, PhoneField, file_url
from accounts.validators import UPLOAD_VALIDATORS
from clinic.models import Chair

from .models import Feedback, Invoice, InvoiceItem, Patient, PatientImage, Payment, Treatment, Visit


class PatientSerializer(ClinicScopedSerializer):
    phone = PhoneField()
    selfie = serializers.FileField(write_only=True, required=False, validators=UPLOAD_VALIDATORS)
    selfie_url = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = [
            "id", "name", "phone", "locality", "chief_complaint", "medical_history", "dental_history",
            "language", "consent_data", "consent_whatsapp", "tips_subscribed", "private_notes",
            "selfie", "selfie_url", "created_at",
        ]

    def get_selfie_url(self, obj):
        return file_url(self.context["request"], "patient-selfie", obj, "selfie")

    def validate_phone(self, value):
        user = self.context["request"].user
        if self.instance and value != self.instance.phone:
            raise serializers.ValidationError("Phone number cannot be changed.")
        if self.instance is None:
            existing = Patient.objects.filter(clinic=user.clinic, phone=value).first()
            if existing:  # duplicate check by phone number
                raise serializers.ValidationError(f"Already registered as patient #{existing.id} ({existing.name}).")
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        if self.instance is None and not attrs.get("consent_data"):
            raise serializers.ValidationError({"consent_data": "Patient consent is required to register."})
        if user.role != "doctor":
            attrs.pop("private_notes", None)  # only the doctor writes private notes
        return super().validate(attrs)

    def create(self, validated_data):
        validated_data["consent_at"] = timezone.now()
        return super().create(validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if self.context["request"].user.role != "doctor":
            data.pop("private_notes", None)
        return data


class VisitSerializer(ClinicScopedSerializer):
    patient_name = serializers.CharField(source="patient.name", read_only=True)

    class Meta:
        model = Visit
        fields = [
            "id", "patient", "patient_name", "doctor", "chair", "scheduled_at", "status", "notes",
        ]

    def validate(self, attrs):
        chair = attrs.get("chair") or (self.instance.chair if self.instance else None)
        starting = attrs.get("status") == Visit.IN_PROGRESS and (not self.instance or self.instance.status != Visit.IN_PROGRESS)
        if starting and chair and chair.status == Chair.LOCKED:
            raise serializers.ValidationError({"chair": "Chair is locked until the 4 hygiene photos are submitted."})
        return super().validate(attrs)


class TreatmentSerializer(ClinicScopedSerializer):
    class Meta:
        model = Treatment
        fields = [
            "id", "patient", "visit", "doctor", "procedure", "tooth", "units", "cost", "notes",
            "status", "done_at", "stock_deducted", "created_at",
        ]
        # status changes only through POST /treatments/{id}/complete/ so stock stays correct
        read_only_fields = ["doctor", "status", "done_at", "stock_deducted"]


class PatientImageSerializer(ClinicScopedSerializer):
    file = serializers.FileField(write_only=True, validators=UPLOAD_VALIDATORS)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = PatientImage
        fields = ["id", "patient", "visit", "kind", "tooth", "stage", "taken_at", "file", "file_url"]

    def get_file_url(self, obj):
        return file_url(self.context["request"], "image-file", obj)


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = ["id", "description", "amount"]


class InvoiceSerializer(ClinicScopedSerializer):
    items = InvoiceItemSerializer(many=True)
    total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    paid = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    due = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = ["id", "patient", "visit", "notes", "items", "total", "paid", "due", "created_at"]

    def create(self, validated_data):
        items = validated_data.pop("items")
        invoice = Invoice.objects.create(**validated_data)
        InvoiceItem.objects.bulk_create(InvoiceItem(invoice=invoice, **i) for i in items)
        return invoice

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        instance = super().update(instance, validated_data)
        if items is not None:  # replace the item list
            instance.items.all().delete()
            InvoiceItem.objects.bulk_create(InvoiceItem(invoice=instance, **i) for i in items)
        return instance


class PaymentSerializer(ClinicScopedSerializer):
    class Meta:
        model = Payment
        fields = ["id", "invoice", "amount", "method", "reference", "paid_at"]
        read_only_fields = ["paid_at"]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be more than zero.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["amount"] > attrs["invoice"].due:
            raise serializers.ValidationError({"amount": "More than the amount due on this invoice."})
        return attrs


class FeedbackSerializer(ClinicScopedSerializer):
    class Meta:
        model = Feedback
        fields = ["id", "patient", "visit", "rating", "comment", "source", "reply", "created_at"]
        read_only_fields = ["reply"]

    def validate_rating(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError("Rating must be from 1 to 5.")
        return value
