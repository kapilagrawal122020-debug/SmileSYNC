from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from accounts.validators import UPLOAD_VALIDATORS, phone_validator
from clinic.models import Chair, Clinic


class Patient(models.Model):
    LANGUAGES = [
        ("en", "English"), ("hi", "Hindi"), ("mr", "Marathi"), ("gu", "Gujarati"),
        ("ta", "Tamil"), ("te", "Telugu"), ("kn", "Kannada"), ("ml", "Malayalam"),
        ("bn", "Bengali"), ("pa", "Punjabi"),
    ]

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="patients")
    # Set when the patient first logs in with their phone number (one login, many clinics).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="patient_records",
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=10, validators=[phone_validator])  # unique per clinic
    locality = models.CharField(max_length=100, blank=True)
    chief_complaint = models.TextField(blank=True)
    medical_history = models.TextField(blank=True)
    dental_history = models.TextField(blank=True)
    selfie = models.FileField(upload_to="selfies/", blank=True, validators=UPLOAD_VALIDATORS)
    language = models.CharField(max_length=2, choices=LANGUAGES, default="en")

    # DPDP Act: explicit consent, and the patient can withdraw it later.
    consent_data = models.BooleanField(default=False)
    consent_whatsapp = models.BooleanField(default=False)
    consent_at = models.DateTimeField(null=True, blank=True)
    tips_subscribed = models.BooleanField(default=False)

    private_notes = models.TextField(blank=True)  # doctor only: discount, behaviour...
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["clinic", "phone"], name="one_patient_per_phone_per_clinic")
        ]

    def __str__(self):
        return f"{self.name} ({self.phone})"


class Visit(models.Model):
    BOOKED, CONFIRMED, RESCHEDULE = "booked", "confirmed", "reschedule"
    IN_PROGRESS, COMPLETED, CANCELLED, NO_SHOW = "in_progress", "completed", "cancelled", "no_show"
    STATUS_CHOICES = [
        (BOOKED, "Booked"), (CONFIRMED, "Confirmed"), (RESCHEDULE, "Reschedule requested"),
        (IN_PROGRESS, "In progress"), (COMPLETED, "Completed"),
        (CANCELLED, "Cancelled"), (NO_SHOW, "No show"),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="visits")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="visits"
    )
    chair = models.ForeignKey(Chair, null=True, blank=True, on_delete=models.SET_NULL, related_name="visits")
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=BOOKED)
    notes = models.TextField(blank=True)
    reminder_sent = models.BooleanField(default=False)
    feedback_requested = models.BooleanField(default=False)

    class Meta:
        ordering = ["scheduled_at"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Chair follows the visit: in use while treating, locked until hygiene photos are done.
        new_status = {self.IN_PROGRESS: Chair.IN_USE, self.COMPLETED: Chair.LOCKED}.get(self.status)
        if self.chair_id and new_status:
            Chair.objects.filter(pk=self.chair_id).update(status=new_status)

    def __str__(self):
        return f"{self.patient.name} on {self.scheduled_at:%d %b %H:%M}"


class Treatment(models.Model):
    """One planned or done procedure. Marking it done deducts stock (see inventory.services)."""

    PLANNED, DONE = "planned", "done"
    STATUS_CHOICES = [(PLANNED, "Planned"), (DONE, "Done")]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="treatments")
    visit = models.ForeignKey(Visit, null=True, blank=True, on_delete=models.SET_NULL, related_name="treatments")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="treatments"
    )
    procedure = models.CharField(max_length=100)  # matched to inventory usage rules, e.g. "Composite Class 1"
    tooth = models.CharField(max_length=100, blank=True)  # one tooth ("26") or a list ("12, 14, 16, 22")
    units = models.PositiveSmallIntegerField(default=1)  # how many fillings / teeth of this procedure
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PLANNED)
    stock_deducted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def mark_done(self):
        self.status = self.DONE
        self.done_at = self.done_at or timezone.now()
        self.save(update_fields=["status", "done_at"])

    def __str__(self):
        return f"{self.procedure} ({self.patient.name})"


class PatientImage(models.Model):
    """X-ray (RVG), intraoral camera photo or scan, tagged with tooth and before/after."""

    XRAY, INTRAORAL, SCAN, OTHER = "xray", "intraoral", "scan", "other"
    KIND_CHOICES = [(XRAY, "X-ray"), (INTRAORAL, "Intraoral photo"), (SCAN, "Scan"), (OTHER, "Other")]
    BEFORE, AFTER = "before", "after"
    STAGE_CHOICES = [(BEFORE, "Before"), (AFTER, "After")]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="images")
    visit = models.ForeignKey(Visit, null=True, blank=True, on_delete=models.SET_NULL, related_name="images")
    file = models.FileField(upload_to="images/", validators=UPLOAD_VALIDATORS)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=INTRAORAL)
    tooth = models.CharField(max_length=20, blank=True)
    stage = models.CharField(max_length=10, choices=STAGE_CHOICES, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    taken_at = models.DateTimeField(default=timezone.now)  # the app may send the capture time (offline sync)

    class Meta:
        ordering = ["-taken_at"]


class Invoice(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="invoices")
    visit = models.ForeignKey(Visit, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def total(self):
        return self.items.aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def paid(self):
        return self.payments.aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def due(self):
        return self.total - self.paid


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)


class Payment(models.Model):
    """A payment received. UPI payments are recorded here with the UTR as the reference."""

    METHOD_CHOICES = [("cash", "Cash"), ("upi", "UPI"), ("card", "Card"), ("other", "Other")]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default="cash")
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-paid_at"]


class Feedback(models.Model):
    """Post-visit rating. Private to the clinic; the doctor can reply."""

    SOURCES = [("app", "App"), ("whatsapp", "WhatsApp")]

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="feedback")
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.SET_NULL, related_name="feedback")
    visit = models.ForeignKey(Visit, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    rating = models.PositiveSmallIntegerField()  # 1 to 5
    comment = models.TextField(blank=True)
    source = models.CharField(max_length=10, choices=SOURCES, default="app")
    reply = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
