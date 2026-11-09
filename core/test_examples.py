from decimal import Decimal

from django.apps import apps
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from accounts.roles import Role
from clinic.models import Patient
from finance.models import Invoice, InvoiceItem

from .models import Branch, ExampleItem, PaymentMethod

COUNTED = ["core.branch", "core.service", "core.paymentmethod", "accounts.user", "clinic.patient",
           "clinic.appointment", "finance.invoice", "finance.payment", "finance.doctorsettlement",
           "finance.doctorpayout", "finance.expense", "records.clinicalnote", "records.patientphoto"]


def snapshot():
    return {label: apps.get_model(label).objects.count() for label in COUNTED}


class ExamplesTest(TestCase):
    """The live site starts empty: only the admin account exists."""

    def setUp(self):
        self.admin = User.objects.create_superuser("owner", password="x-Strong-pass-123", role=Role.ADMIN)
        self.client.force_login(self.admin)

    def test_load_on_empty_site_then_erase_leaves_nothing(self):
        before = snapshot()
        resp = self.client.post(reverse("examples"), {"do": "load"})
        self.assertRedirects(resp, reverse("examples"))
        self.assertTrue(ExampleItem.objects.exists())
        self.assertEqual(Patient.objects.count(), 6)
        self.assertTrue(Branch.objects.filter(code="EX").exists())
        # every page works with the examples in place
        inv = Invoice.objects.first()
        patient = Patient.objects.get(full_name__startswith="فاطمة")
        doctor = User.objects.get(username="example.surg")
        self.client.post(reverse("switch_branch"), {"branch": Branch.objects.get(code="EX").pk})
        for url in [reverse("dashboard"), reverse("appointment_list"), reverse("appointment_calendar"),
                    reverse("queue_screen"), reverse("patient_list"), reverse("patient_detail", args=[patient.pk]),
                    reverse("invoice_list"), reverse("invoice_detail", args=[inv.pk]), reverse("cashbox"),
                    reverse("doctor_accounts"), reverse("doctor_statement", args=[doctor.pk]),
                    reverse("finance_monthly"), reverse("clinical_home"),
                    reverse("clinical_patient", args=[patient.pk]) + "?tab=teeth", reverse("examples")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(User.objects.get(username="example.dent").has_usable_password())
        # loading twice is refused
        n = ExampleItem.objects.count()
        self.client.post(reverse("examples"), {"do": "load"})
        self.assertEqual(ExampleItem.objects.count(), n)

        self.client.post(reverse("examples"), {"do": "erase"})
        self.assertFalse(ExampleItem.objects.exists())
        self.assertEqual(snapshot(), before)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_real_data_is_never_erased(self):
        branch = Branch.objects.create(code="TRP", name_ar="طرابلس")
        cash = PaymentMethod.objects.create(kind="cash", name_ar="نقداً")
        real = Patient.objects.create(full_name="مريض حقيقي", gender="male", phone="0920000000")
        self.client.post(reverse("examples"), {"do": "load"})
        self.assertFalse(Branch.objects.filter(code="EX").exists())  # uses the clinic's own branch
        # staff wrote a real invoice for an example patient
        example_patient = Patient.objects.filter(full_name__endswith="(مثال)").first()
        doctor = User.objects.create(username="realdoc", role=Role.DOCTOR)
        inv = Invoice.objects.create(branch=branch, patient=example_patient, doctor=doctor)
        InvoiceItem.objects.create(invoice=inv, description="x", unit_price=Decimal(10))
        self.client.post(reverse("examples"), {"do": "erase"})
        self.assertTrue(Patient.objects.filter(pk=real.pk).exists())
        self.assertTrue(PaymentMethod.objects.filter(pk=cash.pk).exists())
        self.assertTrue(Patient.objects.filter(pk=example_patient.pk).exists())  # kept: real invoice uses it
        self.assertEqual(ExampleItem.objects.count(), 1)

    def test_only_admin(self):
        manager = User.objects.create(username="m", role=Role.MANAGER)
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("examples")).status_code, 403)
        self.assertEqual(self.client.post(reverse("examples"), {"do": "load"}).status_code, 403)
