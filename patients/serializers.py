from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.access import ClinicScopedSerializer, PhoneField, file_url
from accounts.validators import UPLOAD_VALIDATORS
from clinic.models import Chair

from . import billing
from .models import (
    Advance, AdvanceRefund, Feedback, Invoice, InvoiceItem, Patient, PatientImage, Payment, Treatment, Visit,
)


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

    @extend_schema_field(OpenApiTypes.URI)
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

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj):
        return file_url(self.context["request"], "image-file", obj)


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = ["id", "description", "amount"]


class PaymentLineSerializer(serializers.ModelSerializer):
    """A payment as shown inside an invoice."""

    class Meta:
        model = Payment
        fields = ["id", "amount", "method", "reference", "advance", "paid_at"]


class InvoiceSerializer(ClinicScopedSerializer):
    items = InvoiceItemSerializer(many=True)
    payments = PaymentLineSerializer(many=True, read_only=True)
    advance_applied = serializers.SerializerMethodField()
    # Send true to pay the new invoice from the patient's advance straight away.
    apply_advance = serializers.BooleanField(write_only=True, required=False, default=False)
    total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    paid = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    due = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "patient", "visit", "notes", "items", "total", "advance_applied", "paid", "due",
            "payments", "apply_advance", "created_at",
        ]

    @extend_schema_field(serializers.DecimalField(max_digits=10, decimal_places=2))
    def get_advance_applied(self, obj):
        total = sum((p.amount for p in obj.payments.all() if p.method == Payment.ADVANCE), Decimal("0"))
        return serializers.DecimalField(max_digits=10, decimal_places=2).to_representation(total)

    def create(self, validated_data):
        items = validated_data.pop("items")
        use_advance = validated_data.pop("apply_advance", False)
        invoice = Invoice.objects.create(**validated_data)
        InvoiceItem.objects.bulk_create(InvoiceItem(invoice=invoice, **i) for i in items)
        if use_advance:
            billing.apply_advance(invoice, None, self.context["request"].user)
        return invoice

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        validated_data.pop("apply_advance", None)  # only used when creating; use the apply-advance action later
        instance = super().update(instance, validated_data)
        if items is not None:  # replace the item list
            instance.items.all().delete()
            InvoiceItem.objects.bulk_create(InvoiceItem(invoice=instance, **i) for i in items)
        return instance


class PaymentSerializer(ClinicScopedSerializer):
    class Meta:
        model = Payment
        fields = ["id", "invoice", "amount", "method", "reference", "advance", "paid_at"]
        read_only_fields = ["advance", "paid_at"]

    def validate_method(self, value):
        if value == Payment.ADVANCE:
            raise serializers.ValidationError("Use the apply-advance action to pay from an advance.")
        return value

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


class AdvanceRefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvanceRefund
        fields = ["id", "amount", "note", "refunded_at"]


class AdvanceSerializer(ClinicScopedSerializer):
    received_by_name = serializers.CharField(source="received_by.name", read_only=True, default="")
    used = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    refunded = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    refunds = AdvanceRefundSerializer(many=True, read_only=True)

    class Meta:
        model = Advance
        fields = [
            "id", "patient", "amount", "method", "reference", "note", "received_at", "received_by_name",
            "used", "refunded", "balance", "refunds", "created_at",
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be more than zero.")
        return value

    def validate_received_at(self, value):
        if value > timezone.now() + timedelta(minutes=5):
            raise serializers.ValidationError("The date received cannot be in the future.")
        return value


class RefundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"), required=False)  # default: all that is left
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class BillingSummarySerializer(serializers.Serializer):
    billed = serializers.DecimalField(max_digits=12, decimal_places=2)
    paid = serializers.DecimalField(max_digits=12, decimal_places=2)  # includes money paid from advances
    due = serializers.DecimalField(max_digits=12, decimal_places=2)
    advance_received = serializers.DecimalField(max_digits=12, decimal_places=2)
    advance_used = serializers.DecimalField(max_digits=12, decimal_places=2)
    advance_refunded = serializers.DecimalField(max_digits=12, decimal_places=2)
    advance_balance = serializers.DecimalField(max_digits=12, decimal_places=2)


class ApplyAdvanceSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)  # default: as much as possible
