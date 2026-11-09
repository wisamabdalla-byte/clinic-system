import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from clinic.models import Patient
from core.models import ClinicProfile, Service

from .models import ClinicalNote, PatientPhoto, Prescription, ToothRecord, TreatmentPlan

# 1×1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class RecordsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", force=True, verbosity=0)

    def setUp(self):
        self.patient = Patient.objects.first()
        self.sara = User.objects.get(username="dr.sara")

    def login(self, username):
        self.client.force_login(User.objects.get(username=username))

    def test_clinical_pages_open(self):
        self.login("dr.sara")
        for tab in ["notes", "teeth", "rx", "plans", "photos"]:
            url = reverse("clinical_patient", args=[self.patient.pk]) + f"?tab={tab}"
            with self.subTest(tab=tab):
                self.assertEqual(self.client.get(url).status_code, 200)
        for url in [reverse("clinical_home"), reverse("note_create", args=[self.patient.pk]),
                    reverse("rx_create", args=[self.patient.pk]), reverse("plan_create", args=[self.patient.pk]),
                    reverse("photo_create", args=[self.patient.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_reception_and_accountant_cannot_read_clinical_file(self):
        for who in ("reception", "accountant"):
            self.login(who)
            self.assertEqual(self.client.get(reverse("clinical_patient", args=[self.patient.pk])).status_code, 403)

    def test_doctor_writes_note_and_sets_tooth(self):
        self.login("dr.sara")
        resp = self.client.post(reverse("note_create", args=[self.patient.pk]), {
            "doctor": self.sara.pk, "date": timezone.localdate().isoformat(), "diagnosis": "تسوس"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ClinicalNote.objects.filter(patient=self.patient, diagnosis="تسوس").exists())
        self.client.post(reverse("tooth_save", args=[self.patient.pk]), {"tooth": 26, "condition": "implant"})
        self.assertEqual(ToothRecord.objects.get(patient=self.patient, tooth=26).condition, "implant")
        resp = self.client.post(reverse("tooth_save", args=[self.patient.pk]), {"tooth": 99, "condition": "implant"})
        self.assertFalse(ToothRecord.objects.filter(tooth=99).exists())

    def test_nurse_cannot_prescribe(self):
        self.login("nurse")
        self.assertEqual(self.client.get(reverse("rx_create", args=[self.patient.pk])).status_code, 403)

    def test_prescription_and_plan(self):
        self.login("dr.sara")
        fs = {"items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0",
              "items-MAX_NUM_FORMS": "1000"}
        resp = self.client.post(reverse("rx_create", args=[self.patient.pk]), {
            **fs, "doctor": self.sara.pk, "date": timezone.localdate().isoformat(),
            "items-0-drug": "Amoxicillin 500mg", "items-0-dose": "1", "items-0-frequency": "3/day"})
        rx = Prescription.objects.get(patient=self.patient)
        self.assertRedirects(resp, reverse("rx_print", args=[rx.pk]))
        self.assertContains(self.client.get(reverse("rx_print", args=[rx.pk])), "Amoxicillin")
        svc = Service.objects.get(code="DEN-005")
        self.client.post(reverse("plan_create", args=[self.patient.pk]), {
            **fs, "title": "علاج عصب", "doctor": self.sara.pk, "status": "accepted",
            "items-0-service": svc.pk, "items-0-quantity": "1", "items-0-price": "350", "items-0-tooth": "36",
            "items-0-is_done": "on"})
        plan = TreatmentPlan.objects.get(patient=self.patient)
        self.assertEqual(plan.done_count, 1)
        self.login("reception")
        resp = self.client.get(reverse("invoice_create") + f"?plan={plan.pk}")
        self.assertContains(resp, 'value="36"')

    def test_photo_upload_is_private(self):
        self.login("dr.lina")
        resp = self.client.post(reverse("photo_create", args=[self.patient.pk]), {
            "image": SimpleUploadedFile("a.png", PNG, content_type="image/png"),
            "taken_on": timezone.localdate().isoformat(), "area": "الوجه", "stage": "before"})
        self.assertEqual(resp.status_code, 302)
        photo = PatientPhoto.objects.get()
        self.assertEqual(self.client.get(reverse("photo_image", args=[photo.pk])).content, PNG)
        # not an image
        resp = self.client.post(reverse("photo_create", args=[self.patient.pk]), {
            "image": SimpleUploadedFile("a.png", b"<script>", content_type="image/png"),
            "taken_on": timezone.localdate().isoformat(), "area": "x", "stage": "before"})
        self.assertEqual(resp.status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("photo_image", args=[photo.pk])).status_code, 302)
        self.login("reception")
        self.assertEqual(self.client.get(reverse("photo_image", args=[photo.pk])).status_code, 403)


class BrandingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", force=True, verbosity=0)

    def test_admin_sets_name_logo_and_colour(self):
        self.client.force_login(User.objects.get(username="admin"))
        resp = self.client.post(reverse("clinic_settings"), {
            "name_ar": "مركز النخبة", "name_en": "Elite Center", "primary_color": "#1d4ed8",
            "logo_file": SimpleUploadedFile("l.png", PNG, content_type="image/png")})
        self.assertEqual(resp.status_code, 302)
        profile = ClinicProfile.objects.get()
        self.assertTrue(profile.logo.startswith("data:image/png;base64,"))
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "مركز النخبة")
        self.assertContains(page, "#1d4ed8")

    def test_only_admin(self):
        self.client.force_login(User.objects.get(username="manager"))
        self.assertEqual(self.client.get(reverse("clinic_settings")).status_code, 403)
