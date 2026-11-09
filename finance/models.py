"""Finance (stage 4): invoices, partial payments, doctor shares, monthly closing,
payouts to doctors and clinic expenses.

How a doctor's share is worked out
----------------------------------
Each invoice belongs to one doctor and keeps a copy of that doctor's percentage
at the time it was written (so changing a doctor's % later never rewrites history).

For every payment the patient makes on that invoice:

    net part of the payment = payment × (invoice total − material cost) ÷ invoice total
    doctor share            = net part × doctor %

So material cost is taken off before the split, and partial payments earn the
doctor a share only when the money is actually collected. At the end of each
month the accountant closes the month per doctor: the payments of that month are
locked into a DoctorSettlement and can no longer be changed.
"""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from core.i18n import tr_lazy
from core.models import MONEY, Branch, PaymentMethod, Service, TimeStamped

ZERO = Decimal("0")
CENT = Decimal("0.01")
PERCENT = {"max_digits": 5, "decimal_places": 2,
           "validators": [MinValueValidator(0), MaxValueValidator(100)]}


def money(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def month_start(d):
    return date(d.year, d.month, 1)


def next_month(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


class Invoice(TimeStamped):
    number = models.CharField(max_length=20, unique=True, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="invoices")
    patient = models.ForeignKey("clinic.Patient", on_delete=models.PROTECT, related_name="invoices")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invoices")
    appointment = models.ForeignKey(
        "clinic.Appointment", null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices"
    )
    date = models.DateField(default=timezone.localdate, db_index=True)
    discount = models.DecimalField(**MONEY, default=0, validators=[MinValueValidator(0)])
    doctor_percent = models.DecimalField(**PERCENT, default=0)
    notes = models.TextField(blank=True)
    is_cancelled = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Kept up to date by recalc() so lists and reports stay fast.
    subtotal = models.DecimalField(**MONEY, default=0, editable=False)
    total = models.DecimalField(**MONEY, default=0, editable=False)
    material_cost = models.DecimalField(**MONEY, default=0, editable=False)
    paid = models.DecimalField(**MONEY, default=0, editable=False)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.number or "—"

    def save(self, *args, **kwargs):
        if not self.number:
            with transaction.atomic():
                super().save(*args, **kwargs)
                self.number = f"INV-{self.pk:06d}"
                super().save(update_fields=["number"])
            return
        super().save(*args, **kwargs)

    @property
    def balance(self):
        return max(self.total - self.paid, ZERO)

    @property
    def state(self):
        if self.is_cancelled:
            return "cancelled"
        if self.total > 0 and self.paid >= self.total:
            return "paid"
        return "partial" if self.paid > 0 else "unpaid"

    @property
    def net_ratio(self):
        """Share of each payment left after material cost (0..1)."""
        if self.total <= 0:
            return ZERO
        return max(self.total - self.material_cost, ZERO) / self.total

    @property
    def is_locked(self):
        """True once any payment is inside a closed month."""
        return self.payments.filter(settlement__isnull=False).exists()

    def recalc(self):
        items = list(self.items.all())
        self.subtotal = money(sum((i.line_total for i in items), ZERO))
        self.material_cost = money(sum((i.material_cost for i in items), ZERO))
        self.total = money(max(self.subtotal - self.discount, ZERO))
        self.paid = money(self.payments.aggregate(s=Sum("amount"))["s"])
        self.save(update_fields=["subtotal", "material_cost", "total", "paid", "updated_at"])


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(Service, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=200, blank=True)
    tooth = models.CharField(max_length=20, blank=True)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(**MONEY, validators=[MinValueValidator(0)])
    material_cost = models.DecimalField(**MONEY, default=0, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ["id"]

    @property
    def line_total(self):
        return (self.unit_price or ZERO) * (self.quantity or 0)

    @property
    def label(self):
        return self.description or (self.service.name if self.service else "—")


class DoctorSettlement(TimeStamped):
    """One closed month for one doctor."""

    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="settlements")
    period = models.DateField(help_text="First day of the month")
    collected = models.DecimalField(**MONEY, default=0)
    material = models.DecimalField(**MONEY, default=0)
    net = models.DecimalField(**MONEY, default=0)
    doctor_share = models.DecimalField(**MONEY, default=0)
    adjustment = models.DecimalField(**MONEY, default=0)  # + bonus / − deduction
    adjustment_note = models.CharField(max_length=255, blank=True)
    total_due = models.DecimalField(**MONEY, default=0)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-period", "doctor__first_name"]
        constraints = [models.UniqueConstraint(fields=["doctor", "period"], name="one_settlement_per_month")]

    def __str__(self):
        return f"{self.doctor} · {self.period:%Y-%m}"

    @property
    def paid_out(self):
        return money(self.payouts.aggregate(s=Sum("amount"))["s"])


class Payment(TimeStamped):
    """Money received from a patient against an invoice (can be partial)."""

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField(default=timezone.localdate, db_index=True)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="payments")
    reference = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    settlement = models.ForeignKey(
        DoctorSettlement, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments"
    )
    # Frozen when the month is closed.
    material_part = models.DecimalField(**MONEY, default=0)
    doctor_share = models.DecimalField(**MONEY, default=0)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.invoice.number} · {self.amount}"

    def share_breakdown(self):
        """(material part, net, doctor share) for this payment, worked out live."""
        inv = self.invoice
        net = money(self.amount * inv.net_ratio)
        share = money(net * inv.doctor_percent / 100)
        return money(self.amount - net), net, share


class DoctorPayout(TimeStamped):
    """Money the clinic paid to a doctor."""

    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payouts")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="payouts")
    settlement = models.ForeignKey(
        DoctorSettlement, null=True, blank=True, on_delete=models.SET_NULL, related_name="payouts"
    )
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="payouts")
    reference = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.doctor} · {self.amount}"


class ExpenseCategory(models.TextChoices):
    RENT = "rent", tr_lazy("exp_rent")
    SALARIES = "salaries", tr_lazy("exp_salaries")
    MATERIALS = "materials", tr_lazy("exp_materials")
    LAB = "lab", tr_lazy("exp_lab")
    UTILITIES = "utilities", tr_lazy("exp_utilities")
    MAINTENANCE = "maintenance", tr_lazy("exp_maintenance")
    MARKETING = "marketing", tr_lazy("exp_marketing")
    OTHER = "other", tr_lazy("exp_other")


class Expense(TimeStamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="expenses")
    date = models.DateField(default=timezone.localdate, db_index=True)
    category = models.CharField(max_length=20, choices=ExpenseCategory.choices)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="expenses")
    description = models.CharField(max_length=255)
    reference = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.description
