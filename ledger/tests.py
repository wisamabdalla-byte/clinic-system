"""Accounting integrity tests: every operation must leave the books balanced and correct."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role, has_perm
from clinic.models import Patient
from core.models import Branch, PaymentMethod, Service, ServiceCategory
from finance.models import DoctorPayout, Expense, Invoice, InvoiceItem, Payment, month_start
from finance.services import close_month, reopen_month

from . import engine, posting
from .balances import account_balance, party_balance, treasury_balance, trial_balance_check
from .engine import cr, dr
from .models import (
    Account, EntryKind, EntryStatus, ExpenseCategory, FiscalPeriod, JournalEntry, LedgerError, LedgerSettings, Party,
    PermissionOverride, ProtectedEntryError, Treasury,
)

D = Decimal


def acc(code):
    return Account.objects.get(code=code)


class LedgerTestCase(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="T", name_ar="ط")
        self.admin = User.objects.create_superuser("boss", password="x-Strong-pass-123", role=Role.ADMIN)
        self.acct = User.objects.create(username="acc", role=Role.ACCOUNTANT)
        self.manager = User.objects.create(username="mgr", role=Role.MANAGER)
        self.doctor = User.objects.create(username="doc", role=Role.DOCTOR, commission_percent=40,
                                          department="dental")
        self.patient = Patient.objects.create(full_name="مريض", gender="male", phone="0910000000")
        self.cash_m = PaymentMethod.objects.create(name_ar="نقداً", kind="cash")
        self.card_m = PaymentMethod.objects.create(name_ar="بطاقة", kind="card")
        cat = ServiceCategory.objects.create(name_ar="ح", department="dental")
        self.service = Service.objects.create(code="S1", name_ar="حشو", department="dental", category=cat,
                                              price=D(100))
        self.day = timezone.localdate()
        self.cash = Treasury.objects.get(account__code="1111")
        self.bank = Treasury.objects.get(account__code="1121")

    def assertBalanced(self):
        d, c = trial_balance_check()
        self.assertEqual(d, c)
        for e in JournalEntry.objects.all():
            ed, ec = e.totals()
            self.assertEqual(ed, ec, e.number)

    def bal(self, code, **kw):
        return account_balance(acc(code), **kw)


class EngineTest(LedgerTestCase):
    def test_unbalanced_entry_is_refused(self):
        with self.assertRaises(LedgerError):
            engine.create_entry(date=self.day, description="x",
                                lines=[dr(acc("1111"), 100), cr(acc("411"), 90)])
        self.assertFalse(JournalEntry.objects.exists())

    def test_group_and_control_rules(self):
        with self.assertRaises(LedgerError):  # posting to a heading
            engine.create_entry(date=self.day, description="x", lines=[dr(acc("111"), 5), cr(acc("411"), 5)])
        with self.assertRaises(LedgerError):  # receivable without a party
            engine.create_entry(date=self.day, description="x", lines=[dr(acc("1131"), 5), cr(acc("411"), 5)])

    def test_numbers_are_sequential(self):
        a = engine.create_entry(date=self.day, description="a", lines=[dr(acc("1111"), 5), cr(acc("411"), 5)])
        b = engine.create_entry(date=self.day, description="b", lines=[dr(acc("1111"), 5), cr(acc("411"), 5)])
        self.assertEqual(int(b.number.rsplit("-", 1)[1]), int(a.number.rsplit("-", 1)[1]) + 1)
        self.assertTrue(a.number.startswith(f"JV-{self.day.year}-"))

    def test_posted_entry_cannot_be_deleted_but_can_be_reversed(self):
        e = engine.create_entry(date=self.day, description="a", lines=[dr(acc("1111"), 50), cr(acc("411"), 50)])
        with self.assertRaises(ProtectedEntryError):
            e.delete()
        rev = engine.reverse(e, self.admin)
        e.refresh_from_db()
        self.assertTrue(e.is_reversed)
        self.assertEqual(rev.reversal_of, e)
        self.assertEqual(self.bal("1111"), 0)
        with self.assertRaises(LedgerError):
            engine.reverse(e, self.admin)
        self.assertBalanced()

    def test_manual_workflow_review_approve_post(self):
        s = LedgerSettings.get()
        s.require_review = True
        s.save()
        e = engine.create_entry(date=self.day, description="m", kind=EntryKind.MANUAL, status=EntryStatus.DRAFT,
                                user=self.acct, lines=[dr(acc("522"), 300), cr(acc("1111"), 300)])
        self.assertEqual(self.bal("522"), 0)  # drafts never count
        with self.assertRaises(LedgerError):
            engine.post(e, self.manager)  # not approved yet
        with self.assertRaises(LedgerError):
            engine.approve(e, self.manager)  # not reviewed yet
        engine.submit_review(e, self.manager)
        with self.assertRaises(LedgerError):
            engine.approve(e, self.acct)  # cannot approve your own entry
        engine.approve(e, self.manager)
        engine.post(e, self.acct)
        self.assertEqual(self.bal("522"), D(300))
        self.assertEqual(self.bal("1111"), D(-300))

    def test_closed_period_refuses_postings_except_with_override(self):
        period = engine.get_period(self.day)
        period.is_closed = True
        period.save()
        with self.assertRaises(LedgerError):
            engine.create_entry(date=self.day, description="x", user=self.acct,
                                lines=[dr(acc("1111"), 5), cr(acc("411"), 5)])
        PermissionOverride.objects.create(user=self.acct, perm="periods.override", allow=True)
        acct = User.objects.get(pk=self.acct.pk)
        self.assertTrue(has_perm(acct, "periods", "override"))
        engine.create_entry(date=self.day, description="x", user=acct, lines=[dr(acc("1111"), 5), cr(acc("411"), 5)])

    def test_auto_reversing_adjustment(self):
        nxt = month_start(self.day) + timedelta(days=40)
        nxt = month_start(nxt)
        e = engine.create_entry(date=self.day, description="accrual", kind=EntryKind.ADJUSTMENT,
                                status=EntryStatus.DRAFT, user=self.acct, auto_reverse_on=nxt,
                                lines=[dr(acc("523"), 120), cr(acc("213"), 120)])
        LedgerSettings.objects.update(require_approval=False)
        engine.post(e, self.acct)
        self.assertEqual(self.bal("213", date_to=self.day), D(120))
        self.assertEqual(self.bal("213", date_to=nxt), D(0))

    def test_periods_follow_the_fiscal_year_setting(self):
        LedgerSettings.objects.update(year_start_month=7)
        p = engine.get_period(date(2031, 3, 15))
        self.assertEqual(p.year.start, date(2030, 7, 1))
        self.assertEqual(p.year.end, date(2031, 6, 30))
        self.assertEqual(p.number, 9)
        self.assertEqual(FiscalPeriod.objects.filter(year=p.year).count(), 12)


class FinanceSyncTest(LedgerTestCase):
    """Patient invoices, payments, expenses and doctor accounts post themselves."""

    def make_invoice(self, price=1000, discount=0, day=None):
        inv = Invoice.objects.create(branch=self.branch, patient=self.patient, doctor=self.doctor,
                                     date=day or self.day, doctor_percent=40, discount=D(discount))
        InvoiceItem.objects.create(invoice=inv, service=self.service, unit_price=D(price), material_cost=D(200))
        inv.recalc()
        return inv

    def test_invoice_and_payments(self):
        inv = self.make_invoice(1000, discount=100)
        party = Party.objects.get(patient=self.patient)
        self.assertEqual(self.bal("1131"), D(900))
        self.assertEqual(self.bal("411"), D(1000))
        self.assertEqual(self.bal("419"), D(100))  # discount (contra revenue, debit nature)
        self.assertEqual(party_balance(party), D(900))
        p = Payment.objects.create(invoice=inv, branch=self.branch, date=self.day, amount=D(400), method=self.cash_m)
        inv.recalc()
        Payment.objects.create(invoice=inv, branch=self.branch, date=self.day, amount=D(500), method=self.card_m)
        inv.recalc()
        self.assertEqual(self.bal("1131"), 0)
        self.assertEqual(treasury_balance(self.cash), D(400))
        self.assertEqual(treasury_balance(self.bank), D(500))
        # voiding a payment reverses its entry
        p.delete()
        inv.recalc()
        self.assertEqual(self.bal("1131"), D(400))
        self.assertEqual(treasury_balance(self.cash), 0)
        self.assertBalanced()

    def test_editing_an_invoice_reverses_and_reposts(self):
        inv = self.make_invoice(1000)
        first = engine.active_entry(inv)
        item = inv.items.first()
        item.unit_price = D(1200)
        item.save()
        inv.recalc()
        second = engine.active_entry(inv)
        self.assertNotEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertTrue(first.is_reversed)
        self.assertEqual(self.bal("411"), D(1200))
        inv.recalc()  # nothing changed → no new entry
        self.assertEqual(engine.active_entry(inv).pk, second.pk)
        inv.is_cancelled = True
        inv.save()
        self.assertIsNone(engine.active_entry(inv))
        self.assertEqual(self.bal("411"), 0)
        self.assertBalanced()

    def test_expense_uses_its_category_account(self):
        Expense.objects.create(branch=self.branch, date=self.day, category="rent", amount=D(3000),
                               method=self.cash_m, description="إيجار")
        self.assertEqual(self.bal("522"), D(3000))
        self.assertEqual(treasury_balance(self.cash), D(-3000))
        ExpenseCategory.objects.create(code="cleaning", name_ar="نظافة", account=acc("529"))
        Expense.objects.create(branch=self.branch, date=self.day, category="cleaning", amount=D(70),
                               method=self.cash_m, description="تنظيف")
        self.assertEqual(self.bal("529"), D(70))

    def test_doctor_month_close_payout_and_reopen(self):
        last = month_start(month_start(self.day) - timedelta(days=1))
        inv = self.make_invoice(1000, day=last)
        Payment.objects.create(invoice=inv, branch=self.branch, date=last, amount=D(1000), method=self.cash_m)
        inv.recalc()
        s = close_month(self.doctor, last, self.admin)
        self.assertEqual(self.bal("511"), D(320))  # (1000 − 200 material) × 40 %
        doc_party = Party.objects.get(user=self.doctor)
        self.assertEqual(party_balance(doc_party), D(-320))  # we owe the doctor
        DoctorPayout.objects.create(doctor=self.doctor, branch=self.branch, settlement=s, date=self.day,
                                    amount=D(320), method=self.cash_m)
        self.assertEqual(party_balance(doc_party), 0)
        self.assertEqual(self.bal("212"), 0)
        DoctorPayout.objects.all().delete()
        reopen_month(s)
        self.assertEqual(self.bal("511"), 0)
        self.assertBalanced()

    def test_change_in_closed_period_is_refused(self):
        inv = self.make_invoice(500)
        FiscalPeriod.objects.filter(start__lte=self.day, end__gte=self.day).update(is_closed=True)
        with engine.acting_user(self.acct):
            with self.assertRaises(LedgerError):
                Payment.objects.create(invoice=inv, branch=self.branch, date=self.day, amount=D(100),
                                       method=self.cash_m)

    def test_backfill_of_old_documents(self):
        inv = self.make_invoice(700)
        JournalEntry.objects.filter(source_model="finance.invoice").update(status=EntryStatus.DRAFT)
        JournalEntry.objects.filter(source_model="finance.invoice").delete()
        self.assertEqual(self.bal("411"), 0)
        self.assertEqual(posting.sync_all_finance(), 1)
        self.assertEqual(self.bal("411"), D(700))
        self.assertEqual(posting.sync_all_finance(), 0)
        self.assertIsNotNone(engine.active_entry(inv))
