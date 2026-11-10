from django.db import models

from clinic.models import Clinic
from patients.models import Patient


class Message(models.Model):
    """A WhatsApp message we sent (or skipped). One row per attempt."""

    REMINDER, DOCTOR_ALERT, OFFER, TIP, FEEDBACK, ALERT = (
        "reminder", "doctor_alert", "offer", "tip", "feedback", "alert",
    )
    KIND_CHOICES = [
        (REMINDER, "Appointment reminder"), (DOCTOR_ALERT, "Doctor alert"), (OFFER, "Offer or camp"),
        (TIP, "Daily tip"), (FEEDBACK, "Feedback request"), (ALERT, "Clinic alert"),
    ]
    SENT, DRY_RUN, FAILED, SKIPPED = "sent", "dry_run", "failed", "skipped"

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="messages")
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    to_phone = models.CharField(max_length=10)
    kind = models.CharField(max_length=15, choices=KIND_CHOICES)
    template = models.CharField(max_length=80)  # WhatsApp template name approved in Meta
    params = models.JSONField(default=list)
    status = models.CharField(max_length=10, default=SENT)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
