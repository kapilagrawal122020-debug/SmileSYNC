from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from .validators import phone_validator


class UserManager(BaseUserManager):
    def create_user(self, phone, password=None, **extra):
        if not phone:
            raise ValueError("Phone is required")
        user = self.model(phone=phone, **extra)
        user.set_password(password)  # password=None makes it unusable (patients use OTP)
        user.save()
        return user

    def create_superuser(self, phone, password, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", User.DOCTOR)
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Staff (doctor, consultant, assistant) and patients. The phone number is the login."""

    DOCTOR, CONSULTANT, ASSISTANT, PATIENT = "doctor", "consultant", "assistant", "patient"
    ROLE_CHOICES = [
        (DOCTOR, "Doctor / owner"),
        (CONSULTANT, "Consultant"),
        (ASSISTANT, "Assistant"),
        (PATIENT, "Patient"),
    ]

    phone = models.CharField(max_length=10, unique=True, validators=[phone_validator])
    name = models.CharField(max_length=100, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ASSISTANT)
    clinic = models.ForeignKey(
        "clinic.Clinic", null=True, blank=True, on_delete=models.PROTECT, related_name="staff"
    )
    # Shown on the clinic profile and in the doctor app (mainly for doctors and consultants)
    designation = models.CharField(max_length=100, blank=True)
    speciality = models.CharField(max_length=100, blank=True)
    registration_number = models.CharField(max_length=50, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # can open /admin

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    def __str__(self):
        return f"{self.name or self.phone} ({self.role})"


class Otp(models.Model):
    """One-time login code for patients. Only a hash of the code is stored."""

    phone = models.CharField(max_length=10, db_index=True)
    code_hash = models.CharField(max_length=128)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        return self.created_at < timezone.now() - timedelta(minutes=10)
