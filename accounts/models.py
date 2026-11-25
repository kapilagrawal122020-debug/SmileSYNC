import binascii
import os
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
        extra.setdefault("role", User.SUPER_ADMIN)
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Staff (doctor, consultant, assistant) and patients. The phone number is the login."""

    DOCTOR, CONSULTANT, ASSISTANT, PATIENT = "doctor", "consultant", "assistant", "patient"
    # Two kinds of admin. The super admin runs the platform (every clinic, every list) and never reads clinical data.
    # A clinic admin runs one clinic: its team, profile, billing, stock and documents. Clinics and clinic admins are
    # created by the super admin only.
    SUPER_ADMIN = "super_admin"
    CLINIC_ADMIN = "clinic_admin"
    ROLE_CHOICES = [
        (CLINIC_ADMIN, "Clinic admin"),
        (DOCTOR, "Doctor"),
        (CONSULTANT, "Consultant"),
        (ASSISTANT, "Assistant"),
        (PATIENT, "Patient"),
        (SUPER_ADMIN, "Platform admin"),
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
    # A doctor who signed up for a clinic waits here until the platform admin approves the account.
    signup_pending = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)  # can open /admin

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    @property
    def is_platform_admin(self):
        return self.is_superuser and self.is_active

    @property
    def is_clinic_admin(self):
        return self.role == self.CLINIC_ADMIN and self.is_active

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


class AuthToken(models.Model):
    """A login session. It remembers which clinic the person signed in for, so a patient who visits two
    clinics only sees the clinic they chose, and staff stay inside their own. The platform admin has no clinic."""

    key = models.CharField(max_length=40, primary_key=True)
    user = models.ForeignKey(User, related_name="auth_tokens", on_delete=models.CASCADE)
    clinic = models.ForeignKey("clinic.Clinic", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = binascii.hexlify(os.urandom(20)).decode()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user_id}@{self.clinic_id}"
