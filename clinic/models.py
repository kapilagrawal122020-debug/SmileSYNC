from datetime import date

from django.db import models

from accounts.validators import UPLOAD_VALIDATORS


class Clinic(models.Model):
    name = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    # One-tap links shown after a visit
    google_review_url = models.URLField(blank=True)
    website_url = models.URLField(blank=True)
    social_url = models.URLField(blank=True)

    def __str__(self):
        return self.name


class Chair(models.Model):
    """A dental chair. It stays LOCKED after a visit until the 4 hygiene photos are in."""

    READY, IN_USE, LOCKED = "ready", "in_use", "locked"
    STATUS_CHOICES = [(READY, "Ready"), (IN_USE, "In use"), (LOCKED, "Locked")]

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="chairs")
    name = models.CharField(max_length=50)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=READY)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["clinic", "name"], name="unique_chair_name")]

    def __str__(self):
        return f"{self.name} ({self.status})"


class ClinicDocument(models.Model):
    """Registrations and authorisations with expiry dates (60 / 30 day alerts)."""

    TYPE_CHOICES = [
        ("dental_council", "State Dental Council registration"),
        ("clinic_registration", "Clinic registration"),
        ("biomedical_waste", "Bio-medical waste authorisation"),
        ("aerb", "AERB e-LORA"),
        ("camp_permission", "Camp permission"),
        ("other", "Other"),
    ]

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    title = models.CharField(max_length=150, blank=True)
    number = models.CharField(max_length=80, blank=True)
    file = models.FileField(upload_to="documents/", blank=True, validators=UPLOAD_VALIDATORS)
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["expiry_date"]

    @property
    def days_left(self):
        return (self.expiry_date - date.today()).days if self.expiry_date else None

    def __str__(self):
        return f"{self.get_doc_type_display()} ({self.clinic})"
