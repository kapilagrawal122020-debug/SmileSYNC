from datetime import date

from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from accounts.validators import UPLOAD_VALIDATORS


class Clinic(models.Model):
    """One row per clinic. The platform admin keeps this directory; doctors pick their clinic from it when they sign up."""

    name = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=6, blank=True)
    location_url = models.URLField(blank=True)  # Google Maps link
    phone = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)
    registration_number = models.CharField(max_length=80, blank=True)  # clinic registration under the state Act
    gst_number = models.CharField(max_length=20, blank=True)
    # Point of contact at the clinic: who the platform team calls
    poc_name = models.CharField("point of contact", max_length=100, blank=True)
    poc_designation = models.CharField(max_length=80, blank=True)
    poc_phone = models.CharField(max_length=15, blank=True)
    poc_email = models.EmailField(blank=True)
    admin_notes = models.TextField(blank=True)  # internal, never shown to the clinic
    # The public profile on the marketplace website
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    about = models.TextField(blank=True)
    timings = models.CharField(max_length=200, blank=True)  # e.g. "Mon to Sat, 10 am to 7 pm"
    # A turned-off clinic is hidden from sign-up. Its existing staff keep working.
    is_active = models.BooleanField(default=True)
    # One-tap links shown after a visit
    google_review_url = models.URLField(blank=True)
    website_url = models.URLField(blank=True)
    social_url = models.URLField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.make_slug(self.name, self.city, self.pk)
        super().save(*args, **kwargs)

    @staticmethod
    def make_slug(name, city="", pk=None):
        """smile-care-pune, then smile-care-pune-2 if that is taken. Used in the public web address."""
        base = (slugify(f"{name} {city}".strip())[:150] or "clinic")
        slug, n = base, 1
        while Clinic.objects.filter(slug=slug).exclude(pk=pk).exists():
            n += 1
            slug = f"{base}-{n}"
        return slug

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
