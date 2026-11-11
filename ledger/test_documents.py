"""Bills, vouchers, transfers, custody, fixed assets and year closing: each must post the right entry."""
from datetime import date, timedelta
from decimal import Decimal

from . import engine, posting
from .balances import party_balance, treasury_balance
from .engine import cr, dr
from .models import (
    Account, AssetCategory, Bill, BillLine, Currency, Custody, CustodyMovement, ExchangeRate, ExpenseCategory,
    FixedAsset, LedgerError, Party, TaxRate, Transfer, Treasury, Voucher, VoucherAllocation,
)
from .tests import LedgerTestCase, acc

D = Decimal


class DocumentsTest(LedgerTestCase):
    def supplier(self):
        return Party.objects.create(kind="supplier", name="مورد")

    def bill(self, kind="purchase", party=None, amount=1000, tax=None, currency=None, rate=1):
        b = Bill.objects.create(kind=kind, party=party or self.supplier(), date=self.day, currency=currency,
                                rate=D(rate))
        cat = ExpenseCategory.objects.get(code="materials")
        BillLine.objects.create(bill=b, description="مواد", category=cat if kind == "purchase" else None,
                                account=None if kind == "purchase" else acc("413"), quantity=2,
                                unit_price=D(amount) / 2, tax=tax)
        b.recalc()
        return b

    def test_purchase_bill_with_tax_and_partial_payment(self):
        tax = TaxRate.objects.create(name_ar="رسم", rate=D("2.5"), account=acc("117"))
        b = self.bill(tax=tax)
        self.assertEqual(b.total, D("1025.00"))
        posting.post_document(b, self.acct)
        self.assertEqual(self.bal("512"), D(1000))
        self.assertEqual(self.bal("117"), D("25.00"))
        self.assertEqual(self.bal("211"), D("1025.00"))
        v = Voucher.objects.create(kind="payment", treasury=self.cash, party=b.party, amount=D(600),
                                   date=self.day, description="دفعة")
        VoucherAllocation.objects.create(voucher=v, bill=b, amount=D(600))
        posting.post_document(v, self.acct)
        b.refresh_from_db()
        self.assertEqual(b.payment_state, "partial")
        self.assertEqual(b.balance, D("425.00"))
        self.assertEqual(party_balance(b.party), D("-425.00"))
        with self.assertRaises(LedgerError):  # a bill with payments can't be cancelled
            posting.cancel_document(b, self.acct)
        v2 = Voucher.objects.create(kind="payment", treasury=self.cash, party=b.party, amount=D(500),
                                    date=self.day, description="x")
        VoucherAllocation.objects.create(voucher=v2, bill=b, amount=D(500))
        with self.assertRaises(LedgerError):  # more than the bill's balance
            posting.post_document(v2, self.acct)
        posting.cancel_document(v, self.acct, "خطأ")
        b.refresh_from_db()
        self.assertEqual(b.payment_state, "unpaid")
        self.assertEqual(treasury_balance(self.cash), 0)
        self.assertBalanced()

    def test_foreign_currency_receipt_books_exchange_difference(self):
        usd = Currency.objects.get(code="USD")
        ExchangeRate.objects.create(currency=usd, date=self.day - timedelta(days=5), rate=D("4.80"))
        usd_acc = Account.objects.create(code="1122", name_ar="حساب بالدولار", type="asset", nature="debit",
                                         parent=acc("112"), currency=usd)
        usd_bank = Treasury.objects.create(kind="bank", name_ar="دولار", account=usd_acc)
        customer = Party.objects.create(kind="customer", name="شركة تأمين")
        b = self.bill(kind="sales", party=customer, amount=100, currency=usd, rate="4.80")
        posting.post_document(b, self.acct)
        self.assertEqual(self.bal("1132"), D("480.00"))
        v = Voucher.objects.create(kind="receipt", treasury=usd_bank, party=customer, amount=D(100),
                                   rate=D("5.00"), date=self.day, description="تحصيل")
        VoucherAllocation.objects.create(voucher=v, bill=b, amount=D(100))
        posting.post_document(v, self.acct)
        self.assertEqual(self.bal("1132"), 0)
        self.assertEqual(self.bal("421"), D("20.00"))  # 100 × (5.00 − 4.80) gain
        self.assertEqual(treasury_balance(usd_bank, in_currency=True), D(100))
        from .closing import revalue_currencies

        ExchangeRate.objects.create(currency=usd, date=self.day, rate=D("4.90"))
        revalue_currencies(self.day, self.acct)
        self.assertEqual(treasury_balance(usd_bank), D("490.00"))
        self.assertEqual(self.bal("541"), D("10.00"))
        self.assertBalanced()

    def test_direct_voucher_and_transfer_with_fees(self):
        v = Voucher.objects.create(kind="receipt", treasury=self.cash, account=acc("31"), amount=D(10000),
                                   date=self.day, description="رأس مال")
        posting.post_document(v, self.acct)
        t = Transfer.objects.create(source=self.cash, target=self.bank, amount=D(4000), fees=D(15),
                                    date=self.day, description="إيداع")
        posting.post_document(t, self.acct)
        self.assertEqual(treasury_balance(self.cash), D(5985))
        self.assertEqual(treasury_balance(self.bank), D(4000))
        self.assertEqual(self.bal("527"), D(15))
        self.assertEqual(self.bal("31"), D(10000))
        self.assertBalanced()

    def test_custody_issue_spend_return(self):
        emp = Party.objects.create(kind="employee", name="موظف")
        c = Custody.objects.create(holder=emp, purpose="مشتريات", date=self.day)

        def move(**kw):
            return posting.post_document(CustodyMovement.objects.create(custody=c, date=self.day, **kw), self.acct)

        move(kind="issue", amount=D(1000), treasury=self.cash)
        move(kind="expense", amount=D(350), category=ExpenseCategory.objects.get(code="stationery"),
             description="ورق")
        with self.assertRaises(LedgerError):
            move(kind="expense", amount=D(700), account=acc("529"))  # more than is left
        self.assertEqual(c.figures()["remaining"], D(650))
        move(kind="return", amount=D(650), treasury=self.cash)
        self.assertEqual(c.figures()["remaining"], 0)
        self.assertEqual(self.bal("1141"), 0)
        self.assertEqual(self.bal("524"), D(350))
        self.assertEqual(treasury_balance(self.cash), D(-350))
        self.assertBalanced()


class AssetsAndClosingTest(LedgerTestCase):
    def make_asset(self, **kw):
        cat = AssetCategory.objects.get(asset_account__code="121")
        data = dict(name_ar="جهاز ليزر", category=cat, purchase_date=date(2025, 1, 10),
                    in_service_date=date(2025, 1, 10), cost=D(12000), salvage_value=D(0), life_months=12,
                    funding="treasury", treasury=self.bank)
        data.update(kw)
        return FixedAsset.objects.create(**data)

    def test_straight_line_depreciation_improvement_and_disposal(self):
        from . import assets as svc

        a = self.make_asset()
        svc.acquire(a, self.acct)
        self.assertEqual(self.bal("121"), D(12000))
        run = svc.run_depreciation(date(2025, 3, 31), self.acct)
        self.assertEqual(run.total, D(3000))  # 3 months × 1000
        a.refresh_from_db()
        self.assertEqual(a.book_value(), D(9000))
        # an improvement in April is spread over the 9 months left
        svc.improve(a, self.acct, day=date(2025, 4, 5), amount=D(900), treasury=self.bank, description="ترقية")
        run2 = svc.run_depreciation(date(2025, 4, 30), self.acct)
        self.assertEqual(run2.total, D("1100.00"))  # 9900 ÷ 9
        with self.assertRaises(LedgerError):
            svc.reverse_run(run, self.acct)  # only the latest run can be undone
        svc.reverse_run(run2, self.acct)
        a.refresh_from_db()
        self.assertEqual(a.depreciated_to, date(2025, 3, 31))
        svc.run_depreciation(date(2025, 4, 30), self.acct)
        a.refresh_from_db()
        with self.assertRaises(LedgerError):  # May not depreciated yet
            svc.dispose(a, self.acct, day=date(2025, 6, 10), proceeds=D(8000), treasury=self.cash)
        svc.dispose(a, self.acct, day=date(2025, 5, 10), proceeds=D(8000), treasury=self.cash)
        self.assertEqual(self.bal("121"), 0)
        self.assertEqual(self.bal("1291"), 0)
        self.assertEqual(self.bal("542"), D(800))  # book 12900 − 4100 = 8800, sold for 8000
        self.assertBalanced()

    def test_opening_asset_with_prior_depreciation(self):
        from . import assets as svc

        a = self.make_asset(funding="opening", treasury=None, opening_accumulated=D(6000),
                            in_service_date=date(2024, 7, 1), purchase_date=date(2024, 7, 1))
        svc.acquire(a, self.acct, day=date(2025, 1, 1))
        self.assertEqual(self.bal("34"), D(6000))
        run = svc.run_depreciation(date(2025, 1, 31), self.acct)
        self.assertEqual(run.total, D(1000))  # book 6000 over the 6 months of life left (Jan–Jun 2025)
        self.assertBalanced()

    def test_declining_balance_never_goes_below_salvage(self):
        from . import assets as svc

        a = self.make_asset(method="declining", declining_rate=D(50), salvage_value=D(1000), life_months=24)
        svc.acquire(a, self.acct)
        svc.run_depreciation(date(2027, 12, 31), self.acct)
        a.refresh_from_db()
        self.assertEqual(a.book_value(), D(1000))
        self.assertEqual(a.status, "depreciated")
        self.assertBalanced()

    def test_year_close_moves_profit_to_retained_earnings(self):
        from .closing import close_year, reopen_year

        y = engine.get_year(date(2025, 6, 1))
        engine.create_entry(date=date(2025, 6, 1), description="r",
                            lines=[dr(acc("1111"), 5000), cr(acc("411"), 5000)])
        engine.create_entry(date=date(2025, 6, 2), description="e",
                            lines=[dr(acc("522"), 1200), cr(acc("1111"), 1200)])
        close_year(y, self.admin)
        self.assertEqual(self.bal("411", date_to=date(2025, 12, 31)), 0)
        self.assertEqual(self.bal("32"), D(3800))
        with self.assertRaises(LedgerError):
            engine.create_entry(date=date(2025, 7, 1), description="late", user=self.acct,
                                lines=[dr(acc("1111"), 1), cr(acc("411"), 1)])
        reopen_year(y, self.admin)
        self.assertEqual(self.bal("32"), 0)
        self.assertBalanced()
