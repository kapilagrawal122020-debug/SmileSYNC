"""Tooth numbers, the medical checklist, and appointment overlap, cancel and reschedule."""
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from clinic.models import Chair, Clinic
from masterdata.models import ListValue
from patients.models import Patient, Visit
from patients.teeth import FDI_TEETH, clean_teeth


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class FeatureTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.clinic = Clinic.objects.create(name="Smile Care")
        cls.other = Clinic.objects.create(name="Other")
        cls.doctor = User.objects.create_user("9000000001", "pw12345!", name="Dr A", role="doctor", clinic=cls.clinic)
        cls.doctor2 = User.objects.create_user("9000000005", "pw12345!", name="Dr B", role="doctor", clinic=cls.clinic)
        cls.assistant = User.objects.create_user("9000000002", "pw12345!", name="Asha", role="assistant", clinic=cls.clinic)
        cls.stranger = User.objects.create_user("9000000003", "pw12345!", name="Dr Z", role="doctor", clinic=cls.other)
        cls.patient = Patient.objects.create(clinic=cls.clinic, name="Ravi", phone="9111111111", consent_data=True)
        cls.patient2 = Patient.objects.create(clinic=cls.clinic, name="Priya", phone="9222222222", consent_data=True)
        cls.chair1 = Chair.objects.create(clinic=cls.clinic, name="Chair 1")
        cls.chair2 = Chair.objects.create(clinic=cls.clinic, name="Chair 2")
        cls.noon = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)

    def login(self, user):
        self.client.force_authenticate(user)

    # ---- tooth numbers -----------------------------------------------------------------------
    def test_the_fdi_set_is_the_real_one(self):
        self.assertEqual(len(FDI_TEETH), 52)  # 32 adult + 20 primary
        for ok in ("11", "18", "26", "48", "51", "55", "85"):
            self.assertIn(ok, FDI_TEETH)
        for bad in ("19", "29", "49", "56", "10", "00", "9", "111"):
            self.assertNotIn(bad, FDI_TEETH)

    def test_teeth_are_cleaned_and_checked(self):
        self.assertEqual(clean_teeth("12 14,16;22"), "12, 14, 16, 22")
        self.assertEqual(clean_teeth("26, 26"), "26")
        self.assertEqual(clean_teeth(""), "")

    def test_treatments_and_images_refuse_fake_teeth(self):
        self.login(self.doctor)
        post = lambda tooth: self.client.post("/api/treatments/", {"patient": self.patient.id, "procedure": "Filling", "tooth": tooth}, format="json")
        self.assertEqual(post("26").status_code, 201)
        self.assertEqual(post("12 14").data["tooth"], "12, 14")
        bad = post("49")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("tooth", bad.data)
        self.assertEqual(post("upper left").status_code, 400)
        self.login(self.assistant)
        photo = lambda tooth: SimpleUploadedFile("a.jpg", b"jpeg-" + tooth.encode(), content_type="image/jpeg")
        r = self.client.post("/api/images/", {"patient": self.patient.id, "tooth": "36", "file": photo("36")}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        r = self.client.post("/api/images/", {"patient": self.patient.id, "tooth": "99", "file": photo("99")}, format="multipart")
        self.assertEqual(r.status_code, 400)

    # ---- medical history --------------------------------------------------------------------------
    def history(self, **body):
        return self.client.post(f"/api/patients/{self.patient.id}/history/", body, format="json")

    def test_history_starts_empty_then_keeps_every_review(self):
        self.login(self.assistant)
        first = self.client.get(f"/api/patients/{self.patient.id}/history/").data
        self.assertEqual((first["exists"], first["alerts"], first["versions"]), (False, [], 0))
        r = self.history(conditions=["diabetes", "hypertension", "hepatitis-b"], allergies=["penicillin"], medicines=["anticoagulant"], habits=["areca-nut"], notes="Takes tablets after breakfast")
        self.assertEqual(r.status_code, 201, r.data)
        labels = {a["code"]: a["kind"] for a in r.data["alerts"]}
        self.assertEqual(labels, {"penicillin": "allergy", "anticoagulant": "medicine", "diabetes": "condition", "hypertension": "condition"})
        self.assertNotIn("hepatitis-b", labels)  # infection status is never put in the banner
        self.assertEqual(r.data["reviewed_by_name"], "Asha")
        self.history(allergies=["latex"])
        latest = self.client.get(f"/api/patients/{self.patient.id}/history/").data
        self.assertEqual((latest["allergies"], latest["versions"]), (["latex"], 2))
        self.assertEqual(latest["alerts"][0]["label"], "Latex")
        self.assertEqual(self.patient.history_versions.count(), 2)

    def test_history_only_accepts_codes_from_the_lists(self):
        self.login(self.assistant)
        self.assertEqual(self.history(allergies=["penicillin", "unicorn"]).status_code, 400)
        self.assertEqual(self.history(conditions="diabetes").status_code, 400)  # must be a list
        ListValue.objects.filter(list__key="allergies", code="latex").update(is_active=False)
        r = self.history(allergies=["latex"])
        self.assertEqual(r.status_code, 400)  # turned off in the admin dashboard
        self.assertEqual(self.history(allergies=["penicillin", "penicillin"]).data["allergies"], ["penicillin"])

    def test_history_follows_the_access_rules(self):
        self.login(self.stranger)
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.id}/history/").status_code, 404)
        self.assertEqual(self.history(allergies=["latex"]).status_code, 404)
        patient_user = User.objects.create_user("9111111111", None, role="patient")
        self.patient.user = patient_user
        self.patient.save()
        self.login(patient_user)
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.id}/history/").status_code, 200)
        self.assertEqual(self.history(allergies=["latex"]).status_code, 403)  # patients read, clinic staff write

    # ---- appointments ---------------------------------------------------------------------------------
    def book(self, patient=None, start=None, minutes=30, chair=None, doctor=None, **extra):
        body = {"patient": (patient or self.patient).id, "scheduled_at": (start or self.noon).isoformat(), "duration_minutes": minutes, **extra}
        if chair:
            body["chair"] = chair.id
        if doctor:
            body["doctor"] = doctor.id
        return self.client.post("/api/visits/", body, format="json")

    def test_a_chair_or_doctor_cannot_be_double_booked(self):
        self.login(self.assistant)
        first = self.book(chair=self.chair1, doctor=self.doctor, minutes=45)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertIn("end_at", first.data)
        clash = self.book(self.patient2, self.noon + timedelta(minutes=30), chair=self.chair1)
        self.assertEqual(clash.status_code, 400)
        self.assertIn("chair", clash.data)
        self.assertIn("Ravi", str(clash.data["chair"]))  # says who has it
        same_doctor = self.book(self.patient2, self.noon + timedelta(minutes=15), chair=self.chair2, doctor=self.doctor)
        self.assertEqual(same_doctor.status_code, 400)
        self.assertIn("doctor", same_doctor.data)
        # touching is fine, other chair and doctor is fine
        self.assertEqual(self.book(self.patient2, self.noon + timedelta(minutes=45), chair=self.chair1, doctor=self.doctor).status_code, 201)
        self.assertEqual(self.book(self.patient2, self.noon, chair=self.chair2, doctor=self.doctor2).status_code, 201)

    def test_cancelled_and_no_show_visits_free_the_slot(self):
        self.login(self.assistant)
        v = self.book(chair=self.chair1).data
        self.assertEqual(self.book(self.patient2, chair=self.chair1).status_code, 400)
        self.client.patch(f"/api/visits/{v['id']}/", {"status": "cancelled", "cancel_reason": "patient-cancelled"}, format="json")
        self.assertEqual(self.book(self.patient2, chair=self.chair1).status_code, 201)

    def test_moving_a_visit_into_a_clash_is_refused(self):
        self.login(self.assistant)
        a = self.book(chair=self.chair1).data
        b = self.book(self.patient2, self.noon + timedelta(hours=2), chair=self.chair1).data
        r = self.client.patch(f"/api/visits/{b['id']}/", {"scheduled_at": (self.noon + timedelta(minutes=10)).isoformat()}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Visit.objects.get(pk=b["id"]).scheduled_at, self.noon + timedelta(hours=2))
        self.assertEqual(self.book(self.patient2, minutes=3).status_code, 400)  # length must be sensible

    def test_cancel_needs_a_real_reason_from_the_list(self):
        self.login(self.assistant)
        v = self.book().data
        self.assertEqual(self.client.patch(f"/api/visits/{v['id']}/", {"status": "cancelled", "cancel_reason": "made-up"}, format="json").status_code, 400)
        r = self.client.patch(f"/api/visits/{v['id']}/", {"status": "cancelled", "cancel_reason": "doctor-unavailable"}, format="json")
        self.assertEqual((r.status_code, r.data["status"], r.data["cancel_reason"]), (200, "cancelled", "doctor-unavailable"))

    def test_reschedule_resets_status_and_reminder_and_checks_overlap(self):
        self.login(self.assistant)
        v = self.book(chair=self.chair1, status="reschedule").data
        Visit.objects.filter(pk=v["id"]).update(reminder_sent=True)
        new_time = self.noon + timedelta(days=1, hours=1)
        r = self.client.post(f"/api/visits/{v['id']}/reschedule/", {"scheduled_at": new_time.isoformat()}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        visit = Visit.objects.get(pk=v["id"])
        self.assertEqual((visit.status, visit.reminder_sent, visit.scheduled_at), ("booked", False, new_time))
        other = self.book(self.patient2, new_time, chair=self.chair1).data if False else None
        self.book(self.patient2, new_time + timedelta(hours=3), chair=self.chair1)
        r = self.client.post(f"/api/visits/{v['id']}/reschedule/", {"scheduled_at": (new_time + timedelta(hours=3)).isoformat()}, format="json")
        self.assertEqual(r.status_code, 400)  # that slot is taken
        for status in ("completed", "in_progress", "cancelled"):
            Visit.objects.filter(pk=v["id"]).update(status=status)
            self.assertEqual(self.client.post(f"/api/visits/{v['id']}/reschedule/", {"scheduled_at": new_time.isoformat()}, format="json").status_code, 400, status)
