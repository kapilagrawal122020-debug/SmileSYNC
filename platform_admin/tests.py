"""The platform admin dashboard: who can reach it, and what it can do."""
from django.core.cache import cache
from django.core.management import call_command
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditEvent
from clinic.models import Clinic
from masterdata.models import ListDefinition, ListValue
from patients.models import Patient


class PlatformAdminTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.clinic = Clinic.objects.create(name="Smile Care", city="Pune")
        cls.other = Clinic.objects.create(name="Other Clinic", city="Delhi")
        cls.admin = User.objects.create_superuser("9000000099", "Adm1n!pass", name="Root")
        cls.doctor = User.objects.create_user("9000000001", "pw12345!", name="Dr A", role="doctor", clinic=cls.clinic)
        cls.consultant = User.objects.create_user("9000000004", "pw12345!", name="Dr C", role="consultant", clinic=cls.clinic)
        cls.assistant = User.objects.create_user("9000000002", "pw12345!", name="Asha", role="assistant", clinic=cls.clinic)
        cls.patient_user = User.objects.create_user("9111111111", None, role="patient")
        cls.patient = Patient.objects.create(clinic=cls.clinic, name="Ravi", phone="9111111111", consent_data=True, user=cls.patient_user)

    def login(self, user):
        self.client.force_authenticate(user)

    # ---- who can reach it ----------------------------------------------------------------
    PLATFORM_GETS = [
        "/api/platform/overview/", "/api/platform/lists/", "/api/platform/lists/visit-statuses/",
        "/api/platform/lists/visit-statuses/export/", "/api/platform/clinics/", "/api/platform/users/", "/api/platform/audit/",
    ]

    def test_only_the_platform_admin_can_open_any_platform_url(self):
        self.client.force_authenticate(None)
        for url in self.PLATFORM_GETS:
            self.assertEqual(self.client.get(url).status_code, 401, f"anonymous {url}")
        for who in (self.doctor, self.consultant, self.assistant, self.patient_user):
            self.login(who)
            for url in self.PLATFORM_GETS:
                self.assertEqual(self.client.get(url).status_code, 403, f"{who.role} {url}")
            self.assertEqual(self.client.post("/api/platform/admins/", {"name": "X", "phone": "9000000077", "password": "Adm1n!pass"}).status_code, 403)
            self.assertEqual(self.client.post("/api/platform/lists/import/", {"lists": []}, format="json").status_code, 403)
            value = ListValue.objects.filter(list__key="visit-statuses", code="booked").first()
            self.assertEqual(self.client.patch(f"/api/platform/list-values/{value.id}/", {"label": "Hacked"}, format="json").status_code, 403)
        self.login(self.admin)
        for url in self.PLATFORM_GETS:
            self.assertEqual(self.client.get(url).status_code, 200, f"admin {url}")

    def test_platform_admin_cannot_read_clinic_or_patient_data(self):
        self.login(self.admin)
        for url in ("/api/patients/", "/api/visits/", "/api/invoices/", "/api/dashboard/", "/api/treatments/", "/api/images/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_nobody_else_can_become_a_platform_admin(self):
        self.login(self.doctor)
        r = self.client.post("/api/users/", {"phone": "9000000066", "name": "Sneaky", "role": "super_admin", "password": "pw12345!"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(f"/api/users/{self.assistant.id}/", {"role": "super_admin"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.get(pk=self.assistant.id).is_superuser)
        # sign-up creates doctors only
        self.client.force_authenticate(None)
        r = self.client.post("/api/auth/register/", {"name": "A", "phone": "8000000001", "password": "Str0ng!pass", "clinic": self.clinic.id, "role": "super_admin"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_super_admin_flag_in_the_signed_in_user_and_command_line_creation(self):
        self.client.force_authenticate(None)
        r = self.client.post("/api/auth/login/", {"phone": "9000000099", "password": "Adm1n!pass"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["user"]["is_super_admin"])
        r = self.client.post("/api/auth/login/", {"phone": "9000000001", "password": "pw12345!"}, format="json")
        self.assertFalse(r.data["user"]["is_super_admin"])
        self.assertEqual(self.admin.role, "super_admin")  # createsuperuser default

    def test_a_turned_off_platform_admin_is_locked_out(self):
        other = User.objects.create_superuser("9000000098", "Adm1n!pass", name="Second")
        token = Token.objects.create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION="Token " + token.key)
        self.assertEqual(self.client.get("/api/platform/overview/").status_code, 200)
        other.is_active = False
        other.save()
        self.assertEqual(self.client.get("/api/platform/overview/").status_code, 401)

    # ---- master lists -------------------------------------------------------------------------
    def test_the_blueprint_lists_are_seeded(self):
        self.assertEqual(ListDefinition.objects.count(), 67)
        self.assertEqual(ListValue.objects.count(), 1035)
        self.assertEqual(ListValue.objects.filter(is_system=True).count(), 44)
        self.assertEqual(ListDefinition.objects.get(key="visit-statuses").kind, "bound")
        self.assertEqual(ListDefinition.objects.get(key="tooth-numbering").kind, "locked")
        call_command("seed_lists")  # running it again changes nothing
        self.assertEqual(ListValue.objects.count(), 1035)

    def test_edit_a_value_but_never_its_code(self):
        self.login(self.admin)
        value = ListValue.objects.get(list__key="referral-sources", code="instagram")
        r = self.client.patch(f"/api/platform/list-values/{value.id}/", {"label": "Instagram or Reels", "labels": {"hi": "इंस्टाग्राम"}, "sort_order": 3}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        value.refresh_from_db()
        self.assertEqual((value.label, value.labels["hi"], value.sort_order), ("Instagram or Reels", "इंस्टाग्राम", 3))
        r = self.client.patch(f"/api/platform/list-values/{value.id}/", {"code": "insta"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(f"/api/platform/list-values/{value.id}/", {"labels": {"hi": 5}}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.delete(f"/api/platform/list-values/{value.id}/").status_code, 405)  # values are turned off, never deleted

    def test_values_the_app_depends_on_cannot_be_turned_off(self):
        self.login(self.admin)
        locked = ListValue.objects.get(list__key="visit-statuses", code="completed")
        self.assertTrue(locked.is_system)
        self.assertEqual(self.client.patch(f"/api/platform/list-values/{locked.id}/", {"is_active": False}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(f"/api/platform/list-values/{locked.id}/", {"label": "Visit finished"}, format="json").status_code, 200)  # label is fine
        free = ListValue.objects.get(list__key="referral-sources", code="newspaper")
        self.assertEqual(self.client.patch(f"/api/platform/list-values/{free.id}/", {"is_active": False}, format="json").status_code, 200)

    def test_add_reorder_and_export(self):
        self.login(self.admin)
        r = self.client.post("/api/platform/lists/referral-sources/values/", {"code": "radio", "label": "Radio advert"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.client.post("/api/platform/lists/referral-sources/values/", {"code": "radio", "label": "Again"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/platform/lists/referral-sources/values/", {"code": "bad code!", "label": "x"}, format="json").status_code, 400)
        ids = [v["id"] for v in self.client.get("/api/platform/lists/referral-sources/").data["values"]]
        self.assertEqual(ids[-1], r.data["id"])  # new values go last
        self.assertEqual(self.client.post("/api/platform/lists/referral-sources/reorder/", {"order": list(reversed(ids))}, format="json").status_code, 200)
        self.assertEqual([v["id"] for v in self.client.get("/api/platform/lists/referral-sources/").data["values"]], list(reversed(ids)))
        self.assertEqual(self.client.post("/api/platform/lists/referral-sources/reorder/", {"order": ids[:3]}, format="json").status_code, 400)  # every value exactly once
        exported = self.client.get("/api/platform/lists/referral-sources/export/").data
        self.assertEqual(exported["list"], "referral-sources")
        self.assertEqual(exported["values"][0]["code"], "radio")
        csv = self.client.get("/api/platform/lists/referral-sources/export/?type=csv")
        self.assertEqual(csv.status_code, 200)
        self.assertTrue(csv.content.decode().startswith("code,label,description,group,sort_order,is_active,is_system"))

    def test_import_is_all_or_nothing(self):
        self.login(self.admin)
        good = {"lists": [{"list": "camp-types", "title": "Camp types", "kind": "clinic", "values": [{"code": "school", "label": "School camp"}, {"code": "office", "label": "Office camp"}]}]}
        dry = self.client.post("/api/platform/lists/import/?dry_run=1", good, format="json")
        self.assertEqual((dry.status_code, dry.data["lists_created"], dry.data["values_created"]), (200, 1, 2))
        self.assertFalse(ListDefinition.objects.filter(key="camp-types").exists())  # dry run saves nothing
        self.assertEqual(self.client.post("/api/platform/lists/import/", good, format="json").status_code, 200)
        self.assertEqual(ListValue.objects.filter(list__key="camp-types").count(), 2)
        # re-import edits the label and leaves the code alone
        good["lists"][0]["values"][0]["label"] = "School or college camp"
        self.client.post("/api/platform/lists/import/", good, format="json")
        self.assertEqual(ListValue.objects.get(list__key="camp-types", code="school").label, "School or college camp")
        bad = {"lists": [{"list": "camp-types", "values": [{"code": "ok", "label": "Fine"}, {"code": "bad code", "label": "Broken"}]}]}
        r = self.client.post("/api/platform/lists/import/", bad, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ListValue.objects.filter(list__key="camp-types", code="ok").exists())  # nothing from the bad file was saved

    def test_apps_read_only_active_values_and_only_when_signed_in(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/lists/").status_code, 401)
        self.login(self.assistant)
        r = self.client.get("/api/lists/?keys=referral-sources,visit-statuses")
        self.assertEqual(set(r.data), {"referral-sources", "visit-statuses"})
        self.assertIn("instagram", [v["code"] for v in r.data["referral-sources"]])
        ListValue.objects.filter(list__key="referral-sources", code="instagram").update(is_active=False)
        r = self.client.get("/api/lists/?keys=referral-sources")
        self.assertNotIn("instagram", [v["code"] for v in r.data["referral-sources"]])
        self.assertGreater(len(self.client.get("/api/lists/").data), 60)

    # ---- accounts and clinics ------------------------------------------------------------------------
    def test_user_list_leaves_out_patients_and_can_filter(self):
        self.login(self.admin)
        r = self.client.get("/api/platform/users/")
        phones = [u["phone"] for u in r.data["results"]]
        self.assertNotIn("9111111111", phones)
        self.assertIn("9000000001", phones)
        self.assertEqual(self.client.get(f"/api/platform/users/?role=assistant").data["count"], 1)
        self.assertEqual(self.client.get("/api/platform/users/?search=Dr C").data["count"], 1)

    def test_turning_an_account_off_signs_it_out(self):
        self.client.force_authenticate(None)
        login = self.client.post("/api/auth/login/", {"phone": "9000000002", "password": "pw12345!"}, format="json")
        token = login.data["token"]
        self.login(self.admin)
        r = self.client.patch(f"/api/platform/users/{self.assistant.id}/", {"is_active": False}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(Token.objects.filter(key=token).exists())
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post("/api/auth/login/", {"phone": "9000000002", "password": "pw12345!"}, format="json").status_code, 401)

    def test_admin_cannot_lock_themselves_or_the_last_admin_out(self):
        self.login(self.admin)
        r = self.client.patch(f"/api/platform/users/{self.admin.id}/", {"is_active": False}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(User.objects.get(pk=self.admin.id).is_active)

    def test_who_someone_is_cannot_be_changed_from_the_dashboard(self):
        self.login(self.admin)
        self.client.patch(f"/api/platform/users/{self.assistant.id}/", {"role": "doctor", "phone": "9999999999", "clinic": self.other.id, "name": "Asha K"}, format="json")
        a = User.objects.get(pk=self.assistant.id)
        self.assertEqual((a.role, a.phone, a.clinic_id, a.name), ("assistant", "9000000002", self.clinic.id, "Asha K"))

    def test_set_password_checks_strength_and_signs_the_person_out(self):
        token = Token.objects.create(user=self.assistant)
        self.login(self.admin)
        url = f"/api/platform/users/{self.assistant.id}/set-password/"
        self.assertEqual(self.client.post(url, {"password": "12345"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"password": "Br4nd!new-pass"}, format="json").status_code, 200)
        self.assertFalse(Token.objects.filter(key=token.key).exists())
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post("/api/auth/login/", {"phone": "9000000002", "password": "Br4nd!new-pass"}, format="json").status_code, 200)

    def test_an_admin_can_create_another_admin(self):
        self.login(self.admin)
        r = self.client.post("/api/platform/admins/", {"name": "Second Admin", "phone": "9000000055", "password": "Adm1n!pass2"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        new = User.objects.get(phone="9000000055")
        self.assertTrue(new.is_superuser and new.is_staff)
        self.assertEqual(new.role, "super_admin")
        self.assertEqual(self.client.post("/api/platform/admins/", {"name": "W", "phone": "9000000056", "password": "123"}, format="json").status_code, 400)

    def test_clinics_overview_and_edit(self):
        self.login(self.admin)
        r = self.client.get("/api/platform/clinics/?search=smile")
        self.assertEqual(r.data["count"], 1)
        self.assertEqual((r.data["results"][0]["staff_count"], r.data["results"][0]["patient_count"]), (3, 1))
        self.assertEqual(self.client.patch(f"/api/platform/clinics/{self.clinic.id}/", {"city": "Mumbai"}, format="json").status_code, 200)
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.city, "Mumbai")
        ov = self.client.get("/api/platform/overview/").data
        self.assertEqual((ov["clinics"], ov["patients"], ov["lists"]["count"]), (2, 1, 67))
        self.assertNotIn("name", str(ov["staff_by_role"]))  # numbers only, no people or patient records here

    # ---- audit trail ------------------------------------------------------------------------------------
    def test_important_actions_are_recorded(self):
        self.client.force_authenticate(None)
        self.client.post("/api/auth/login/", {"phone": "9000000099", "password": "wrong"}, format="json")
        self.client.post("/api/auth/login/", {"phone": "9000000099", "password": "Adm1n!pass"}, format="json")
        self.login(self.admin)
        value = ListValue.objects.get(list__key="referral-sources", code="instagram")
        self.client.patch(f"/api/platform/list-values/{value.id}/", {"label": "Insta"}, format="json")
        actions = list(AuditEvent.objects.values_list("action", flat=True))
        for expected in ("login.failed", "login", "list.value.update"):
            self.assertIn(expected, actions)
        failed = AuditEvent.objects.get(action="login.failed")
        self.assertEqual(failed.user_label, "9000000099")
        r = self.client.get("/api/platform/audit/?action=list.")
        self.assertEqual([e["action"] for e in r.data["results"]], ["list.value.update"])
        self.assertEqual(r.data["results"][0]["detail"]["code"], "instagram")


class ClinicDirectoryTests(APITestCase):
    """The platform admin keeps the clinic directory. Doctors choose from it at sign-up and wait for approval."""

    NEW = {
        "name": "Bright Smiles", "city": "Jaipur", "state": "Rajasthan", "pincode": "302001", "address": "12 MI Road",
        "phone": "01412345678", "email": "hello@bright.example", "poc_name": "Dr Meena Rao", "poc_designation": "Owner",
        "poc_phone": "9812345678", "poc_email": "meena@bright.example", "registration_number": "RJ-123", "admin_notes": "Met at the Jaipur expo",
    }

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("9000000099", "Adm1n!pass", name="Root")
        cls.clinic = Clinic.objects.create(name="Smile Care", city="Pune", poc_name="Dr A", poc_phone="9000000010", admin_notes="secret note")
        cls.hidden = Clinic.objects.create(name="Closed Clinic", city="Delhi", is_active=False)
        cls.doctor = User.objects.create_user("9000000001", "pw12345!", name="Dr A", role="doctor", clinic=cls.clinic)

    def setUp(self):
        cache.clear()

    def test_admin_adds_a_clinic_with_all_details(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post("/api/platform/clinics/", self.NEW, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        clinic = Clinic.objects.get(pk=r.data["id"])
        self.assertEqual((clinic.poc_name, clinic.poc_phone, clinic.state, clinic.pincode), ("Dr Meena Rao", "9812345678", "Rajasthan", "302001"))
        self.assertTrue(clinic.is_active)
        self.assertTrue(AuditEvent.objects.filter(action="clinic.create", object_id=str(clinic.id)).exists())

    def test_a_clinic_needs_a_name_city_and_a_point_of_contact(self):
        self.client.force_authenticate(self.admin)
        for field in ("name", "city", "poc_name", "poc_phone"):
            body = {k: v for k, v in self.NEW.items() if k != field}
            r = self.client.post("/api/platform/clinics/", body, format="json")
            self.assertEqual(r.status_code, 400, field)
            self.assertIn(field, r.data)
        self.assertEqual(self.client.post("/api/platform/clinics/", {**self.NEW, "poc_phone": "12345"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/platform/clinics/", {**self.NEW, "pincode": "12ab"}, format="json").status_code, 400)
        dup = self.client.post("/api/platform/clinics/", {**self.NEW, "name": "smile care", "city": "PUNE"}, format="json")
        self.assertEqual(dup.status_code, 400)
        self.assertIn("already exists", str(dup.data["name"]))

    def test_only_the_platform_admin_can_manage_the_directory(self):
        for who in (self.doctor, None):
            self.client.force_authenticate(who)
            self.assertIn(self.client.post("/api/platform/clinics/", self.NEW, format="json").status_code, (401, 403))
            self.assertIn(self.client.patch(f"/api/platform/clinics/{self.clinic.id}/", {"is_active": False}, format="json").status_code, (401, 403))
        self.assertFalse(Clinic.objects.filter(name="Bright Smiles").exists())
        self.assertTrue(Clinic.objects.get(pk=self.clinic.id).is_active)

    def test_clinics_cannot_be_deleted_only_turned_off(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(f"/api/platform/clinics/{self.clinic.id}/").status_code, 405)
        r = self.client.patch(f"/api/platform/clinics/{self.clinic.id}/", {"is_active": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/platform/clinics/?active=false").data["count"], 2)

    def test_sign_up_list_is_public_active_only_and_shows_no_contact_details(self):
        self.client.force_authenticate(None)
        r = self.client.get("/api/auth/clinics/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["name"] for c in r.data], ["Smile Care"])  # the turned-off clinic is hidden
        self.assertEqual(set(r.data[0]), {"id", "name", "city", "state"})
        self.assertNotIn("secret note", str(r.data))
        self.assertEqual(len(self.client.get("/api/auth/clinics/?search=pune").data), 1)
        self.assertEqual(len(self.client.get("/api/auth/clinics/?search=zzz").data), 0)

    def test_staff_never_see_the_point_of_contact_or_admin_notes(self):
        self.client.force_authenticate(self.doctor)
        r = self.client.get("/api/clinics/")
        text = str(r.data)
        self.assertNotIn("secret note", text)
        self.assertNotIn("poc_", text)
        self.assertNotIn("9000000010", text)
        # the name belongs to the directory, so a doctor cannot rename the clinic
        self.client.patch(f"/api/clinics/{self.clinic.id}/", {"name": "Renamed", "city": "Mumbai"}, format="json")
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.name, "Smile Care")

    def test_signup_then_approval_by_the_platform_admin(self):
        self.client.force_authenticate(None)
        body = {"name": "Dr New", "phone": "8000000005", "password": "Str0ng!pass", "clinic": self.clinic.id}
        self.assertEqual(self.client.post("/api/auth/register/", body, format="json").status_code, 201)
        new = User.objects.get(phone="8000000005")
        self.assertTrue(AuditEvent.objects.filter(action="signup.request", object_id=str(new.id)).exists())
        # a clinic that is turned off cannot be chosen
        self.assertEqual(self.client.post("/api/auth/register/", {**body, "phone": "8000000006", "clinic": self.hidden.id}, format="json").status_code, 400)

        # a doctor cannot use the platform admin's approve button
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self.client.post(f"/api/platform/users/{new.id}/approve/").status_code, 403)

        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/platform/overview/").data["pending_signups"], 1)
        pending = self.client.get("/api/platform/users/?pending=true")
        self.assertEqual([u["phone"] for u in pending.data["results"]], ["8000000005"])
        self.assertTrue(pending.data["results"][0]["signup_pending"])
        counts = {c["name"]: c["pending_count"] for c in self.client.get("/api/platform/clinics/").data["results"]}
        self.assertEqual(counts["Smile Care"], 1)
        self.assertEqual(self.client.post(f"/api/platform/users/{new.id}/approve/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/platform/users/{new.id}/approve/").status_code, 400)  # already done
        self.assertTrue(AuditEvent.objects.filter(action="user.approve", object_id=str(new.id)).exists())

        self.client.force_authenticate(None)
        login = self.client.post("/api/auth/login/", {"phone": "8000000005", "password": "Str0ng!pass"})
        self.assertEqual(login.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION="Token " + login.data["token"])
        self.assertEqual(self.client.get("/api/dashboard/").status_code, 200)

    def test_a_switched_off_account_is_not_shown_as_waiting(self):
        """Turning someone off deliberately must not look like a pending sign-up, and cannot be approved as one."""
        self.client.force_authenticate(self.admin)
        self.client.patch(f"/api/platform/users/{self.doctor.id}/", {"is_active": False}, format="json")
        r = self.client.get(f"/api/platform/users/{self.doctor.id}/")
        self.assertFalse(r.data["signup_pending"])
        self.assertEqual(self.client.post(f"/api/platform/users/{self.doctor.id}/approve/").status_code, 400)
