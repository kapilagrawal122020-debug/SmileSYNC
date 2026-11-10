from django.conf import settings
from django.db import models

from accounts.validators import UPLOAD_VALIDATORS
from clinic.models import Clinic
from patients.models import Visit

AFTER_PATIENT, MORNING, EVENING = "after_patient", "morning", "evening"

# The 4 photos needed after every patient, and the 6 areas for the daily rounds.
AFTER_PATIENT_AREAS = {
    "spit_basin": "Spit basin and tray",
    "chair_seat": "Chair seating area",
    "suction_tips": "Suction tips",
    "rotors": "Rotors and instruments",
}
DAILY_AREAS = {
    "waiting": "Waiting area",
    "reception": "Reception",
    "operatories": "Operatories",
    "washroom": "Washroom",
    "pantry": "Pantry",
    "storage": "Instrument and medicine storage",
}


class HygieneCheck(models.Model):
    PENDING, APPROVED, REDO = "pending", "approved", "redo"
    STATUS_CHOICES = [(PENDING, "Pending review"), (APPROVED, "Approved"), (REDO, "Redo requested")]
    KIND_CHOICES = [(AFTER_PATIENT, "After patient"), (MORNING, "Morning round"), (EVENING, "Evening round")]

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="hygiene_checks")
    kind = models.CharField(max_length=15, choices=KIND_CHOICES)
    area = models.CharField(max_length=20, choices=list({**AFTER_PATIENT_AREAS, **DAILY_AREAS}.items()))
    visit = models.ForeignKey(Visit, null=True, blank=True, on_delete=models.CASCADE, related_name="hygiene_checks")
    photo = models.FileField(upload_to="hygiene/", blank=True, validators=UPLOAD_VALIDATORS)  # blank after auto-delete
    photo_hash = models.CharField(max_length=64, db_index=True)  # SHA-256, used to reject re-uploaded photos
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    taken_at = models.DateTimeField(auto_now_add=True)  # server time, the phone's clock is not trusted
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    review_note = models.CharField(max_length=200, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-taken_at"]
