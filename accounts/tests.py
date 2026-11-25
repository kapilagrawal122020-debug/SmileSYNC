"""Clinic as an organisation: the two kinds of admin, who can do what inside a clinic, and the public marketplace."""
from django.core.cache import cache
from rest_framework.test import APITestCase

from accounts.models import AuthToken, User
from audit.models import AuditEvent
from clinic.models import Clinic
from patients.models import Patient


class ClinicOrganisationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.super = User.objects.create_superuser("9000000099", "Adm1n!pass", name="Root")
        cls.clinic = Clinic.objects.create(name="Smile Care", city="Pune", state="Maharashtra", about="Family dentistry", timings="Mon to Sat 10 to 7", poc_name="Dr A", poc_phone="9000000010", admin_notes="secret")
        cls.other = Clinic.objects.create(name="Other Clinic", city="Delhi")
        mk = lambda phone, name, role, clinic, **kw: User.objects.create_user(phone, "pw12345!", name=name, role=role, clinic=clinic, **kw)
        cls.admin = mk("9000000011", "Admin A", "clinic_admin", cls.clinic, designation="BDS, MDS", speciality="Endodontics")
        cls.doctor = mk("9000000012", "Dr Doc", "doctor", cls.clinic, designation="BDS")
        cls.consultant = mk("9000000013", "Dr Con", "consultant", cls.clinic)
        cls.assistant = mk("9000000014", "Asha", "assistant", cls.clinic)
        cls.other_admin = mk("9000000021", "Admin B", "clinic_admin", cls.other)
        cls.other_assistant = mk("9000000022", "Bina", "assistant", cls.other)
        cls.patient = Patient.objects.create(clinic=cls.clinic, name="Ravi", phone="9111111111", consent_data=True, private_notes="asks for discount")
        cls.other_patient = Patient.objects.create(clinic=cls.other, name="Sita", phone="9222222222", consent_data=True)

    def setUp(self):
        cache.clear()

    def login(self, user):
        self.client.force_authenticate(user)

    # ---- the clinic admin runs one clinic's team --------------------------------------
    def test_clinic_admin_sees_and_manages_only_their_own_team(self):
        self.login(self.admin)
        phones = {u["phone"] for u in self.client.get("/api/users/").data["results"]}
        self.assertEqual(phones, {"9000000011", "9000000012", "9000000013", "9000000014"})
        r = self.client.post("/api/users/", {"phone": "9000000031", "name": "New Doc", "role": "doctor", "password": "pw12345!"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(User.objects.get(phone="9000000031").clinic, self.clinic)  # always their own clinic
        r = self.client.post("/api/users/", {"phone": "9000000032", "name": "Co Admin", "role": "clinic_admin", "password": "pw12345!"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        for role in ("super_admin", "patient"):
            self.assertEqual(self.client.post("/api/users/", {"phone": "9000000033", "name": "X", "role": role, "password": "pw12345!"}, format="json").status_code, 400, role)
        # another clinic's people do not exist for them
        self.assertEqual(self.client.patch(f"/api/users/{self.other_assistant.id}/", {"is_active": False}, format="json").status_code, 404)
        self.assertEqual(self.client.post(f"/api/users/{self.other_assistant.id}/approve/").status_code, 404)
        self.assertTrue(User.objects.get(pk=self.other_assistant.id).is_active)

    def test_other_staff_can_see_the_team_but_not_change_it(self):
        for who in (self.doctor, self.consultant, self.assistant):
            self.login(who)
            self.assertEqual(self.client.get("/api/users/").status_code, 200, who.role)
            self.assertEqual(self.client.post("/api/users/", {"phone": "9000000041", "name": "X", "role": "assistant", "password": "pw12345!"}, format="json").status_code, 403, who.role)
            self.assertEqual(self.client.patch(f"/api/users/{self.assistant.id}/", {"is_active": False}, format="json").status_code, 403, who.role)
            self.assertEqual(self.client.delete(f"/api/users/{self.assistant.id}/").status_code, 403, who.role)
        self.assertTrue(User.objects.get(pk=self.assistant.id).is_active)

    def test_clinic_admin_approves_sign_ups_for_their_own_clinic_only(self):
        self.client.force_authenticate(None)
        mine = {"role": "assistant", "name": "Joiner", "phone": "8000000051", "password": "Str0ng!pass", "clinic": self.clinic.id}
        theirs = {**mine, "phone": "8000000052", "clinic": self.other.id}
        self.assertEqual(self.client.post("/api/auth/register/", mine, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/auth/register/", theirs, format="json").status_code, 201)
        joiner, outsider = User.objects.get(phone="8000000051"), User.objects.get(phone="8000000052")
        self.assertTrue(joiner.signup_pending and not joiner.is_active)

        self.login(self.doctor)
        self.assertEqual(self.client.post(f"/api/users/{joiner.id}/approve/").status_code, 403)  # only the clinic admin
        self.login(self.admin)
        listed = {u["phone"]: u for u in self.client.get("/api/users/").data["results"]}
        self.assertTrue(listed["8000000051"]["signup_pending"])
        self.assertNotIn("8000000052", listed)
        self.assertEqual(self.client.post(f"/api/users/{outsider.id}/approve/").status_code, 404)  # not their clinic
        self.assertEqual(self.client.post(f"/api/users/{joiner.id}/approve/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/users/{joiner.id}/approve/").status_code, 400)  # already done
        self.assertTrue(AuditEvent.objects.filter(action="user.approve", object_id=str(joiner.id), clinic=self.clinic).exists())
        self.assertFalse(User.objects.get(pk=outsider.id).is_active)

        self.client.force_authenticate(None)
        r = self.client.post("/api/auth/login/", {"phone": "8000000051", "password": "Str0ng!pass", "clinic": self.clinic.id})
        self.assertEqual(r.status_code, 200)

    def test_the_dashboard_counts_people_waiting_for_approval(self):
        self.login(self.admin)
        self.assertEqual(self.client.get("/api/dashboard/").data["pending_signups"], 0)
        self.client.force_authenticate(None)
        self.client.post("/api/auth/register/", {"role": "assistant", "name": "J", "phone": "8000000061", "password": "Str0ng!pass", "clinic": self.clinic.id}, format="json")
        self.client.post("/api/auth/register/", {"role": "assistant", "name": "K", "phone": "8000000062", "password": "Str0ng!pass", "clinic": self.other.id}, format="json")
        self.login(self.admin)
        self.assertEqual(self.client.get("/api/dashboard/").data["pending_signups"], 1)  # only their own clinic's

    def test_a_clinic_always_keeps_one_admin(self):
        self.login(self.other_admin)
        me = self.other_admin.id
        self.assertEqual(self.client.patch(f"/api/users/{me}/", {"is_active": False}, format="json").status_code, 400)  # not themselves
        self.assertEqual(self.client.patch(f"/api/users/{me}/", {"role": "doctor"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/users/{me}/").status_code, 400)
        self.assertTrue(User.objects.filter(pk=me, role="clinic_admin", is_active=True).exists())
        # with a second admin, the first can still not be removed by themselves, but the second can step in
        co = self.client.post("/api/users/", {"phone": "9000000061", "name": "Co", "role": "clinic_admin", "password": "pw12345!"}, format="json").data["id"]
        self.login(User.objects.get(pk=co))
        self.assertEqual(self.client.patch(f"/api/users/{me}/", {"role": "doctor"}, format="json").status_code, 200)
        # now Co is the only admin and cannot be demoted by anyone
        self.login(self.super)
        self.assertEqual(self.client.get("/api/platform/users/?role=clinic_admin&clinic=%d" % self.other.id).data["count"], 1)
        self.login(User.objects.get(pk=co))
        self.assertEqual(self.client.patch(f"/api/users/{co}/", {"role": "assistant"}, format="json").status_code, 400)

    def test_turning_off_or_deleting_a_person_signs_them_out(self):
        token = AuthToken.objects.create(user=self.assistant, clinic=self.clinic)
        self.login(self.admin)
        self.assertEqual(self.client.patch(f"/api/users/{self.assistant.id}/", {"is_active": False}, format="json").status_code, 200)
        self.assertFalse(AuthToken.objects.filter(key=token.key).exists())
        self.assertTrue(AuditEvent.objects.filter(action="user.update", object_id=str(self.assistant.id)).exists())

    # ---- what each role can do inside the clinic ---------------------------------------
    def test_the_clinic_admin_can_do_what_a_doctor_can_and_more(self):
        self.login(self.admin)
        r = self.client.post("/api/treatments/", {"patient": self.patient.id, "procedure": "Filling", "tooth": "26"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.id}/").data["private_notes"], "asks for discount")
        self.assertEqual(self.client.get("/api/dashboard/").status_code, 200)  # management view
        self.assertEqual(self.client.get("/api/documents/").status_code, 200)

    def test_doctors_treat_but_do_not_manage(self):
        self.login(self.doctor)
        self.assertEqual(self.client.post("/api/treatments/", {"patient": self.patient.id, "procedure": "Scaling"}, format="json").status_code, 201)
        self.assertEqual(self.client.get(f"/api/patients/{self.patient.id}/").data["private_notes"], "asks for discount")
        for url in ("/api/dashboard/", "/api/documents/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.patch(f"/api/clinics/{self.clinic.id}/", {"about": "Hacked"}, format="json").status_code, 403)
        self.assertEqual(self.client.post("/api/chairs/", {"name": "Chair 9"}, format="json").status_code, 403)

    def test_assistants_do_not_see_private_notes_or_manage(self):
        self.login(self.assistant)
        self.assertNotIn("private_notes", self.client.get(f"/api/patients/{self.patient.id}/").data)
        self.assertEqual(self.client.get("/api/dashboard/").status_code, 403)
        self.assertEqual(self.client.post("/api/users/", {"phone": "9000000071", "name": "X", "role": "assistant", "password": "pw12345!"}, format="json").status_code, 403)

    def test_clinic_admin_edits_the_public_profile_but_not_the_name_or_web_address(self):
        self.login(self.admin)
        r = self.client.patch(f"/api/clinics/{self.clinic.id}/", {"about": "Painless care", "timings": "Daily 9 to 5", "phone": "02012345678", "name": "Renamed", "slug": "renamed"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.clinic.refresh_from_db()
        self.assertEqual((self.clinic.about, self.clinic.timings), ("Painless care", "Daily 9 to 5"))
        self.assertEqual((self.clinic.name, self.clinic.slug), ("Smile Care", "smile-care-pune"))
        self.assertEqual(self.client.patch(f"/api/clinics/{self.other.id}/", {"about": "Mine now"}, format="json").status_code, 404)

    def test_clinic_admin_never_reaches_another_clinic_or_the_platform(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(f"/api/patients/{self.other_patient.id}/").status_code, 404)
        self.assertEqual(self.client.get("/api/patients/").data["count"], 1)
        for url in ("/api/platform/overview/", "/api/platform/clinics/", "/api/platform/users/", "/api/platform/audit/", "/api/platform/lists/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post("/api/platform/clinics/", {"name": "My Own", "city": "X", "poc_name": "Me", "poc_phone": "9876543210"}, format="json").status_code, 403)
        self.assertEqual(self.client.post(f"/api/platform/clinics/{self.clinic.id}/admins/", {"name": "Z", "phone": "9000000081", "password": "Adm1n!pass2"}, format="json").status_code, 403)
        self.assertEqual(self.client.post("/api/platform/admins/", {"name": "Z", "phone": "9000000082", "password": "Adm1n!pass2"}, format="json").status_code, 403)

    # ---- the super admin creates clinics and their first admin --------------------------
    NEW_CLINIC = {"name": "Lotus Dental", "city": "Kolkata", "poc_name": "Dr Lotus", "poc_phone": "9812345678"}

    def test_super_admin_creates_a_clinic_with_its_first_admin(self):
        self.login(self.super)
        body = {**self.NEW_CLINIC, "admin_name": "Lotus Admin", "admin_phone": "9000000091", "admin_password": "Str0ng!pass"}
        r = self.client.post("/api/platform/clinics/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["slug"], "lotus-dental-kolkata")
        self.assertEqual([a["phone"] for a in r.data["admins"]], ["9000000091"])
        self.assertNotIn("admin_password", r.data)
        admin = User.objects.get(phone="9000000091")
        self.assertEqual((admin.role, admin.clinic_id, admin.is_active), ("clinic_admin", r.data["id"], True))
        self.client.force_authenticate(None)
        ok = self.client.post("/api/auth/login/", {"phone": "9000000091", "password": "Str0ng!pass", "clinic": r.data["id"]})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.data["user"]["role"], "clinic_admin")

    def test_the_first_admin_is_all_or_nothing_and_checked(self):
        self.login(self.super)
        post = lambda **kw: self.client.post("/api/platform/clinics/", {**self.NEW_CLINIC, **kw}, format="json")
        self.assertEqual(post(admin_name="Only Name").status_code, 400)
        self.assertEqual(post(admin_name="A", admin_phone="9000000011", admin_password="Str0ng!pass").status_code, 400)  # phone taken
        self.assertEqual(post(admin_name="A", admin_phone="9000000092", admin_password="12345").status_code, 400)  # weak password
        self.assertFalse(Clinic.objects.filter(name="Lotus Dental").exists())  # nothing half-created
        self.assertEqual(post().status_code, 201)  # a clinic without an admin is allowed: add one later
        clinic = Clinic.objects.get(name="Lotus Dental")
        self.assertEqual(self.client.get("/api/platform/overview/").data["clinics_without_admin"], 1)
        r = self.client.post(f"/api/platform/clinics/{clinic.id}/admins/", {"name": "Later", "phone": "9000000093", "password": "Str0ng!pass"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(User.objects.get(phone="9000000093").role, "clinic_admin")
        self.assertEqual(self.client.get("/api/platform/overview/").data["clinics_without_admin"], 0)
        self.assertEqual(self.client.patch(f"/api/platform/clinics/{clinic.id}/", {"admin_name": "X", "admin_phone": "9000000094", "admin_password": "Str0ng!pass"}, format="json").status_code, 400)
        self.assertTrue(AuditEvent.objects.filter(action="clinic.admin.create").exists())

    def test_a_clinic_admin_login_needs_the_right_clinic(self):
        for clinic, ok in ((self.clinic, 200), (self.other, 401)):
            r = self.client.post("/api/auth/login/", {"phone": "9000000011", "password": "pw12345!", "clinic": clinic.id})
            self.assertEqual(r.status_code, ok)
        # the super admin needs no clinic, and their session has none
        r = self.client.post("/api/auth/login/", {"phone": "9000000099", "password": "Adm1n!pass"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["user"]["clinic_id"], None)
        self.assertIsNone(AuthToken.objects.get(key=r.data["token"]).clinic_id)

    # ---- the marketplace ---------------------------------------------------------------
    def test_marketplace_is_public_and_filterable(self):
        Clinic.objects.create(name="Hidden Clinic", city="Pune", is_active=False)
        r = self.client.get("/api/public/clinics/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["name"] for c in r.data], ["Other Clinic", "Smile Care"])
        self.assertEqual([c["name"] for c in self.client.get("/api/public/clinics/?search=smi").data], ["Smile Care"])
        self.assertEqual([c["name"] for c in self.client.get("/api/public/clinics/?city=delhi").data], ["Other Clinic"])
        self.assertEqual([c["name"] for c in self.client.get("/api/public/clinics/?state=maharashtra").data], ["Smile Care"])
        self.assertEqual(self.client.get("/api/auth/clinics/").data, r.data)  # the older address gives the same list

    def test_a_clinic_page_shows_only_what_the_clinic_publishes(self):
        r = self.client.get("/api/public/clinics/smile-care-pune/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["about"], r.data["timings"]), ("Family dentistry", "Mon to Sat 10 to 7"))
        text = str(r.data)
        for private in ("secret", "9000000010", "Dr A", "poc_", "admin_notes", "registration_number", "gst"):
            self.assertNotIn(private, text)
        # doctors, consultants and an admin who is also the doctor (has a designation) are listed; assistants are not
        names = sorted(d["name"] for d in r.data["doctors"])
        self.assertEqual(names, ["Admin A", "Dr Con", "Dr Doc"])
        self.assertNotIn("Asha", names)
        self.assertEqual(set(r.data["doctors"][0]), {"name", "designation", "speciality"})
        self.assertEqual(self.client.get("/api/public/clinics/no-such-clinic/").status_code, 404)
        Clinic.objects.filter(pk=self.other.pk).update(is_active=False)
        self.assertEqual(self.client.get(f"/api/public/clinics/{self.other.slug}/").status_code, 404)  # turned off: not listed

    def test_web_addresses_are_unique_and_stable(self):
        a = Clinic.objects.create(name="Same Name", city="Pune")
        b = Clinic.objects.create(name="Same Name", city="Pune")
        self.assertEqual((a.slug, b.slug), ("same-name-pune", "same-name-pune-2"))
        a.name = "Renamed"
        a.save()
        self.assertEqual(Clinic.objects.get(pk=a.pk).slug, "same-name-pune")  # links keep working after a rename
