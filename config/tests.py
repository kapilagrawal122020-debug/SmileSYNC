"""End-to-end tests of the flows from the product brief. Run: python manage.py test"""
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from clinic.models import Chair, Clinic
from inventory.models import StockItem, UsageRule
from messaging import jobs
from messaging.models import Message
from patients.models import Patient, Visit


def photo(n):
    """A tiny fake JPEG; `n` makes every file different."""
    return SimpleUploadedFile(f"p{n}.jpg", b"fake-jpeg-%d" % n, content_type="image/jpeg")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), DEBUG=True)  # DEBUG: dev OTP and unsigned webhook
class SmileSyncTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.clinic = Clinic.objects.create(name="Smile Care", google_review_url="https://g.page/smile")
        cls.other_clinic = Clinic.objects.create(name="Other Clinic")
        cls.doctor = User.objects.create_user("9000000001", "pw12345", name="Dr A", role="doctor", clinic=cls.clinic)
        cls.assistant = User.objects.create_user("9000000002", "pw12345", name="Asha", role="assistant", clinic=cls.clinic)
        cls.other_doctor = User.objects.create_user("9000000003", "pw12345", name="Dr B", role="doctor", clinic=cls.other_clinic)
        cls.patient = Patient.objects.create(
            clinic=cls.clinic, name="Ravi", phone="9111111111", consent_data=True,
            consent_whatsapp=True, private_notes="asks for discount",
        )
        cls.chair = Chair.objects.create(clinic=cls.clinic, name="Chair 1")

    def login(self, user):
        self.client.force_authenticate(user)

    # ---- auth -------------------------------------------------------------------------
    def test_staff_login_with_phone_and_password(self):
        r = self.client.post("/api/auth/login/", {"phone": "+91 90000 00001", "password": "pw12345"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("token", r.data)
        bad = self.client.post("/api/auth/login/", {"phone": "9000000001", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)

    def test_patient_otp_login_sees_only_own_data(self):
        r = self.client.post("/api/auth/otp/request/", {"phone": "9111111111"})
        code = r.data["dev_otp"]
        self.assertEqual(self.client.post("/api/auth/otp/verify/", {"phone": "9111111111", "code": "000000"}).status_code, 400)
        r = self.client.post("/api/auth/otp/verify/", {"phone": "9111111111", "code": code})
        self.assertEqual(r.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION="Token " + r.data["token"])
        self.assertEqual(self.client.get("/api/patients/").data["count"], 1)
        self.assertEqual(self.client.get("/api/inventory/items/").status_code, 403)
        self.assertEqual(self.client.get("/api/treatments/").status_code, 403)

    # ---- registration -----------------------------------------------------------------
    def test_register_patient_rules(self):
        self.login(self.assistant)
        body = {"name": "Sita", "phone": "98765 43210", "consent_data": True, "consent_whatsapp": True}
        r = self.client.post("/api/patients/", body)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["phone"], "9876543210")
        self.assertEqual(self.client.post("/api/patients/", body).status_code, 400)  # duplicate phone
        no_consent = self.client.post("/api/patients/", {"name": "X", "phone": "9876500000"})
        self.assertEqual(no_consent.status_code, 400)

    def test_private_notes_are_doctor_only(self):
        self.login(self.assistant)
        self.assertNotIn("private_notes", self.client.get(f"/api/patients/{self.patient.id}/").data)
        self.client.patch(f"/api/patients/{self.patient.id}/", {"private_notes": "hacked"})
        self.login(self.doctor)
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.id}/").data["private_notes"], "asks for discount")

    def test_other_clinic_cannot_see_or_use_my_patient(self):
        self.login(self.other_doctor)
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.id}/").status_code, 404)
        r = self.client.post("/api/visits/", {"patient": self.patient.id, "scheduled_at": timezone.now().isoformat()})
        self.assertEqual(r.status_code, 400)

    # ---- treatment + inventory --------------------------------------------------------
    def test_completing_treatment_deducts_stock(self):
        tube = StockItem.objects.create(clinic=self.clinic, name="Composite A2", unit="g", pack_size=3, quantity=3, reorder_level=1.5)
        UsageRule.objects.create(clinic=self.clinic, procedure="Composite Class 1", item=tube, quantity="0.2")
        UsageRule.objects.create(clinic=self.clinic, procedure="Composite Class 2", item=tube, quantity="0.6")

        self.login(self.doctor)
        for procedure, units in [("Composite Class 1", 3), ("Composite Class 2", 2)]:
            t = self.client.post("/api/treatments/", {"patient": self.patient.id, "procedure": procedure, "units": units}).data
            r = self.client.post(f"/api/treatments/{t['id']}/complete/")
            self.assertEqual(r.status_code, 200, r.data)
            self.assertEqual(self.client.post(f"/api/treatments/{t['id']}/complete/").data["stock_used"], [])  # only once

        tube.refresh_from_db()
        self.assertEqual(tube.quantity, Decimal("1.20"))  # 3 g - 0.6 g - 1.2 g, as in the brief
        alerts = self.client.get("/api/inventory/items/alerts/").data
        self.assertEqual([i["name"] for i in alerts["low_stock"]], ["Composite A2"])

    def test_assistant_can_stock_in_but_not_edit_items_or_treatments(self):
        tube = StockItem.objects.create(clinic=self.clinic, name="Gloves", unit="pcs", pack_size=100, quantity=0)
        self.login(self.assistant)
        r = self.client.post(f"/api/inventory/items/{tube.id}/stock-in/", {"packs": 5})
        self.assertEqual(r.data["quantity"], "500.00")
        self.assertEqual(self.client.patch(f"/api/inventory/items/{tube.id}/", {"name": "x"}).status_code, 403)
        self.assertEqual(self.client.post("/api/treatments/", {"patient": self.patient.id, "procedure": "x"}).status_code, 403)

    def test_monthly_count_logs_variance(self):
        item = StockItem.objects.create(clinic=self.clinic, name="Gloves", unit="pcs", quantity=100)
        self.login(self.doctor)
        r = self.client.post(f"/api/inventory/items/{item.id}/count/", {"counted": 90})
        self.assertEqual(r.data["variance"], Decimal("-10"))
        self.assertEqual(self.client.get(f"/api/inventory/movements/?item={item.id}").data["count"], 1)

    # ---- hygiene ----------------------------------------------------------------------
    def test_chair_locked_until_four_hygiene_photos(self):
        self.login(self.assistant)
        v = self.client.post("/api/visits/", {"patient": self.patient.id, "chair": self.chair.id, "scheduled_at": timezone.now().isoformat()}).data
        self.client.patch(f"/api/visits/{v['id']}/", {"status": "in_progress"})
        self.client.patch(f"/api/visits/{v['id']}/", {"status": "completed"})
        self.chair.refresh_from_db()
        self.assertEqual(self.chair.status, "locked")

        # a locked chair cannot start another visit
        v2 = self.client.post("/api/visits/", {"patient": self.patient.id, "chair": self.chair.id, "scheduled_at": timezone.now().isoformat()}).data
        self.assertEqual(self.client.patch(f"/api/visits/{v2['id']}/", {"status": "in_progress"}).status_code, 400)

        for i, area in enumerate(["spit_basin", "chair_seat", "suction_tips", "rotors"]):
            self.chair.refresh_from_db()
            self.assertEqual(self.chair.status, "locked")
            r = self.client.post("/api/hygiene/checks/", {"kind": "after_patient", "area": area, "visit": v["id"], "photo": photo(i)}, format="multipart")
            self.assertEqual(r.status_code, 201, r.data)
        self.chair.refresh_from_db()
        self.assertEqual(self.chair.status, "ready")

        # same photo again is rejected
        dup = self.client.post("/api/hygiene/checks/", {"kind": "morning", "area": "waiting", "photo": photo(0)}, format="multipart")
        self.assertEqual(dup.status_code, 400)

    def test_doctor_reviews_hygiene_photo_and_protected_download(self):
        self.login(self.assistant)
        r = self.client.post("/api/hygiene/checks/", {"kind": "morning", "area": "waiting", "photo": photo(7)}, format="multipart")
        check_id = r.data["id"]
        self.assertEqual(self.client.post(f"/api/hygiene/checks/{check_id}/review/", {"status": "approved"}).status_code, 403)
        self.login(self.doctor)
        self.assertEqual(self.client.post(f"/api/hygiene/checks/{check_id}/review/", {"status": "approved"}).status_code, 200)
        self.assertEqual(self.client.get("/api/hygiene/checks/score/").data["score"], 100)
        self.assertEqual(self.client.get(f"/api/hygiene/checks/{check_id}/photo/").status_code, 200)
        self.login(self.other_doctor)
        self.assertEqual(self.client.get(f"/api/hygiene/checks/{check_id}/photo/").status_code, 404)

    # ---- images, billing, feedback ----------------------------------------------------
    def test_image_upload_and_patient_view(self):
        self.login(self.assistant)
        r = self.client.post("/api/images/", {"patient": self.patient.id, "kind": "xray", "tooth": "36", "stage": "before", "file": photo(1)}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        bad = self.client.post("/api/images/", {"patient": self.patient.id, "file": SimpleUploadedFile("x.exe", b"MZ")}, format="multipart")
        self.assertEqual(bad.status_code, 400)

    def test_invoice_and_payments(self):
        self.login(self.assistant)
        inv = self.client.post("/api/invoices/", {"patient": self.patient.id, "items": [
            {"description": "Filling", "amount": "1500"}, {"description": "X-ray", "amount": "200"}]}, format="json").data
        self.assertEqual(inv["total"], "1700.00")
        self.assertEqual(self.client.post("/api/payments/", {"invoice": inv["id"], "amount": "1000", "method": "upi", "reference": "UTR123"}).status_code, 201)
        self.assertEqual(self.client.post("/api/payments/", {"invoice": inv["id"], "amount": "800"}).status_code, 400)  # more than due
        self.assertEqual(self.client.get(f"/api/invoices/{inv['id']}/").data["due"], "700.00")
        self.login(self.doctor)
        self.assertEqual(self.client.get("/api/dashboard/").data["dues"], Decimal("700"))

    def test_patient_feedback_low_rating_alerts_doctor(self):
        self.patient.user = User.objects.create_user("9111111111", None, role="patient")
        self.patient.save()
        self.login(self.patient.user)
        r = self.client.post("/api/feedback/", {"patient": self.patient.id, "rating": 2, "comment": "Long wait"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(Message.objects.filter(kind="alert", template="low_rating_alert", to_phone="9000000001").exists())
        self.login(self.doctor)
        fid = self.client.get("/api/feedback/").data["results"][0]["id"]
        self.assertEqual(self.client.post(f"/api/feedback/{fid}/reply/", {"reply": "Sorry!"}).data["reply"], "Sorry!")

    # ---- messaging --------------------------------------------------------------------
    def test_reminder_job_respects_consent_and_reply_confirms(self):
        when = timezone.now() + timedelta(days=1)
        visit = Visit.objects.create(patient=self.patient, scheduled_at=when)
        jobs.reminders()
        self.assertEqual(Message.objects.get(kind="reminder").status, "dry_run")
        jobs.reminders()  # not sent twice
        self.assertEqual(Message.objects.filter(kind="reminder").count(), 1)

        r = self.client.post("/api/whatsapp/webhook/", {"entry": [{"changes": [{"value": {"messages": [{"from": "919111111111", "text": {"body": "1"}}]}}]}]}, format="json")
        self.assertEqual(r.status_code, 200)
        visit.refresh_from_db()
        self.assertEqual(visit.status, "confirmed")

        self.patient.consent_whatsapp = False
        self.patient.save()
        self.login(self.doctor)
        res = self.client.post("/api/messages/broadcast/", {"template": "festive_offer", "params": ["Diwali"]}, format="json")
        self.assertEqual(res.data["patients"], 0)  # opted-out patients are not messaged

    def test_whatsapp_stop_withdraws_consent(self):
        self.client.post("/api/whatsapp/webhook/", {"entry": [{"changes": [{"value": {"messages": [{"from": "919111111111", "text": {"body": "STOP"}}]}}]}]}, format="json")
        self.patient.refresh_from_db()
        self.assertFalse(self.patient.consent_whatsapp)

    # ---- admin ------------------------------------------------------------------------
    def test_admin_user_add_page_works(self):
        admin = User.objects.create_superuser("9222222222", "pw12345", name="Root")
        self.client.force_login(admin)
        self.assertEqual(self.client.get("/admin/accounts/user/add/").status_code, 200)
