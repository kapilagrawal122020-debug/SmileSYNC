from django.contrib import admin

from .models import (
    Advance, AdvanceRefund, Feedback, Invoice, InvoiceItem, Patient, PatientImage, Payment, Treatment, Visit,
)

admin.site.register([
    Patient, Visit, Treatment, PatientImage, Invoice, InvoiceItem, Payment, Advance, AdvanceRefund, Feedback,
])
