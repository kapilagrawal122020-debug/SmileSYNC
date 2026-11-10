"""Scheduled jobs. Run them from cron / Task Scheduler / a platform scheduler:

    python manage.py run_job reminders          # daily, evening: tomorrow's appointments
    python manage.py run_job digest             # daily, early morning: today's list to each doctor
    python manage.py run_job feedback-requests  # daily, evening: rating request after completed visits
    python manage.py run_job documents          # daily: 60 and 30 day expiry alerts
    python manage.py run_job stock              # daily: low stock and expiry alerts
    python manage.py run_job hygiene-morning    # e.g. 11:00: alert if the morning round is missing
    python manage.py run_job hygiene-evening    # e.g. 21:00: alert if the evening round is missing
    python manage.py run_job tips               # daily: tip to subscribed patients
    python manage.py run_job purge-photos       # daily: delete old hygiene photos
"""
from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from clinic.models import Clinic, ClinicDocument
from hygiene.models import EVENING, MORNING, HygieneCheck
from inventory.services import stock_alerts
from patients.models import Patient, Visit

from .models import Message
from .services import alert_doctors, send_to_patient, send_to_staff

TIPS = [
    "Brush twice a day for two minutes with a fluoride toothpaste.",
    "Replace your toothbrush every three months.",
    "Floss once a day, before brushing.",
    "Rinse your mouth with water after sweets and cold drinks.",
    "Do not skip your six-monthly dental check-up.",
    "Clean your tongue gently every morning.",
    "Avoid sticky sweets between meals.",
]


def reminders():
    """Tell tomorrow's patients about their appointment. They reply 1 to confirm, 2 to reschedule."""
    tomorrow = date.today() + timedelta(days=1)
    visits = Visit.objects.filter(
        scheduled_at__date=tomorrow, status=Visit.BOOKED, reminder_sent=False
    ).select_related("patient__clinic")
    for visit in visits:
        local = timezone.localtime(visit.scheduled_at)
        send_to_patient(
            visit.patient, Message.REMINDER, "appointment_reminder",
            [visit.patient.name, visit.patient.clinic.name, f"{local:%d %b}", f"{local:%I:%M %p}"],
        )
        visit.reminder_sent = True
        visit.save(update_fields=["reminder_sent"])


def digest():
    """Each doctor gets today's patients with their chief complaint and medical history."""
    for clinic in Clinic.objects.all():
        visits = Visit.objects.filter(
            patient__clinic=clinic, scheduled_at__date=date.today(),
            status__in=[Visit.BOOKED, Visit.CONFIRMED],
        ).select_related("patient")
        if not visits:
            continue
        lines = [
            f"{timezone.localtime(v.scheduled_at):%I:%M %p} {v.patient.name}: "
            f"{v.patient.chief_complaint or '-'} / {v.patient.medical_history or 'no medical history'}"
            for v in visits
        ]
        alert_doctors(clinic, "doctor_daily_digest", [" | ".join(lines)], Message.DOCTOR_ALERT)


def feedback_requests():
    """Ask for a rating after a completed visit, with the one-tap Google review link."""
    visits = Visit.objects.filter(status=Visit.COMPLETED, feedback_requested=False).select_related("patient__clinic")
    for visit in visits:
        clinic = visit.patient.clinic
        send_to_patient(
            visit.patient, Message.FEEDBACK, "feedback_request",
            [visit.patient.name, clinic.name, clinic.google_review_url or clinic.website_url or "-"],
        )
        visit.feedback_requested = True
        visit.save(update_fields=["feedback_requested"])


def documents():
    """Alert doctors when a registration or authorisation is exactly 60 or 30 days from expiry."""
    for days in (60, 30):
        for doc in ClinicDocument.objects.filter(expiry_date=date.today() + timedelta(days=days)).select_related("clinic"):
            alert_doctors(doc.clinic, "document_expiry_alert", [doc.get_doc_type_display(), days, f"{doc.expiry_date:%d %b %Y}"])


def stock():
    for clinic in Clinic.objects.all():
        found = stock_alerts(clinic)
        if found["low_stock"] or found["expiring"]:
            alert_doctors(
                clinic, "stock_alert",
                [", ".join(i.name for i in found["low_stock"]) or "-", ", ".join(i.name for i in found["expiring"]) or "-"],
            )


def missed_hygiene(kind):
    """Alert doctors if no photo for this round was submitted today."""
    for clinic in Clinic.objects.all():
        if not HygieneCheck.objects.filter(clinic=clinic, kind=kind, taken_at__date=date.today()).exists():
            alert_doctors(clinic, "hygiene_missed_alert", [kind])


def tips():
    tip = TIPS[date.today().timetuple().tm_yday % len(TIPS)]
    for patient in Patient.objects.filter(tips_subscribed=True, consent_whatsapp=True).select_related("clinic"):
        send_to_patient(patient, Message.TIP, "daily_oral_tip", [tip])


def purge_photos():
    """Hygiene photos are deleted after HYGIENE_PHOTO_RETENTION_DAYS. The check record stays."""
    cutoff = timezone.now() - timedelta(days=settings.HYGIENE_PHOTO_RETENTION_DAYS)
    for check in HygieneCheck.objects.filter(taken_at__lt=cutoff).exclude(photo=""):
        check.photo.delete(save=False)
        check.photo = ""
        check.save(update_fields=["photo"])


JOBS = {
    "reminders": reminders,
    "digest": digest,
    "feedback-requests": feedback_requests,
    "documents": documents,
    "stock": stock,
    "hygiene-morning": lambda: missed_hygiene(MORNING),
    "hygiene-evening": lambda: missed_hygiene(EVENING),
    "tips": tips,
    "purge-photos": purge_photos,
}
