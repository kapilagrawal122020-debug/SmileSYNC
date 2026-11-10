import hashlib
import hmac
import json

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.access import ALL, OWNER, ClinicScopedMixin
from patients.models import Patient, Visit

from .models import Message
from .services import send_to_patient


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "patient", "to_phone", "kind", "template", "params", "status", "error", "created_at"]


class BroadcastSerializer(serializers.Serializer):
    template = serializers.CharField()  # an approved WhatsApp template, e.g. "festive_offer"
    params = serializers.ListField(child=serializers.CharField(), required=False, default=list)


class MessageViewSet(ClinicScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Message log. Patients see their own; the doctor sees all and can send broadcasts."""

    queryset = Message.objects.all()
    serializer_class = MessageSerializer
    patient_lookup = "patient__user"
    read_roles = ALL
    action_roles = {"broadcast": OWNER}
    filter_params = {"kind": "kind", "patient": "patient"}

    @extend_schema(request=BroadcastSerializer, responses=inline_serializer("BroadcastResult", {
        "patients": serializers.IntegerField(),
        "results": serializers.DictField(child=serializers.IntegerField()),
    }))
    @action(detail=False, methods=["post"])
    def broadcast(self, request):
        """Festive or camp offer to every patient who consented to WhatsApp (opted-out patients are skipped)."""
        data = BroadcastSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        patients = Patient.objects.filter(clinic=request.user.clinic, consent_whatsapp=True)
        results = {}
        for patient in patients:
            message = send_to_patient(patient, Message.OFFER, data.validated_data["template"], data.validated_data["params"])
            results[message.status] = results.get(message.status, 0) + 1
        return Response({"patients": patients.count(), "results": results})


# ---- WhatsApp webhook: patient replies -------------------------------------------------

def handle_reply(phone, text):
    """'YES'/'1' confirms the next booked visit, '2' asks to reschedule, 'STOP' withdraws consent."""
    text = text.strip().lower()
    patients = Patient.objects.filter(phone=phone)
    if text in ("stop", "unsubscribe"):
        patients.update(consent_whatsapp=False, tips_subscribed=False)
    elif text in ("yes", "confirm", "1", "2", "reschedule"):
        new_status = Visit.CONFIRMED if text in ("yes", "confirm", "1") else Visit.RESCHEDULE
        visit = Visit.objects.filter(patient__in=patients, status=Visit.BOOKED).order_by("scheduled_at").first()
        if visit:
            visit.status = new_status
            visit.save(update_fields=["status"])


def signature_ok(request):
    secret = settings.WHATSAPP_APP_SECRET
    if not secret:
        return settings.DEBUG  # unsigned webhooks are only accepted in development
    expected = "sha256=" + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", ""))


@extend_schema(exclude=True)  # called by Meta, not for trying from Swagger
@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def whatsapp_webhook(request):
    if request.method == "GET":  # Meta's one-time verification handshake
        token = settings.WHATSAPP_VERIFY_TOKEN
        if token and request.query_params.get("hub.verify_token") == token:
            return HttpResponse(request.query_params.get("hub.challenge", ""))
        return HttpResponse(status=403)

    if not signature_ok(request):
        return HttpResponse(status=403)
    payload = json.loads(request.body or "{}")
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                text = msg.get("text", {}).get("body") or msg.get("button", {}).get("text") or ""
                handle_reply(msg.get("from", "")[-10:], text)
    return HttpResponse("ok")
