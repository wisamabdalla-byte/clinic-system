"""Every accounting page opens, and the main forms post correct, balanced entries through HTTP."""
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse

from accounts.models import User
from finance.models import Invoice, InvoiceItem, Payment

from .balances import party_balance, treasury_balance
from .models import (
    Account, AssetCategory, Bill, Custody, EntryStatus, FixedAsset, JournalEntry, LedgerSettings, Party,
    PermissionOverride, Transfer, Voucher,
)
from .reports import REPORTS
from .tests import LedgerTestCase, acc

D = Decimal


class PagesTest(LedgerTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.supplier = Party.objects.create(kind="supplier", name="مورد المواد")
        self.employee = Party.objects.create(kind="employee", name="موظف المشتريات")

    def lines_post(self, rows, **extra):
        data = {"date": self.day.isoformat(), "kind": "manual", "description": "قيد اختبار", "reference": "",
                "lines-TOTAL_FORMS": str(len(rows)), "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "2",
                "lines-MAX_NUM_FORMS": "1000"}
        for i, r in enumerate(rows):
            for k, v in r.items():
                data[f"lines-{i}-{k}"] = v
        data.update(extra)
        return data

    def test_every_page_opens(self):
        inv = Invoice.objects.create(branch=self.branch, patient=self.patient, doctor=self.doctor, date=self.day,
                                     doctor_percent=40)
        InvoiceItem.objects.create(invoice=inv, service=self.service, unit_price=D(100))
        inv.recalc()
        entry = JournalEntry.objects.first()
        urls = [reverse(n) for n in [
            "acc_home", "account_list", "account_create", "journal_list", "journal_create", "bill_list",
            "bill_create_sales", "bill_create_purchase", "voucher_list", "voucher_create_receipt",
            "voucher_create_payment", "treasury_list", "treasury_create", "transfer_list", "transfer_create",
            "reconcile_create", "custody_list", "custody_create", "party_list", "party_create", "asset_list",
            "asset_create", "depreciation", "assetcategory_list", "ledger_reports", "ledger_settings",
            "period_list", "fin_permissions", "backup", "project_list", "expcat_list", "tax_list",
            "currency_list", "rate_list", "rate_create", "tax_create"]]
        urls += [reverse("journal_detail", args=[entry.pk]), reverse("account_edit", args=[acc("1111").pk]),
                 reverse("party_detail", args=[self.supplier.pk]), reverse("party_list") + "?kind=patient"]
        for key in REPORTS:
            urls += [reverse("ledger_report", args=[key]), reverse("ledger_report", args=[key]) + "?format=print"
                     + "&compare=prev_year&unposted=1"]
        for url in urls:
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200, url)
        r = self.client.get(reverse("ledger_report", args=["trial_balance"]) + "?format=xlsx")
        self.assertEqual(r["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(r.content.startswith(b"PK"))

    def test_manual_entry_workflow_over_http(self):
        LedgerSettings.objects.update(require_review=True, require_approval=True)
        rows = [{"account": acc("522").pk, "debit": "750", "credit": "", "description": "إيجار"},
                {"account": acc("211").pk, "debit": "", "credit": "750", "party": self.supplier.code}]
        r = self.client.post(reverse("journal_create"), self.lines_post(rows))
        e = JournalEntry.objects.get(kind="manual")
        self.assertRedirects(r, reverse("journal_detail", args=[e.pk]))
        self.assertEqual(e.status, EntryStatus.DRAFT)
        # unbalanced edit is refused and nothing changes
        bad = [dict(rows[0]), dict(rows[1], credit="700")]
        self.client.post(reverse("journal_edit", args=[e.pk]), self.lines_post(bad))
        self.assertEqual(e.lines.get(account__code="211").credit, D(750))
        act = lambda do: self.client.post(reverse("journal_action", args=[e.pk]), {"do": do})  # noqa: E731
        act("review")
        # the admin created it, so a manager must approve it
        self.client.force_login(self.manager)
        act("approve")
        act("post")
        e.refresh_from_db()
        self.assertEqual(e.status, EntryStatus.POSTED)
        self.assertEqual(party_balance(self.supplier), D(-750))
        # posted: editing redirects with an error, deleting is refused
        self.client.post(reverse("journal_action", args=[e.pk]), {"do": "delete"})
        self.assertTrue(JournalEntry.objects.filter(pk=e.pk).exists())
        self.client.force_login(self.admin)
        r = self.client.post(reverse("journal_action", args=[e.pk]), {"do": "correct", "note": "خطأ"})
        draft = JournalEntry.objects.filter(kind="manual", status="draft").latest("id")
        self.assertRedirects(r, reverse("journal_edit", args=[draft.pk]))
        self.assertEqual(party_balance(self.supplier), 0)
        self.assertBalanced()

    def test_accountant_cannot_approve_or_reverse_unless_granted(self):
        self.client.force_login(self.acct)
        rows = [{"account": acc("1111").pk, "debit": "10"}, {"account": acc("423").pk, "credit": "10"}]
        self.client.post(reverse("journal_create"), self.lines_post(rows))
        e = JournalEntry.objects.get(kind="manual")
        self.assertEqual(self.client.post(reverse("journal_action", args=[e.pk]), {"do": "approve"}).status_code, 403)
        PermissionOverride.objects.create(user=self.acct, perm="journal.edit", allow=False)
        self.assertEqual(self.client.get(reverse("journal_create")).status_code, 403)
        doctor = User.objects.get(pk=self.doctor.pk)
        self.client.force_login(doctor)
        self.assertEqual(self.client.get(reverse("acc_home")).status_code, 403)

    def test_bill_voucher_transfer_custody_asset_over_http(self):
        # purchase bill, saved and posted
        data = {"party": self.supplier.code, "date": self.day.isoformat(), "due_date": "", "currency": "",
                "rate": "", "party_reference": "INV-77", "notes": "", "and_post": "1",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000", "lines-0-description": "قفازات",
                "lines-0-category": str(self.expcat("materials")), "lines-0-quantity": "10",
                "lines-0-unit_price": "25"}
        self.client.post(reverse("bill_create_purchase"), data)
        bill = Bill.objects.get()
        self.assertEqual((bill.status, bill.total), ("posted", D(250)))
        # payment voucher applied to it
        v = {"date": self.day.isoformat(), "treasury": str(self.cash.pk), "amount": "250", "rate": "",
             "party": self.supplier.code, "account": "", "category": "", "project": "", "payee": "",
             "description": "سداد", "reference": "", "and_post": "1", "alloc-TOTAL_FORMS": "1",
             "alloc-INITIAL_FORMS": "0", "alloc-0-bill": str(bill.pk), "alloc-0-amount": "250"}
        self.client.post(reverse("voucher_create_payment"), v)
        bill.refresh_from_db()
        self.assertEqual(bill.payment_state, "paid")
        self.assertEqual(Voucher.objects.get().status, "posted")
        # transfer
        self.client.post(reverse("transfer_create"), {"date": self.day.isoformat(), "source": self.bank.pk,
                                                      "target": self.cash.pk, "amount": "1000", "fees": "0",
                                                      "description": "سحب", "reference": ""})
        self.assertEqual(Transfer.objects.get().status, "posted")
        self.assertEqual(treasury_balance(self.cash), D(750))
        # custody: issue then spend
        self.client.post(reverse("custody_create"), {"kind": "custody", "holder": self.employee.code,
                                                     "date": self.day.isoformat(), "purpose": "مشتريات",
                                                     "project": "", "amount": "300", "treasury": self.cash.pk})
        c = Custody.objects.get()
        self.client.post(reverse("custody_detail", args=[c.pk]), {
            "do": "add", "kind": "expense", "date": self.day.isoformat(), "amount": "120",
            "category": self.expcat("stationery"), "description": "ورق"})
        self.assertEqual(c.figures()["remaining"], D(180))
        # fixed asset: create draft, book it, depreciate
        cat = AssetCategory.objects.get(asset_account__code="123")
        self.client.post(reverse("asset_create"), {
            "name_ar": "حاسوب", "name_en": "", "category": cat.pk, "branch": "", "project": "",
            "purchase_date": "2025-01-05", "in_service_date": "2025-01-05", "cost": "3600", "salvage_value": "0",
            "life_months": "", "method": "straight", "declining_rate": "", "funding": "treasury",
            "treasury": self.bank.pk, "supplier": "", "opening_accumulated": "0", "serial_number": "",
            "location": "", "notes": ""})
        a = FixedAsset.objects.get()
        self.assertEqual(a.life_months, 36)
        self.client.post(reverse("asset_detail", args=[a.pk]), {"do": "activate", "date": "2025-01-05"})
        self.client.post(reverse("depreciation"), {"date": "2025-03-31"})
        a.refresh_from_db()
        self.assertEqual(a.accumulated(), D(300))
        self.assertBalanced()

    def test_treasury_creation_makes_its_own_account(self):
        self.client.post(reverse("treasury_create"), {"kind": "bank", "name_ar": "مصرف الجمهورية",
                                                      "name_en": "", "account": "", "branch": "", "bank_name": "",
                                                      "account_number": "", "iban": "", "is_active": "on",
                                                      "currency": ""})
        acc_ = Account.objects.get(name_ar="مصرف الجمهورية")
        self.assertEqual((acc_.parent.code, acc_.code), ("112", "1122"))

    def test_period_close_and_backup_download(self):
        period = self.client.get(reverse("period_list"))
        self.assertEqual(period.status_code, 200)
        from .engine import get_period

        last = get_period(date(self.day.year, self.day.month, 1) - timedelta(days=1))
        self.client.post(reverse("period_list"), {"do": "close", "period": last.pk})
        last.refresh_from_db()
        self.assertTrue(last.is_closed)
        # a payment dated in the closed month is refused with a message, nothing saved
        inv = Invoice.objects.create(branch=self.branch, patient=self.patient, doctor=self.doctor, date=self.day,
                                     doctor_percent=0)
        InvoiceItem.objects.create(invoice=inv, service=self.service, unit_price=D(100))
        inv.recalc()
        self.client.force_login(self.acct)
        r = self.client.post(reverse("invoice_pay", args=[inv.pk]), {
            "date": last.end.isoformat(), "amount": "50", "method": self.cash_m.pk, "reference": "", "notes": ""},
            HTTP_REFERER=reverse("invoice_detail", args=[inv.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Payment.objects.exists())
        self.client.force_login(self.admin)
        r = self.client.post(reverse("backup"), {"do": "download"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'"ledger.account"', r.content)

    def expcat(self, code):
        from .models import ExpenseCategory

        return ExpenseCategory.objects.get(code=code).pk

