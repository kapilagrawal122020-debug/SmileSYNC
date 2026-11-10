from django.contrib import admin

from .models import Chair, Clinic, ClinicDocument

admin.site.register([Clinic, Chair, ClinicDocument])
