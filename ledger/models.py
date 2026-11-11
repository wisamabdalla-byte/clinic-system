"""General ledger: chart of accounts, journal entries and everything that posts to them.

See docs/accounting-design.md for the overall picture. The rules that move money
live in ledger/engine.py (posting, reversal, periods) and ledger/posting.py (which
document produces which entry); this file only describes the tables.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.i18n import current_lang, tr, tr_lazy
from core.models import MONEY, Branch, Named, PaymentMethod, TimeStamped

ZERO = Decimal("0")
RATE = {"max_digits": 18, "decimal_places": 6}
QTY = {"max_digits": 12, "decimal_places": 3}
USER = settings.AUTH_USER_MODEL


class LedgerError(Exception):
    """A rule of the ledger was broken (unbalanced entry, closed period, ...).

    The message is already translated; LedgerErrorMiddleware shows it to the user
    and the request's transaction is rolled back."""


class ProtectedEntryError(LedgerError):
    pass


def by_user(related_name="+"):
    return models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name=related_name)


# ======================================================================= chart of accounts
class AccountType(models.TextChoices):
    ASSET = "asset", tr_lazy("at_asset")
    LIABILITY = "liability", tr_lazy("at_liability")
    EQUITY = "equity", tr_lazy("at_equity")
    REVENUE = "revenue", tr_lazy("at_revenue")
    EXPENSE = "expense", tr_lazy("at_expense")


NATURAL = {"asset": "debit", "expense": "debit", "liability": "credit", "equity": "credit", "revenue": "credit"}
BALANCE_SHEET_TYPES = ("asset", "liability", "equity")
PNL_TYPES = ("revenue", "expense")


class Nature(models.TextChoices):
    DEBIT = "debit", tr_lazy("nature_debit")
    CREDIT = "credit", tr_lazy("nature_credit")


class CashFlowClass(models.TextChoices):
    OPERATING = "operating", tr_lazy("cf_operating")
    INVESTING = "investing", tr_lazy("cf_investing")
    FINANCING = "financing", tr_lazy("cf_financing")


class Currency(models.Model):
    code = models.CharField(max_length=3, unique=True)
    name_ar = models.CharField(max_length=60)
    name_en = models.CharField(max_length=60, blank=True)
    symbol = models.CharField(max_length=10, blank=True)
    is_base = models.BooleanField(default=False, editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_base", "code"]
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.code

    @property
    def name(self):
        return self.name_en if current_lang() == "en" and self.name_en else self.name_ar

    def rate_on(self, day):
        """LYD per one unit of this currency on that day (latest rate on or before it)."""
        if self.is_base:
            return Decimal(1)
        r = self.rates.filter(date__lte=day).order_by("-date").first()
        if r is None:
            raise LedgerError(tr("no_rate", self.code, f"{day:%Y-%m-%d}"))
        return r.rate


class ExchangeRate(models.Model):
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates")
    date = models.DateField(default=timezone.localdate)
    rate = models.DecimalField(**RATE, validators=[MinValueValidator(Decimal("0.000001"))])
    created_by = by_user()

    class Meta:
        ordering = ["-date", "currency__code"]
        constraints = [models.UniqueConstraint(fields=["currency", "date"], name="one_rate_per_day")]

    def __str__(self):
        return f"{self.currency} {self.date:%Y-%m-%d} = {self.rate}"


class Account(TimeStamped, Named):
    code = models.CharField(max_length=20, unique=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    type = models.CharField(max_length=12, choices=AccountType.choices)
    nature = models.CharField(max_length=6, choices=Nature.choices)
    is_group = models.BooleanField(default=False)
    is_control = models.BooleanField(default=False)  # lines must name a customer/supplier/employee
    allow_manual = models.BooleanField(default=True)  # usable in manual journal entries
    cash_flow = models.CharField(max_length=10, choices=CashFlowClass.choices, blank=True)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def level(self):
        n, node = 1, self
        while node.parent_id:
            node = node.parent
            n += 1
        return n

    @property
    def sign(self):
        """+1 when the account grows with debits."""
        return 1 if self.nature == Nature.DEBIT else -1

    @property
    def is_cash(self):
        return hasattr(self, "treasury")

    def clean(self):
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": tr("acc_parent_self")})
            node = self.parent
            while node is not None:
                if node.pk == self.pk:
                    raise ValidationError({"parent": tr("acc_parent_loop")})
                node = node.parent
            if not self.parent.is_group:
                raise ValidationError({"parent": tr("acc_parent_not_group")})
            if self.parent.type != self.type:
                raise ValidationError({"type": tr("acc_type_mismatch", self.parent.get_type_display())})
        if self.pk and self.is_group and self.lines.exists():
            raise ValidationError({"is_group": tr("acc_group_has_lines")})
        if self.pk and not self.is_group and self.children.exists():
            raise ValidationError({"is_group": tr("acc_has_children")})


class Project(TimeStamped, Named):
    """A project or cost centre that expenses can be charged to."""

    code = models.CharField(max_length=20, unique=True)
    budget = models.DecimalField(**MONEY, null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.name}"


class PartyKind(models.TextChoices):
    PATIENT = "patient", tr_lazy("pk_patient")
    CUSTOMER = "customer", tr_lazy("pk_customer")
    SUPPLIER = "supplier", tr_lazy("pk_supplier")
    EMPLOYEE = "employee", tr_lazy("pk_employee")
    DOCTOR = "doctor", tr_lazy("pk_doctor")


PARTY_PREFIX = {"patient": "PT", "customer": "C", "supplier": "S", "employee": "E", "doctor": "D"}


class Party(TimeStamped):
    """A customer, supplier, employee or doctor with an account balance."""

    code = models.CharField(max_length=20, unique=True, editable=False)
    kind = models.CharField(max_length=10, choices=PartyKind.choices)
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    tax_no = models.CharField(max_length=50, blank=True)
    payment_terms_days = models.PositiveIntegerField(default=0)
    credit_limit = models.DecimalField(**MONEY, null=True, blank=True)
    account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="parties",
        limit_choices_to={"is_group": False, "is_control": True},
    )
    # CASCADE is safe: a party with any journal line, bill or voucher is itself protected by those.
    patient = models.OneToOneField("clinic.Patient", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="party")
    user = models.OneToOneField(USER, null=True, blank=True, on_delete=models.CASCADE, related_name="party")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "parties"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            from .engine import next_number

            self.code = next_number(PARTY_PREFIX.get(self.kind, "P"), yearly=False, width=5)
        super().save(*args, **kwargs)

    def default_account(self):
        if self.account_id:
            return self.account
        key = {"patient": "patients_receivable", "customer": "customers_receivable",
               "supplier": "suppliers_payable", "employee": "employee_advances",
               "doctor": "doctors_payable"}[self.kind]
        return LedgerSettings.get().acct(key)


# ======================================================================= periods
class FiscalYear(models.Model):
    name = models.CharField(max_length=20, unique=True)
    start = models.DateField()
    end = models.DateField()
    is_closed = models.BooleanField(default=False)
    closing_entry = models.ForeignKey("JournalEntry", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    closed_by = by_user()
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start"]

    def __str__(self):
        return self.name


class FiscalPeriod(models.Model):
    year = models.ForeignKey(FiscalYear, on_delete=models.CASCADE, related_name="periods")
    number = models.PositiveSmallIntegerField()
    start = models.DateField()
    end = models.DateField()
    is_closed = models.BooleanField(default=False)
    closed_by = by_user()
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start"]
        constraints = [models.UniqueConstraint(fields=["year", "number"], name="one_period_number_per_year")]

    def __str__(self):
        return f"{self.start:%Y-%m}"


# ======================================================================= settings
SETTING_ACCOUNTS = [
    # key, label key, type the account must have
    ("cash_account", "set_cash_account", "asset"),
    ("bank_account", "set_bank_account", "asset"),
    ("patients_receivable", "set_patients_receivable", "asset"),
    ("customers_receivable", "set_customers_receivable", "asset"),
    ("suppliers_payable", "set_suppliers_payable", "liability"),
    ("doctors_payable", "set_doctors_payable", "liability"),
    ("employee_advances", "set_employee_advances", "asset"),
    ("custody_account", "set_custody_account", "asset"),
    ("revenue_dental", "set_revenue_dental", "revenue"),
    ("revenue_derma", "set_revenue_derma", "revenue"),
    ("revenue_general", "set_revenue_general", "revenue"),
    ("sales_discount", "set_sales_discount", "revenue"),
    ("doctor_commission", "set_doctor_commission", "expense"),
    ("default_expense", "set_default_expense", "expense"),
    ("bank_charges", "set_bank_charges", "expense"),
    ("fx_gain", "set_fx_gain", "revenue"),
    ("fx_loss", "set_fx_loss", "expense"),
    ("asset_gain", "set_asset_gain", "revenue"),
    ("asset_loss", "set_asset_loss", "expense"),
    ("retained_earnings", "set_retained_earnings", "equity"),
    ("opening_equity", "set_opening_equity", "equity"),
]


class LedgerSettings(models.Model):
    """One row: default accounts used by automatic entries, and the approval rules."""

    year_start_month = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1),
                                                                                MaxValueValidator(12)])
    require_review = models.BooleanField(default=False)
    require_approval = models.BooleanField(default=True)
    allow_self_approval = models.BooleanField(default=False)
    auto_post_documents = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    # plus one account field per SETTING_ACCOUNTS entry, added below the class

    class Meta:
        verbose_name_plural = "ledger settings"

    @classmethod
    def get(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    def acct(self, key):
        account = getattr(self, key)
        if account is None:
            raise LedgerError(tr("missing_setting_account", tr(dict((k, l) for k, l, _ in SETTING_ACCOUNTS)[key])))
        return account


for _key, _label, _type in SETTING_ACCOUNTS:
    LedgerSettings.add_to_class(_key, models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        limit_choices_to={"is_group": False, "type": _type}))


class PaymentMethodAccount(models.Model):
    """Which ledger account the money of a payment method goes to (cash box, POS bank, ...)."""

    method = models.OneToOneField(PaymentMethod, on_delete=models.CASCADE, related_name="ledger_account")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+",
                                limit_choices_to={"is_group": False})


class ExpenseGroup(models.TextChoices):
    OPERATING = "operating", tr_lazy("eg_operating")
    ADMIN = "admin", tr_lazy("eg_admin")
    GENERAL = "general", tr_lazy("eg_general")
    PROJECT = "project", tr_lazy("eg_project")
    OTHER = "other", tr_lazy("eg_other")


class ExpenseCategory(TimeStamped, Named):
    code = models.CharField(max_length=30, unique=True)
    group = models.CharField(max_length=12, choices=ExpenseGroup.choices, default=ExpenseGroup.GENERAL)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expense_categories",
                                limit_choices_to={"is_group": False, "type": "expense"})
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["group", "name_ar"]
        verbose_name_plural = "expense categories"


class TaxKind(models.TextChoices):
    SALES = "sales", tr_lazy("tax_sales")
    PURCHASE = "purchase", tr_lazy("tax_purchase")
    BOTH = "both", tr_lazy("tax_both")


class TaxRate(TimeStamped, Named):
    """A tax or fee the clinic must apply. No rates are assumed: the admin enters them per Libyan law."""

    rate = models.DecimalField(max_digits=6, decimal_places=3,
                               validators=[MinValueValidator(0), MaxValueValidator(100)])
    kind = models.CharField(max_length=10, choices=TaxKind.choices, default=TaxKind.BOTH)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+",
                                limit_choices_to={"is_group": False})
    legal_reference = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name_ar"]

    def __str__(self):
        return f"{self.name} ({self.rate.normalize()}%)"


class PermissionOverride(models.Model):
    """Gives (allow=True) or takes away (allow=False) one finance action from one user."""

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="perm_overrides")
    perm = models.CharField(max_length=60)  # "module.action"
    allow = models.BooleanField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "perm"], name="one_override_per_perm")]


class Sequence(models.Model):
    key = models.CharField(max_length=30, unique=True)
    last = models.PositiveIntegerField(default=0)


# ======================================================================= journal
class EntryStatus(models.TextChoices):
    DRAFT = "draft", tr_lazy("st_draft")
    REVIEWED = "reviewed", tr_lazy("st_reviewed")
    APPROVED = "approved", tr_lazy("st_approved")
    POSTED = "posted", tr_lazy("st_posted")


class EntryKind(models.TextChoices):
    MANUAL = "manual", tr_lazy("ek_manual")
    AUTO = "auto", tr_lazy("ek_auto")
    OPENING = "opening", tr_lazy("ek_opening")
    ADJUSTMENT = "adjustment", tr_lazy("ek_adjustment")
    REVERSAL = "reversal", tr_lazy("ek_reversal")
    CLOSING = "closing", tr_lazy("ek_closing")


class JournalEntry(TimeStamped):
    number = models.CharField(max_length=30, unique=True, editable=False)
    date = models.DateField(db_index=True)
    period = models.ForeignKey(FiscalPeriod, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(max_length=12, choices=EntryKind.choices, default=EntryKind.MANUAL)
    status = models.CharField(max_length=10, choices=EntryStatus.choices, default=EntryStatus.DRAFT,
                              db_index=True)
    description = models.CharField(max_length=255)
    reference = models.CharField(max_length=100, blank=True)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    # The document this entry was produced from, e.g. "finance.invoice" + "12".
    source_model = models.CharField(max_length=60, blank=True, db_index=True)
    source_id = models.CharField(max_length=40, blank=True, db_index=True)
    source_label = models.CharField(max_length=60, blank=True)
    reversal_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="reversed_by")
    is_reversed = models.BooleanField(default=False)
    auto_reverse_on = models.DateField(null=True, blank=True)
    total = models.DecimalField(**MONEY, default=0, editable=False)
    return_note = models.CharField(max_length=255, blank=True)
    created_by = by_user()
    reviewed_by = by_user()
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = by_user()
    approved_at = models.DateTimeField(null=True, blank=True)
    posted_by = by_user()
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "journal entries"

    def __str__(self):
        return self.number

    @property
    def is_posted(self):
        return self.status == EntryStatus.POSTED

    @property
    def is_editable(self):
        return self.status != EntryStatus.POSTED and self.kind in (EntryKind.MANUAL, EntryKind.OPENING,
                                                                     EntryKind.ADJUSTMENT)

    def delete(self, *args, **kwargs):
        from .engine import purge_allowed

        if self.status == EntryStatus.POSTED and not purge_allowed():
            raise ProtectedEntryError(tr("posted_cannot_delete"))
        return super().delete(*args, **kwargs)

    def totals(self):
        agg = self.lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        return agg["d"] or ZERO, agg["c"] or ZERO


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(**MONEY, default=0)
    credit = models.DecimalField(**MONEY, default=0)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    fx_amount = models.DecimalField(**MONEY, null=True, blank=True)  # amount in that currency
    fx_rate = models.DecimalField(**RATE, null=True, blank=True)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name="lines")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.PROTECT, related_name="lines")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reconciliation = models.ForeignKey("BankReconciliation", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="lines")

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["account", "entry"])]

    @property
    def amount(self):
        return self.debit - self.credit


class Attachment(models.Model):
    """A scanned invoice, receipt or document attached to an entry or any other financial document."""

    target_model = models.CharField(max_length=60, db_index=True)
    target_id = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    data = models.BinaryField()
    uploaded_by = by_user()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


# ======================================================================= cash and banks
class TreasuryKind(models.TextChoices):
    CASH = "cash", tr_lazy("tk_cash")
    BANK = "bank", tr_lazy("tk_bank")


class Treasury(TimeStamped, Named):
    kind = models.CharField(max_length=6, choices=TreasuryKind.choices)
    account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="treasury",
                                   limit_choices_to={"is_group": False, "type": "asset"})
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    bank_name = models.CharField(max_length=120, blank=True)
    account_number = models.CharField(max_length=60, blank=True)
    iban = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name_ar"]
        verbose_name_plural = "treasuries"

    @property
    def currency(self):
        return self.account.currency


class DocStatus(models.TextChoices):
    DRAFT = "draft", tr_lazy("st_draft")
    POSTED = "posted", tr_lazy("st_posted")
    CANCELLED = "cancelled", tr_lazy("st_cancelled")


class PostedDocument(TimeStamped):
    """Shared fields of documents that produce one journal entry when posted."""

    number = models.CharField(max_length=30, unique=True, editable=False)
    date = models.DateField(default=timezone.localdate, db_index=True)
    status = models.CharField(max_length=10, choices=DocStatus.choices, default=DocStatus.DRAFT)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cancel_reason = models.CharField(max_length=255, blank=True)
    created_by = by_user()

    PREFIX = "DOC"

    class Meta:
        abstract = True
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            from .engine import next_number

            self.number = next_number(self.PREFIX, self.date)
        super().save(*args, **kwargs)


class Transfer(PostedDocument):
    PREFIX = "TR"
    source = models.ForeignKey(Treasury, on_delete=models.PROTECT, related_name="transfers_out")
    target = models.ForeignKey(Treasury, on_delete=models.PROTECT, related_name="transfers_in")
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    source_rate = models.DecimalField(**RATE, default=1)
    target_amount = models.DecimalField(**MONEY, null=True, blank=True)  # when the currencies differ
    target_rate = models.DecimalField(**RATE, default=1)
    fees = models.DecimalField(**MONEY, default=0, validators=[MinValueValidator(0)])
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)

    class Meta(PostedDocument.Meta):
        pass


class BankReconciliation(TimeStamped):
    treasury = models.ForeignKey(Treasury, on_delete=models.PROTECT, related_name="reconciliations")
    statement_date = models.DateField()
    statement_balance = models.DecimalField(**MONEY)
    is_done = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)
    created_by = by_user()
    done_by = by_user()
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-statement_date", "-id"]

    def __str__(self):
        return f"{self.treasury} · {self.statement_date:%Y-%m-%d}"


# ======================================================================= bills and vouchers
class BillKind(models.TextChoices):
    SALES = "sales", tr_lazy("bk_sales")
    PURCHASE = "purchase", tr_lazy("bk_purchase")


class Bill(PostedDocument):
    """A sales invoice to a customer or a purchase/expense invoice from a supplier."""

    kind = models.CharField(max_length=10, choices=BillKind.choices)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="bills")
    due_date = models.DateField(null=True, blank=True)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    rate = models.DecimalField(**RATE, default=1)
    party_reference = models.CharField(max_length=100, blank=True)  # the supplier's own invoice number
    notes = models.TextField(blank=True)
    subtotal = models.DecimalField(**MONEY, default=0, editable=False)
    tax_total = models.DecimalField(**MONEY, default=0, editable=False)
    total = models.DecimalField(**MONEY, default=0, editable=False)
    paid = models.DecimalField(**MONEY, default=0, editable=False)

    class Meta(PostedDocument.Meta):
        pass

    @property
    def PREFIX(self):  # noqa: N802 - mirrors the class attribute of other documents
        return "SI" if self.kind == BillKind.SALES else "PI"

    @property
    def balance(self):
        return max(self.total - self.paid, ZERO)

    @property
    def payment_state(self):
        if self.status == DocStatus.CANCELLED:
            return "cancelled"
        if self.status == DocStatus.DRAFT:
            return "draft"
        if self.total > 0 and self.paid >= self.total:
            return "paid"
        return "partial" if self.paid > 0 else "unpaid"

    @property
    def is_overdue(self):
        return (self.payment_state in ("unpaid", "partial") and self.due_date is not None
                and self.due_date < timezone.localdate())

    def recalc(self):
        from .engine import q

        lines = list(self.lines.select_related("tax"))
        self.subtotal = q(sum((ln.net for ln in lines), ZERO))
        self.tax_total = q(sum((ln.tax_amount for ln in lines), ZERO))
        self.total = q(self.subtotal + self.tax_total)
        self.save(update_fields=["subtotal", "tax_total", "total", "updated_at"])

    def refresh_paid(self):
        from .engine import q

        paid = self.allocations.filter(voucher__status=DocStatus.POSTED).aggregate(s=Sum("amount"))["s"]
        self.paid = q(paid)
        self.save(update_fields=["paid", "updated_at"])


class BillLine(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=200)
    category = models.ForeignKey(ExpenseCategory, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                limit_choices_to={"is_group": False})
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(**QTY, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    unit_price = models.DecimalField(**MONEY, validators=[MinValueValidator(0)])
    tax = models.ForeignKey(TaxRate, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["id"]

    @property
    def net(self):
        from .engine import q

        return q((self.quantity or 0) * (self.unit_price or 0))

    @property
    def tax_amount(self):
        from .engine import q

        return q(self.net * self.tax.rate / 100) if self.tax_id else ZERO

    def target_account(self):
        if self.account_id:
            return self.account
        if self.category_id:
            return self.category.account
        return None


class VoucherKind(models.TextChoices):
    RECEIPT = "receipt", tr_lazy("vk_receipt")
    PAYMENT = "payment", tr_lazy("vk_payment")


class Voucher(PostedDocument):
    """سند قبض / سند صرف: money in or out of a cash box or bank account."""

    kind = models.CharField(max_length=10, choices=VoucherKind.choices)
    treasury = models.ForeignKey(Treasury, on_delete=models.PROTECT, related_name="vouchers")
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name="vouchers")
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                limit_choices_to={"is_group": False})
    category = models.ForeignKey(ExpenseCategory, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    rate = models.DecimalField(**RATE, default=1)
    payee = models.CharField(max_length=200, blank=True)  # name on the receipt when there is no party
    description = models.CharField(max_length=255)
    reference = models.CharField(max_length=100, blank=True)  # cheque or transfer number

    class Meta(PostedDocument.Meta):
        pass

    @property
    def PREFIX(self):  # noqa: N802
        return "RV" if self.kind == VoucherKind.RECEIPT else "PV"

    @property
    def allocated(self):
        return self.allocations.aggregate(s=Sum("amount"))["s"] or ZERO


class VoucherAllocation(models.Model):
    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name="allocations")
    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["voucher", "bill"], name="one_allocation_per_bill")]


# ======================================================================= custody and advances
class CustodyKind(models.TextChoices):
    CUSTODY = "custody", tr_lazy("ck_custody")
    ADVANCE = "advance", tr_lazy("ck_advance")


class Custody(TimeStamped):
    number = models.CharField(max_length=30, unique=True, editable=False)
    kind = models.CharField(max_length=10, choices=CustodyKind.choices, default=CustodyKind.CUSTODY)
    holder = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="custodies")
    date = models.DateField(default=timezone.localdate)
    purpose = models.CharField(max_length=255)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_by = by_user()

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "custodies"

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            from .engine import next_number

            self.number = next_number("CU", self.date)
        super().save(*args, **kwargs)

    def account(self):
        s = LedgerSettings.get()
        return s.acct("custody_account" if self.kind == CustodyKind.CUSTODY else "employee_advances")

    def figures(self):
        rows = {r["kind"]: r["s"] for r in self.movements.filter(status=DocStatus.POSTED)
                .values("kind").annotate(s=Sum("amount"))}
        issued = rows.get("issue", ZERO) or ZERO
        spent = rows.get("expense", ZERO) or ZERO
        returned = rows.get("return", ZERO) or ZERO
        return {"issued": issued, "spent": spent, "returned": returned, "remaining": issued - spent - returned}


class MovementKind(models.TextChoices):
    ISSUE = "issue", tr_lazy("mk_issue")
    EXPENSE = "expense", tr_lazy("mk_expense")
    RETURN = "return", tr_lazy("mk_return")


class CustodyMovement(PostedDocument):
    PREFIX = "CM"
    custody = models.ForeignKey(Custody, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=10, choices=MovementKind.choices)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    treasury = models.ForeignKey(Treasury, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    category = models.ForeignKey(ExpenseCategory, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                limit_choices_to={"is_group": False})
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)

    class Meta(PostedDocument.Meta):
        ordering = ["date", "id"]


# ======================================================================= fixed assets
class DepMethod(models.TextChoices):
    STRAIGHT = "straight", tr_lazy("dm_straight")
    DECLINING = "declining", tr_lazy("dm_declining")


class AssetCategory(TimeStamped, Named):
    asset_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+",
                                      limit_choices_to={"is_group": False, "type": "asset"})
    accumulated_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+",
                                            limit_choices_to={"is_group": False, "type": "asset"})
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+",
                                        limit_choices_to={"is_group": False, "type": "expense"})
    life_months = models.PositiveIntegerField(default=60, validators=[MinValueValidator(1)])
    method = models.CharField(max_length=10, choices=DepMethod.choices, default=DepMethod.STRAIGHT)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name_ar"]
        verbose_name_plural = "asset categories"


class AssetStatus(models.TextChoices):
    DRAFT = "draft", tr_lazy("st_draft")
    ACTIVE = "active", tr_lazy("as_active")
    DEPRECIATED = "depreciated", tr_lazy("as_depreciated")
    DISPOSED = "disposed", tr_lazy("as_disposed")


class FundingKind(models.TextChoices):
    TREASURY = "treasury", tr_lazy("fk_treasury")
    SUPPLIER = "supplier", tr_lazy("fk_supplier")
    OPENING = "opening", tr_lazy("fk_opening")


class FixedAsset(TimeStamped, Named):
    code = models.CharField(max_length=20, unique=True, editable=False)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="assets")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    purchase_date = models.DateField(default=timezone.localdate)
    in_service_date = models.DateField(default=timezone.localdate)
    cost = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    salvage_value = models.DecimalField(**MONEY, default=0, validators=[MinValueValidator(0)])
    life_months = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    method = models.CharField(max_length=10, choices=DepMethod.choices, default=DepMethod.STRAIGHT)
    declining_rate = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                         validators=[MinValueValidator(0), MaxValueValidator(100)])
    # opening accumulated depreciation for assets bought before the system started
    opening_accumulated = models.DecimalField(**MONEY, default=0, validators=[MinValueValidator(0)])
    funding = models.CharField(max_length=10, choices=FundingKind.choices, default=FundingKind.TREASURY)
    treasury = models.ForeignKey(Treasury, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    supplier = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name="assets")
    serial_number = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=AssetStatus.choices, default=AssetStatus.DRAFT)
    depreciated_to = models.DateField(null=True, blank=True)  # last month-end already depreciated
    disposed_on = models.DateField(null=True, blank=True)
    created_by = by_user()

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.name}"

    def save(self, *args, **kwargs):
        if not self.code:
            from .engine import next_number

            self.code = next_number("FA", yearly=False, width=5)
        super().save(*args, **kwargs)

    def accumulated(self, as_of=None):
        qs = self.events.filter(kind="depreciation")
        if as_of:
            qs = qs.filter(date__lte=as_of)
        return (qs.aggregate(s=Sum("amount"))["s"] or ZERO) + self.opening_accumulated

    def total_cost(self, as_of=None):
        qs = self.events.filter(kind__in=["acquisition", "improvement"])
        if as_of:
            qs = qs.filter(date__lte=as_of)
        return qs.aggregate(s=Sum("amount"))["s"] or ZERO

    def book_value(self, as_of=None):
        return self.total_cost(as_of) - self.accumulated(as_of)


class AssetEventKind(models.TextChoices):
    ACQUISITION = "acquisition", tr_lazy("ae_acquisition")
    IMPROVEMENT = "improvement", tr_lazy("ae_improvement")
    DEPRECIATION = "depreciation", tr_lazy("ae_depreciation")
    DISPOSAL = "disposal", tr_lazy("ae_disposal")


class DepreciationRun(TimeStamped):
    number = models.CharField(max_length=30, unique=True, editable=False)
    date = models.DateField()  # month-end up to which assets were depreciated
    entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    total = models.DecimalField(**MONEY, default=0)
    is_reversed = models.BooleanField(default=False)
    created_by = by_user()

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            from .engine import next_number

            self.number = next_number("DEP", self.date)
        super().save(*args, **kwargs)


class AssetEvent(models.Model):
    asset = models.ForeignKey(FixedAsset, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=12, choices=AssetEventKind.choices)
    date = models.DateField()
    amount = models.DecimalField(**MONEY, default=0)
    months = models.PositiveIntegerField(default=0)  # months covered (depreciation) or added (improvement)
    proceeds = models.DecimalField(**MONEY, default=0)  # disposal
    description = models.CharField(max_length=255, blank=True)
    entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    run = models.ForeignKey(DepreciationRun, null=True, blank=True, on_delete=models.CASCADE,
                            related_name="events")
    created_by = by_user()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]
