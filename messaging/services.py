from .models import Message
from .whatsapp import send_template


def send_to_patient(patient, kind, template, params=()):
    """Send only if the patient consented (and, for tips, subscribed). Always logs a Message row."""
    message = Message(
        clinic=patient.clinic, patient=patient, to_phone=patient.phone,
        kind=kind, template=template, params=list(params),
    )
    if not patient.consent_whatsapp:
        message.status, message.error = Message.SKIPPED, "No WhatsApp consent"
    elif kind == Message.TIP and not patient.tips_subscribed:
        message.status, message.error = Message.SKIPPED, "Not subscribed to tips"
    else:
        message.status, message.error = send_template(patient.phone, template, params, patient.language)
    message.save()
    return message


def send_to_staff(user, kind, template, params=()):
    """Alerts to doctors. No consent check: it is the clinic's own staff."""
    status, error = send_template(user.phone, template, params)
    return Message.objects.create(
        clinic=user.clinic, to_phone=user.phone, kind=kind,
        template=template, params=list(params), status=status, error=error,
    )


def alert_doctors(clinic, template, params=(), kind=Message.ALERT):
    for doctor in clinic.staff.filter(role="doctor", is_active=True):
        send_to_staff(doctor, kind, template, params)
