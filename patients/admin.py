from django.contrib import admin

from .models import Feedback, Invoice, InvoiceItem, Patient, PatientImage, Payment, Treatment, Visit

admin.site.register([Patient, Visit, Treatment, PatientImage, Invoice, InvoiceItem, Payment, Feedback])
