from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from clinic.models import Chair, Clinic
from inventory.models import StockItem, UsageRule
from patients.models import Patient


class Command(BaseCommand):
    help = "Create a demo clinic with a doctor, an assistant, stock and a patient (development only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Demo data is only for development (DEBUG=1).")

        clinic, _ = Clinic.objects.get_or_create(name="Smile Care Dental", defaults={"city": "Pune"})
        for phone, name, role, password in [
            ("9000000001", "Dr. Demo", User.DOCTOR, "doctor123"),
            ("9000000002", "Asha (assistant)", User.ASSISTANT, "assist123"),
        ]:
            if not User.objects.filter(phone=phone).exists():
                User.objects.create_user(phone=phone, name=name, role=role, password=password, clinic=clinic)

        Chair.objects.get_or_create(clinic=clinic, name="Chair 1")
        Chair.objects.get_or_create(clinic=clinic, name="Chair 2")

        # The A2 composite example from the brief: 3 g tube, 0.2 g per Class 1, 0.6 g per Class 2.
        tube, _ = StockItem.objects.get_or_create(
            clinic=clinic, name="Composite A2", defaults={"unit": "g", "pack_size": 3, "quantity": 3, "reorder_level": 1.5, "shade_or_size": "A2"},
        )
        for procedure, qty in [("Composite Class 1", "0.2"), ("Composite Class 2", "0.6")]:
            UsageRule.objects.get_or_create(clinic=clinic, procedure=procedure, item=tube, defaults={"quantity": qty})
        StockItem.objects.get_or_create(clinic=clinic, name="Gloves", defaults={"unit": "pcs", "pack_size": 100, "quantity": 200, "reorder_level": 50})
        StockItem.objects.get_or_create(clinic=clinic, name="Alginate", defaults={"unit": "g", "pack_size": 500, "quantity": 1000, "reorder_level": 500})

        Patient.objects.get_or_create(
            clinic=clinic, phone="9111111111",
            defaults={"name": "Ravi Kumar", "locality": "Kothrud", "chief_complaint": "Toothache", "consent_data": True, "consent_whatsapp": True},
        )
        self.stdout.write(self.style.SUCCESS(
            "Demo ready. Doctor 9000000001 / doctor123, assistant 9000000002 / assist123, patient 9111111111 (OTP login)."
        ))
