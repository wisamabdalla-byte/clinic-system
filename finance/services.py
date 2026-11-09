"""Money rules kept in one place: monthly doctor figures, closing, balances."""
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import ZERO, DoctorPayout, DoctorSettlement, Payment, money, month_start, next_month


@dataclass
class MonthFigures:
    doctor: object
    period: object
    payments: list = field(default_factory=list)
    collected: Decimal = ZERO
    material: Decimal = ZERO
    net: Decimal = ZERO
    doctor_share: Decimal = ZERO
    settlement: object = None

    @property
    def is_closed(self):
        return self.settlement is not None


def open_payments(doctor, period):
    """Payments on this doctor's invoices in the month that are not locked yet."""
    return (Payment.objects.filter(invoice__doctor=doctor, settlement__isnull=True,
                                   date__gte=period, date__lt=next_month(period))
            .select_related("invoice", "invoice__patient", "method").order_by("date", "id"))


def month_figures(doctor, period):
    period = month_start(period)
    settlement = DoctorSettlement.objects.filter(doctor=doctor, period=period).first()
    fig = MonthFigures(doctor=doctor, period=period, settlement=settlement)
    if settlement:
        fig.payments = list(settlement.payments.select_related("invoice", "invoice__patient", "method")
                            .order_by("date", "id"))
        for p in fig.payments:
            p.calc_material, p.calc_net, p.calc_share = p.material_part, p.amount - p.material_part, p.doctor_share
        fig.collected, fig.material = settlement.collected, settlement.material
        fig.net, fig.doctor_share = settlement.net, settlement.doctor_share
        return fig
    fig.payments = list(open_payments(doctor, period))
    for p in fig.payments:
        p.calc_material, p.calc_net, p.calc_share = p.share_breakdown()
        fig.collected += p.amount
        fig.material += p.calc_material
        fig.net += p.calc_net
        fig.doctor_share += p.calc_share
    return fig


def close_month(doctor, period, user, adjustment=ZERO, note=""):
    period = month_start(period)
    with transaction.atomic():
        if DoctorSettlement.objects.select_for_update().filter(doctor=doctor, period=period).exists():
            raise ValueError("already closed")
        fig = month_figures(doctor, period)
        s = DoctorSettlement.objects.create(
            doctor=doctor, period=period, collected=money(fig.collected), material=money(fig.material),
            net=money(fig.net), doctor_share=money(fig.doctor_share), adjustment=money(adjustment),
            adjustment_note=note[:255], total_due=money(fig.doctor_share + (adjustment or ZERO)),
            closed_by=user,
        )
        for p in fig.payments:
            p.settlement = s
            p.material_part = p.calc_material
            p.doctor_share = p.calc_share
            p.save(update_fields=["settlement", "material_part", "doctor_share", "updated_at"])
        return s


def reopen_month(settlement):
    with transaction.atomic():
        settlement.payments.update(settlement=None, material_part=0, doctor_share=0)
        settlement.delete()


def is_month_closed(doctor, day):
    return DoctorSettlement.objects.filter(doctor=doctor, period=month_start(day)).exists()


def doctor_balance(doctor):
    """What the clinic still owes the doctor from closed months."""
    due = DoctorSettlement.objects.filter(doctor=doctor).aggregate(s=Sum("total_due"))["s"] or ZERO
    paid = DoctorPayout.objects.filter(doctor=doctor).aggregate(s=Sum("amount"))["s"] or ZERO
    return money(due), money(paid), money(due - paid)
