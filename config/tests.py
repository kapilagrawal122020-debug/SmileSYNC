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

    def test_doctor_self_signup_creates_clinic_and_logs_in(self):
        body = {
            "role": "doctor", "name": "Kapil Agrawal", "phone": "8979772850", "password": "Kap@123il",
            "clinic_name": "Kapil Clinic", "city": "Kotdwara", "designation": "BDS",
            "speciality": "Ortho", "registration_number": "13935",
        }
        r = self.client.post("/api/auth/register/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["user"]["role"], "doctor")
        user = User.objects.get(phone="8979772850")
        self.assertEqual(user.clinic.name, "Kapil Clinic")
        self.assertNotEqual(user.password, "Kap@123il")  # stored hashed
        self.client.credentials(HTTP_AUTHORIZATION="Token " + r.data["token"])
        self.assertEqual(self.client.get("/api/auth/me/").data["phone"], "8979772850")
        self.assertEqual(self.client.get("/api/dashboard/").status_code, 200)

    def test_signup_rejects_duplicates_weak_passwords_and_other_roles(self):
        ok = {"name": "A", "phone": "8000000001", "password": "Str0ng!pass", "clinic_name": "C"}
        self.assertEqual(self.client.post("/api/auth/register/", {**ok, "phone": "9000000001"}, format="json").status_code, 400)  # phone taken
        self.assertEqual(self.client.post("/api/auth/register/", {**ok, "password": "12345"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/auth/register/", {**ok, "role": "assistant"}, format="json").status_code, 400)
        self.assertEqual(Clinic.objects.filter(name="C").count(), 0)  # nothing half-created

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

    # ---- advance payments -------------------------------------------------------------
    def new_advance(self, amount="10000", **extra):
        body = {"patient": self.patient.id, "amount": amount, "method": "upi", "reference": "UTR9", "note": "For braces", **extra}
        r = self.client.post("/api/advances/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def new_invoice(self, amount="34000", **extra):
        body = {"patient": self.patient.id, "items": [{"description": "Braces", "amount": amount}], **extra}
        r = self.client.post("/api/invoices/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def test_advance_is_applied_when_the_invoice_is_created(self):
        self.login(self.assistant)
        adv = self.new_advance("10000")
        self.assertEqual(adv["balance"], "10000.00")
        self.assertEqual(adv["received_by_name"], "Asha")

        inv = self.new_invoice("34000", apply_advance=True)
        self.assertEqual((inv["total"], inv["advance_applied"], inv["paid"], inv["due"]), ("34000.00", "10000.00", "10000.00", "24000.00"))
        self.assertEqual(inv["payments"][0]["method"], "advance")
        self.assertEqual(inv["payments"][0]["advance"], adv["id"])

        after = self.client.get(f"/api/advances/{adv['id']}/").data
        self.assertEqual((after["used"], after["balance"]), ("10000.00", "0.00"))
        s = self.client.get(f"/api/patients/{self.patient.id}/billing/").data
        self.assertEqual((s["billed"], s["paid"], s["due"], s["advance_balance"]), ("34000.00", "10000.00", "24000.00", "0.00"))
        self.login(self.doctor)
        self.assertEqual(self.client.get("/api/dashboard/").data["dues"], Decimal("24000"))  # advance money counts as paid

    def test_without_the_flag_the_advance_is_left_alone(self):
        self.login(self.assistant)
        adv = self.new_advance("5000")
        inv = self.new_invoice("2000")
        self.assertEqual(inv["paid"], "0.00")
        self.assertEqual(self.client.get(f"/api/advances/{adv['id']}/").data["balance"], "5000.00")

    def test_oldest_advance_is_used_first_and_can_be_split(self):
        self.login(self.assistant)
        older = self.new_advance("3000", received_at=(timezone.now() - timedelta(days=3)).isoformat())
        newer = self.new_advance("4000")
        inv = self.new_invoice("5000")
        r = self.client.post(f"/api/invoices/{inv['id']}/apply-advance/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["due"], "0.00")
        self.assertEqual(sorted((p["advance"], p["amount"]) for p in r.data["payments"]), sorted([(older["id"], "3000.00"), (newer["id"], "2000.00")]))
        self.assertEqual(self.client.get(f"/api/advances/{older['id']}/").data["balance"], "0.00")
        self.assertEqual(self.client.get(f"/api/advances/{newer['id']}/").data["balance"], "2000.00")

    def test_apply_advance_limits(self):
        self.login(self.assistant)
        self.new_advance("1000")
        inv = self.new_invoice("5000")
        url = f"/api/invoices/{inv['id']}/apply-advance/"
        self.assertEqual(self.client.post(url, {"amount": "2000"}, format="json").status_code, 400)  # only 1000 available
        self.assertEqual(self.client.post(url, {"amount": "0"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"amount": "400"}, format="json").data["due"], "4600.00")
        self.assertEqual(self.client.post(url, {}, format="json").data["due"], "4000.00")  # the 600 left
        self.assertEqual(self.client.post(url, {}, format="json").data["due"], "4000.00")  # nothing left: no error, no change
        small = self.new_invoice("300")
        self.assertEqual(self.client.post(f"/api/invoices/{small['id']}/apply-advance/", {"amount": "500"}, format="json").status_code, 400)  # more than due

    def test_a_payment_cannot_claim_to_be_from_an_advance(self):
        self.login(self.assistant)
        inv = self.new_invoice("1000")
        r = self.client.post("/api/payments/", {"invoice": inv["id"], "amount": "500", "method": "advance"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_refund_is_recorded_and_doctor_only(self):
        self.login(self.assistant)
        adv = self.new_advance("5000")
        inv = self.new_invoice("1000")
        self.client.post(f"/api/invoices/{inv['id']}/apply-advance/", {}, format="json")  # 1000 used, 4000 left
        self.assertEqual(self.client.post(f"/api/advances/{adv['id']}/refund/", {"amount": "100"}, format="json").status_code, 403)

        self.login(self.doctor)
        url = f"/api/advances/{adv['id']}/refund/"
        self.assertEqual(self.client.post(url, {"amount": "4500"}, format="json").status_code, 400)  # more than is left
        r = self.client.post(url, {"amount": "1500", "note": "Treatment cancelled"}, format="json")
        self.assertEqual((r.data["refunded"], r.data["balance"]), ("1500.00", "2500.00"))
        r = self.client.post(url, {}, format="json")  # no amount: the rest
        self.assertEqual((r.data["refunded"], r.data["balance"]), ("4000.00", "0.00"))
        self.assertEqual(len(r.data["refunds"]), 2)  # one row per refund
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)  # nothing left

    def test_delete_only_unused_advances_by_the_doctor(self):
        self.login(self.assistant)
        used = self.new_advance("800", received_at=(timezone.now() - timedelta(days=1)).isoformat())  # older, so used first
        mistake = self.new_advance("700")
        inv = self.new_invoice("800")
        self.client.post(f"/api/invoices/{inv['id']}/apply-advance/", {"amount": "800"}, format="json")
        self.assertEqual(self.client.delete(f"/api/advances/{mistake['id']}/").status_code, 403)  # assistants cannot delete money records
        self.login(self.doctor)
        self.assertEqual(self.client.delete(f"/api/advances/{used['id']}/").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/advances/{mistake['id']}/").status_code, 204)

    def test_advance_input_rules(self):
        self.login(self.assistant)
        base = {"patient": self.patient.id, "method": "cash"}
        for bad in ({"amount": "0"}, {"amount": "-5"}, {"amount": "100", "received_at": (timezone.now() + timedelta(days=2)).isoformat()}):
            self.assertEqual(self.client.post("/api/advances/", {**base, **bad}, format="json").status_code, 400, bad)
        past = self.client.post("/api/advances/", {**base, "amount": "100", "received_at": (timezone.now() - timedelta(days=30)).isoformat()}, format="json")
        self.assertEqual(past.status_code, 201)  # money taken earlier can be recorded with its real date
        self.assertEqual(self.client.patch(f"/api/advances/{past.data['id']}/", {"amount": "999"}, format="json").status_code, 405)  # never edited

    def test_advances_stay_inside_the_clinic_and_patients_see_their_own(self):
        self.login(self.assistant)
        adv = self.new_advance("2500")
        self.login(self.other_doctor)
        self.assertEqual(self.client.get(f"/api/advances/{adv['id']}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.id}/billing/").status_code, 404)
        self.assertEqual(self.client.post("/api/advances/", {"patient": self.patient.id, "amount": "1"}, format="json").status_code, 400)

        self.patient.user = User.objects.create_user("9111111111", None, role="patient")
        self.patient.save()
        self.login(self.patient.user)
        self.assertEqual(self.client.get("/api/advances/").data["count"], 1)
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.id}/billing/").data["advance_balance"], "2500.00")
        self.assertEqual(self.client.post("/api/advances/", {"patient": self.patient.id, "amount": "1"}, format="json").status_code, 403)
        self.assertEqual(self.client.post(f"/api/advances/{adv['id']}/refund/", {}, format="json").status_code, 403)

    # ---- paying an invoice at creation, and the dues that are left -----------------------
    def test_invoice_created_with_money_already_paid(self):
        self.login(self.assistant)
        inv = self.new_invoice("34000", paid_now="20000", paid_method="upi", paid_reference="UTR55")
        self.assertEqual((inv["total"], inv["paid"], inv["due"], inv["advance_applied"]), ("34000.00", "20000.00", "14000.00", "0.00"))
        self.assertEqual((inv["payments"][0]["method"], inv["payments"][0]["amount"], inv["payments"][0]["reference"]), ("upi", "20000.00", "UTR55"))
        self.login(self.doctor)
        self.assertEqual(self.client.get("/api/dashboard/").data["dues"], Decimal("14000"))

    def test_advance_and_money_paid_now_together(self):
        self.login(self.assistant)
        self.new_advance("10000")
        inv = self.new_invoice("34000", apply_advance=True, paid_now="20000")
        self.assertEqual((inv["paid"], inv["advance_applied"], inv["due"]), ("30000.00", "10000.00", "4000.00"))
        self.assertEqual(sorted(p["method"] for p in inv["payments"]), ["advance", "cash"])

    def test_paid_now_cannot_exceed_what_is_due_and_nothing_is_saved(self):
        from patients.models import Invoice
        self.login(self.assistant)
        body = {"patient": self.patient.id, "items": [{"description": "Braces", "amount": "34000"}]}
        r = self.client.post("/api/invoices/", {**body, "paid_now": "40000"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("paid_now", r.data)
        self.new_advance("10000")
        too_much = self.client.post("/api/invoices/", {**body, "apply_advance": True, "paid_now": "25000"}, format="json")  # only 24000 is due after the advance
        self.assertEqual(too_much.status_code, 400)
        self.assertEqual(self.client.post("/api/invoices/", {**body, "paid_now": "0"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/invoices/", {**body, "paid_now": "100", "paid_method": "advance"}, format="json").status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)  # the failed attempts left no invoice behind

    def test_billing_summary_shows_what_is_left_after_the_advance(self):
        self.login(self.assistant)
        self.new_advance("20000")
        self.new_invoice("34000")  # advance not used yet
        s = self.client.get(f"/api/patients/{self.patient.id}/billing/").data
        self.assertEqual((s["due"], s["advance_balance"], s["due_after_advance"]), ("34000.00", "20000.00", "14000.00"))
        self.new_advance("30000")  # more advance than is due: nothing left to pay, never negative
        s = self.client.get(f"/api/patients/{self.patient.id}/billing/").data
        self.assertEqual(s["due_after_advance"], "0.00")

    # ---- CORS -------------------------------------------------------------------------
    @override_settings(CORS_ALLOWED_ORIGINS=["https://app.netlify.app"], CORS_ALLOW_ALL_ORIGINS=False)
    def test_cors_allows_listed_frontend_only(self):
        ask = {"HTTP_ACCESS_CONTROL_REQUEST_METHOD": "POST", "HTTP_ACCESS_CONTROL_REQUEST_HEADERS": "content-type,authorization"}
        ok = self.client.options("/api/auth/login/", HTTP_ORIGIN="https://app.netlify.app", **ask)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok["Access-Control-Allow-Origin"], "https://app.netlify.app")
        bad = self.client.options("/api/auth/login/", HTTP_ORIGIN="https://evil.example", **ask)
        self.assertNotIn("Access-Control-Allow-Origin", bad)

    # ---- admin ------------------------------------------------------------------------
    def test_admin_user_add_page_works(self):
        admin = User.objects.create_superuser("9222222222", "pw12345", name="Root")
        self.client.force_login(admin)
        self.assertEqual(self.client.get("/admin/accounts/user/add/").status_code, 200)
