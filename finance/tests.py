from datetime import date, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role
from clinic.models import Patient
from core.models import Branch, PaymentMethod, Service

from .models import DoctorPayout, DoctorSettlement, Invoice, InvoiceItem, Payment, month_start
from .services import close_month, doctor_balance, month_figures


class FinanceRulesTest(TestCase):
    """The money rules: material taken off before the split, partial payments, monthly closing."""

    def setUp(self):
        self.branch = Branch.objects.create(code="T", name_ar="ط")
        self.doctor = User.objects.create(username="doc", role=Role.DOCTOR, commission_percent=40)
        self.patient = Patient.objects.create(full_name="مريض", gender="male", phone="0910000000")
        self.cash = PaymentMethod.objects.create(name_ar="نقداً", kind="cash")
        self.day = month_start(timezone.localdate()) - timedelta(days=10)  # last month

    def make_invoice(self, price=1000, material=200, discount=0):
        inv = Invoice.objects.create(branch=self.branch, patient=self.patient, doctor=self.doctor,
                                     date=self.day, doctor_percent=self.doctor.commission_percent,
                                     discount=Decimal(discount))
        InvoiceItem.objects.create(invoice=inv, description="زرعة", unit_price=Decimal(price),
                                   material_cost=Decimal(material))
        inv.recalc()
        return inv

    def pay(self, inv, amount, day=None):
        p = Payment.objects.create(invoice=inv, branch=self.branch, date=day or self.day,
                                   amount=Decimal(amount), method=self.cash)
        inv.recalc()
        return p

    def test_material_is_taken_off_before_the_split(self):
        inv = self.make_invoice(1000, 200)
        p = self.pay(inv, 1000)
        material, net, share = p.share_breakdown()
        self.assertEqual((material, net, share), (Decimal("200.00"), Decimal("800.00"), Decimal("320.00")))

    def test_partial_payments_earn_a_proportional_share(self):
        inv = self.make_invoice(1000, 200)
        first = self.pay(inv, 300)
        self.assertEqual(inv.state, "partial")
        self.assertEqual(inv.balance, Decimal("700.00"))
        self.assertEqual(first.share_breakdown()[2], Decimal("96.00"))  # 300 × 0.8 × 40%
        self.pay(inv, 700)
        self.assertEqual(inv.state, "paid")
        fig = month_figures(self.doctor, self.day)
        self.assertEqual(fig.collected, Decimal("1000"))
        self.assertEqual(fig.doctor_share, Decimal("320.00"))

    def test_discount_lowers_total_and_share(self):
        inv = self.make_invoice(1000, 200, discount=100)
        self.assertEqual(inv.total, Decimal("900.00"))
        p = self.pay(inv, 900)
        self.assertEqual(p.share_breakdown()[2], Decimal("280.00"))  # (900 − 200) × 40%

    def test_closing_a_month_freezes_figures_and_balance(self):
        inv = self.make_invoice(1000, 200)
        self.pay(inv, 500)
        s = close_month(self.doctor, self.day, None, adjustment=Decimal("-50"), note="سلفة")
        self.assertEqual(s.doctor_share, Decimal("160.00"))
        self.assertEqual(s.total_due, Decimal("110.00"))
        self.assertTrue(inv.is_locked)
        # changing the doctor's % later doesn't change the closed month
        self.doctor.commission_percent = 10
        self.doctor.save()
        self.assertEqual(month_figures(self.doctor, self.day).doctor_share, Decimal("160.00"))
        DoctorPayout.objects.create(doctor=self.doctor, amount=Decimal("100"), method=self.cash)
        self.assertEqual(doctor_balance(self.doctor), (Decimal("110.00"), Decimal("100.00"), Decimal("10.00")))
        with self.assertRaises(ValueError):
            close_month(self.doctor, self.day, None)

    def test_a_payment_in_a_later_month_belongs_to_that_month(self):
        inv = self.make_invoice(1000, 0)
        self.pay(inv, 400)
        close_month(self.doctor, self.day, None)
        later = timezone.localdate()
        self.pay(inv, 600, day=later)
        self.assertEqual(month_figures(self.doctor, later).doctor_share, Decimal("240.00"))


class FinancePagesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", force=True, verbosity=0)

    def login(self, username):
        self.client.force_login(User.objects.get(username=username))

    def test_pages_open_for_accountant(self):
        self.login("admin")
        inv = Invoice.objects.first()
        doctor = User.objects.get(username="dr.omar")
        for url in [reverse("invoice_list"), reverse("invoice_create"), reverse("invoice_detail", args=[inv.pk]),
                    reverse("invoice_print", args=[inv.pk]), reverse("invoice_edit", args=[inv.pk]),
                    reverse("doctor_accounts"), reverse("doctor_month", args=[doctor.pk]),
                    reverse("doctor_statement", args=[doctor.pk]), reverse("payout_list"),
                    reverse("payout_create"), reverse("expense_list"), reverse("expense_create"),
                    reverse("cashbox"), reverse("finance_monthly"), reverse("finance_monthly") + "?scope=all",
                    reverse("clinic_settings")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_reception_writes_invoice_with_first_payment(self):
        self.login("reception")
        patient = Patient.objects.first()
        doctor = User.objects.get(username="dr.sara")
        svc = Service.objects.get(code="DEN-004")
        card = PaymentMethod.objects.get(kind="card")
        data = {
            "patient": patient.pk, "doctor": doctor.pk, "date": timezone.localdate().isoformat(), "discount": "0",
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
            "items-0-service": svc.pk, "items-0-quantity": "2", "items-0-unit_price": "100",
            "items-0-material_cost": "30", "items-0-tooth": "16",
            "pay-amount": "50", "pay-method": card.pk, "pay-reference": "",
        }
        resp = self.client.post(reverse("invoice_create"), data)
        self.assertEqual(resp.status_code, 200)  # card needs a reference
        data["pay-reference"] = "POS-1"
        resp = self.client.post(reverse("invoice_create"), data)
        inv = Invoice.objects.latest("id")
        self.assertRedirects(resp, reverse("invoice_detail", args=[inv.pk]))
        self.assertEqual((inv.total, inv.paid, inv.material_cost), (Decimal("200.00"), Decimal("50.00"), Decimal("30.00")))
        self.assertEqual(inv.doctor_percent, Decimal("40.00"))
        # more than the balance is refused
        resp = self.client.post(reverse("invoice_pay", args=[inv.pk]), {
            "date": timezone.localdate().isoformat(), "amount": "500", "method": card.pk, "reference": "x"})
        self.assertEqual(resp.status_code, 200)
        inv.refresh_from_db()
        self.assertEqual(inv.paid, Decimal("50.00"))

    def test_reception_cannot_see_doctor_accounts_or_remove_payments(self):
        self.login("reception")
        self.assertEqual(self.client.get(reverse("doctor_accounts")).status_code, 403)
        p = Payment.objects.filter(settlement__isnull=True).first()
        self.assertEqual(self.client.post(reverse("payment_void", args=[p.pk])).status_code, 403)

    def test_doctor_sees_only_own_account(self):
        self.login("dr.sara")
        resp = self.client.get(reverse("doctor_accounts"))
        self.assertContains(resp, "سارة")
        self.assertNotContains(resp, "عمر حسن")
        omar = User.objects.get(username="dr.omar")
        self.assertEqual(self.client.get(reverse("doctor_statement", args=[omar.pk])).status_code, 404)

    def test_closed_month_blocks_payment_and_edit(self):
        self.login("admin")
        omar = User.objects.get(username="dr.omar")
        s = DoctorSettlement.objects.get(doctor=omar)
        p = s.payments.first()
        resp = self.client.post(reverse("payment_void", args=[p.pk]))
        self.assertTrue(Payment.objects.filter(pk=p.pk).exists())
        resp = self.client.get(reverse("invoice_edit", args=[p.invoice_id]))
        self.assertRedirects(resp, reverse("invoice_detail", args=[p.invoice_id]))
        # admin can reopen
        self.client.post(reverse("doctor_month", args=[omar.pk]), {"month": s.period.strftime("%Y-%m"), "do": "reopen"})
        self.assertFalse(DoctorSettlement.objects.filter(pk=s.pk).exists())
        p.refresh_from_db()
        self.assertIsNone(p.settlement_id)

    def test_accountant_closes_month(self):
        self.login("accountant")
        sara = User.objects.get(username="dr.sara")
        today = timezone.localdate()
        resp = self.client.post(reverse("doctor_month", args=[sara.pk]),
                                {"month": today.strftime("%Y-%m"), "adjustment": "25", "adjustment_note": "مكافأة"})
        self.assertEqual(resp.status_code, 302)
        s = DoctorSettlement.objects.get(doctor=sara, period=month_start(today))
        self.assertEqual(s.total_due, s.doctor_share + Decimal("25"))
        self.assertEqual(self.client.post(reverse("doctor_month", args=[sara.pk]),
                                          {"month": today.strftime("%Y-%m"), "do": "reopen"}).status_code, 403)

    def test_invoice_from_appointment_prefills(self):
        self.login("reception")
        from clinic.models import Appointment

        appt = Appointment.objects.filter(status="completed", service__isnull=False).first()
        resp = self.client.get(reverse("invoice_create") + f"?appointment={appt.pk}")
        self.assertContains(resp, appt.patient.full_name)
        self.assertContains(resp, f'value="{appt.service.pk}" selected')
