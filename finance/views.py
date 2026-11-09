from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from accounts.models import User
from accounts.roles import Role, has_perm
from clinic.models import Appointment, Patient
from core.audit import log
from core.i18n import tr
from core.mixins import Col, CrudFormMixin, CrudListView, PermissionMixin
from core.models import PaymentMethod, PriceList, Service

from .forms import (
    CloseMonthForm, ExpenseForm, FirstPaymentForm, InvoiceForm, ItemFormSet, PaymentForm, PayoutForm,
)
from .models import (
    ZERO, DoctorPayout, DoctorSettlement, Expense, ExpenseCategory, Invoice, Payment, money,
    month_start, next_month,
)
from .services import close_month, doctor_balance, month_figures, reopen_month

STATE_STYLE = {"paid": "badge-good", "partial": "badge-accent", "unpaid": "badge-bad", "cancelled": "badge-muted"}
STATES = ["unpaid", "partial", "paid", "cancelled"]


def parse_month(value):
    try:
        y, m = (int(x) for x in (value or "").split("-")[:2])
        return date(y, m, 1)
    except (ValueError, TypeError):
        return month_start(timezone.localdate())


def parse_date(value, default=None):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return default or timezone.localdate()


def month_nav(period, url_name, **extra):
    prev = month_start(period - timedelta(days=1))
    nxt = next_month(period)
    base = reverse(url_name)
    q = "".join(f"&{k}={v}" for k, v in extra.items() if v)
    return {
        "period": period,
        "prev_url": f"{base}?month={prev:%Y-%m}{q}",
        "next_url": f"{base}?month={nxt:%Y-%m}{q}",
        "this_url": f"{base}?month={timezone.localdate():%Y-%m}{q}",
        "is_current": period == month_start(timezone.localdate()),
    }


def price_tables():
    """Service prices for every price list, so the invoice form can fill prices for the chosen patient."""
    services = list(Service.objects.filter(is_active=True))
    base = {str(s.pk): str(s.price) for s in services}
    lists = {}
    for pl in PriceList.objects.filter(is_active=True).prefetch_related("items"):
        special = {i.service_id: i.price for i in pl.items.all()}
        lists[str(pl.pk)] = {str(s.pk): str(pl.price_for(s, special.get(s.pk))) for s in services}
    default = PriceList.objects.filter(is_active=True, is_default=True).first()
    return {"base": base, "lists": lists, "default": str(default.pk) if default else ""}


def patient_price_list(patient):
    if patient and patient.price_list_id and patient.price_list.is_active:
        return patient.price_list
    return PriceList.objects.filter(is_active=True, is_default=True).first()


def decorate_invoice(inv):
    inv.state_label = tr(f"inv_{inv.state}")
    inv.state_class = STATE_STYLE[inv.state]
    return inv


# ======================================================================= invoices
class InvoiceList(CrudListView):
    module = "invoices"
    title_key, subtitle_key = "nav_invoices", "invoices_sub"
    search_fields = ["number", "patient__full_name", "patient__phone", "patient__file_no"]
    has_status = False
    create_url = "invoice_create"
    row_actions = [("open", "invoice_detail", "clipboard", "invoices.view")]
    columns = [
        Col("invoice_no", lambda o: o.number, "title", sub=lambda o: o.date.strftime("%Y-%m-%d")),
        Col("patient", lambda o: o.patient.full_name, sub=lambda o: o.patient.file_no),
        Col("doctor", lambda o: str(o.doctor)),
        Col("total", lambda o: o.total, "money"),
        Col("paid", lambda o: o.paid, "money-good"),
        Col("balance", lambda o: o.balance, "money"),
        Col("status", lambda o: tr(f"inv_{o.state}"), "badge"),
    ]

    def get_queryset(self):
        self.queryset = Invoice.objects.filter(branch=self.request.branch).select_related("patient", "doctor")
        self.filters = [("doctor", "doctor_id", [(d.pk, str(d)) for d in User.objects.filter(role=Role.DOCTOR)])]
        qs = super().get_queryset()
        state = self.request.GET.get("state", "")
        if state == "cancelled":
            qs = qs.filter(is_cancelled=True)
        elif state == "paid":
            qs = qs.filter(is_cancelled=False, total__gt=0, paid__gte=F("total"))
        elif state == "partial":
            qs = qs.filter(is_cancelled=False, paid__gt=0, paid__lt=F("total"))
        elif state == "unpaid":
            qs = qs.filter(is_cancelled=False, paid=0)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_controls"].insert(0, {
            "param": "state", "value": self.request.GET.get("state", ""), "label": tr("status"),
            "choices": [(s, tr(f"inv_{s}")) for s in STATES],
        })
        return ctx


class InvoiceFormBase(PermissionMixin, TemplateView):
    module, action = "invoices", "edit"
    template_name = "finance/invoice_form.html"
    invoice = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.branch is None:
            messages.error(request, tr("need_branch"))
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def build(self, data=None):
        raise NotImplementedError

    def render_forms(self, form, formset, payform=None, **extra):
        patient = None
        pid = form["patient"].value()
        if pid:
            patient = Patient.objects.filter(pk=pid).select_related("price_list").first()
        pl = patient_price_list(patient)
        doctors = form.fields["doctor"].queryset
        ctx = self.get_context_data(
            form=form, formset=formset, payform=payform, patient=patient,
            patient_list=pl.pk if pl else "",
            prices=price_tables(),
            doctor_percents={str(d.pk): str(d.commission_percent) for d in doctors},
            fields=[form[f] for f in ("doctor", "date", "discount", "notes")],
            back_url=self.back_url(), invoice=self.invoice, **extra,
        )
        return self.render_to_response(ctx)

    def back_url(self):
        if self.invoice:
            return reverse("invoice_detail", args=[self.invoice.pk])
        return reverse("invoice_list")


class InvoiceCreate(InvoiceFormBase):
    def get(self, request):
        initial = {"date": timezone.localdate()}
        items_initial = []
        appt = None
        if request.GET.get("appointment"):
            appt = Appointment.objects.filter(pk=request.GET["appointment"]).select_related(
                "patient", "service", "doctor").first()
        if appt:
            initial.update(patient=appt.patient_id, doctor=appt.doctor_id,
                           date=timezone.localdate(appt.start))
            if appt.service:
                pl = patient_price_list(appt.patient)
                price = pl.price_for(appt.service) if pl else appt.service.price
                items_initial.append({"service": appt.service.pk, "quantity": 1, "unit_price": price})
        elif request.GET.get("plan"):
            from records.models import TreatmentPlan

            plan = TreatmentPlan.objects.filter(pk=request.GET["plan"]).select_related("patient").first()
            if plan:
                initial.update(patient=plan.patient_id, doctor=plan.doctor_id)
                items = [i for i in plan.items.all() if i.is_done] or list(plan.items.all())
                items_initial = [{"service": i.service_id, "tooth": i.tooth, "quantity": i.quantity,
                                  "unit_price": i.price} for i in items]
        elif request.GET.get("patient"):
            initial["patient"] = request.GET["patient"]
        if "doctor" not in initial and request.user.role == Role.DOCTOR:
            initial["doctor"] = request.user.pk
        form = InvoiceForm(initial=initial, branch=request.branch)
        formset = ItemFormSet(prefix="items", instance=Invoice(), initial=items_initial)
        formset.extra = max(1, len(items_initial))
        return self.render_forms(form, formset, FirstPaymentForm(prefix="pay"),
                                 appointment=appt, title=f"{tr('add')} {tr('ent_invoice')}")

    def post(self, request):
        form = InvoiceForm(request.POST, branch=request.branch)
        formset = ItemFormSet(request.POST, prefix="items", instance=Invoice())
        payform = FirstPaymentForm(request.POST, prefix="pay")
        appt = Appointment.objects.filter(pk=request.POST.get("appointment") or 0).first()
        if form.is_valid() and formset.is_valid() and payform.is_valid():
            with transaction.atomic():
                inv = form.save(commit=False)
                inv.branch = request.branch
                inv.created_by = request.user
                inv.appointment = appt
                inv.doctor_percent = inv.doctor.commission_percent
                inv.save()
                formset.instance = inv
                formset.save()
                inv.recalc()
                amount = payform.cleaned_data.get("amount")
                if amount:
                    if amount > inv.balance:
                        transaction.set_rollback(True)
                        payform.add_error("amount", tr("amount_over_balance", f"{inv.balance:,.2f}"))
                        return self.invalid(form, formset, payform, appt)
                    Payment.objects.create(
                        invoice=inv, branch=request.branch, date=inv.date, amount=amount,
                        method=payform.cleaned_data["method"], reference=payform.cleaned_data["reference"],
                        received_by=request.user,
                    )
                    inv.recalc()
            log(request, "create", inv)
            messages.success(request, tr("invoice_saved", inv.number))
            return redirect("invoice_detail", inv.pk)
        return self.invalid(form, formset, payform, appt)

    def invalid(self, form, formset, payform, appt):
        messages.error(self.request, tr("fix_errors"))
        return self.render_forms(form, formset, payform, appointment=appt,
                                 title=f"{tr('add')} {tr('ent_invoice')}")


class InvoiceUpdate(InvoiceFormBase):
    def setup_invoice(self, pk):
        self.invoice = get_object_or_404(Invoice, pk=pk)
        if self.invoice.is_cancelled or self.invoice.is_locked:
            messages.error(self.request, tr("invoice_locked"))
            return False
        return True

    def get(self, request, pk):
        if not self.setup_invoice(pk):
            return redirect("invoice_detail", pk)
        form = InvoiceForm(instance=self.invoice, branch=self.invoice.branch)
        formset = ItemFormSet(prefix="items", instance=self.invoice)
        return self.render_forms(form, formset, title=f"{tr('edit')} {self.invoice.number}")

    def post(self, request, pk):
        if not self.setup_invoice(pk):
            return redirect("invoice_detail", pk)
        old_doctor = self.invoice.doctor_id
        form = InvoiceForm(request.POST, instance=self.invoice, branch=self.invoice.branch)
        formset = ItemFormSet(request.POST, prefix="items", instance=self.invoice)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                inv = form.save(commit=False)
                if inv.doctor_id != old_doctor:
                    inv.doctor_percent = inv.doctor.commission_percent
                inv.save()
                formset.save()
                inv.recalc()
                if inv.paid > inv.total:
                    transaction.set_rollback(True)
                    messages.error(request, tr("total_below_paid", f"{inv.paid:,.2f}"))
                    return self.render_forms(form, formset, title=f"{tr('edit')} {self.invoice.number}")
            log(request, "update", inv, form.changed_data + (["items"] if formset.has_changed() else []))
            messages.success(request, tr("saved"))
            return redirect("invoice_detail", inv.pk)
        messages.error(request, tr("fix_errors"))
        return self.render_forms(form, formset, title=f"{tr('edit')} {self.invoice.number}")


class InvoiceDetail(PermissionMixin, DetailView):
    module = "invoices"
    model = Invoice
    template_name = "finance/invoice_detail.html"

    def get_queryset(self):
        return Invoice.objects.select_related("patient", "doctor", "branch", "created_by", "appointment")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        inv = decorate_invoice(self.object)
        user = self.request.user
        payments = list(inv.payments.select_related("method", "received_by", "settlement"))
        for p in payments:
            p.calc_material, p.calc_net, p.calc_share = p.share_breakdown()
        can_edit = has_perm(user, "invoices", "edit")
        ctx.update(
            title=inv.number,
            items=inv.items.select_related("service"),
            payments=payments,
            can_edit=can_edit and not inv.is_cancelled and not inv.is_locked,
            can_pay=can_edit and not inv.is_cancelled and inv.balance > 0,
            can_cancel=can_edit and not inv.is_cancelled and inv.paid == 0,
            can_void=has_perm(user, "invoices", "void"),
            can_see_share=has_perm(user, "doctor_accounts", "edit"),
            payform=kwargs.get("payform") or PaymentForm(invoice=inv, initial={
                "date": timezone.localdate(), "amount": inv.balance}),
            net_total=money(inv.total - inv.material_cost) if inv.total > inv.material_cost else ZERO,
            doctor_total=money(max(inv.total - inv.material_cost, ZERO) * inv.doctor_percent / 100),
        )
        return ctx


class InvoicePrint(InvoiceDetail):
    template_name = "finance/invoice_print.html"


class PaymentCreate(PermissionMixin, View):
    module, action = "invoices", "edit"

    def post(self, request, pk):
        inv = get_object_or_404(Invoice, pk=pk)
        if inv.is_cancelled:
            raise PermissionDenied
        form = PaymentForm(request.POST, invoice=inv)
        if form.is_valid():
            with transaction.atomic():
                p = form.save(commit=False)
                p.invoice, p.branch, p.received_by = inv, request.branch or inv.branch, request.user
                p.save()
                inv.recalc()
            log(request, "create", p)
            messages.success(request, tr("payment_saved", f"{p.amount:,.2f}"))
            return redirect("invoice_detail", inv.pk)
        messages.error(request, tr("fix_errors"))
        view = InvoiceDetail()
        view.setup(request, pk=pk)
        view.object = inv
        return view.render_to_response(view.get_context_data(payform=form))


class PaymentVoid(PermissionMixin, View):
    module, action = "invoices", "void"

    def post(self, request, pk):
        p = get_object_or_404(Payment.objects.select_related("invoice"), pk=pk)
        if p.settlement_id:
            messages.error(request, tr("payment_locked"))
        else:
            inv = p.invoice
            log(request, "update", inv, [f"void payment {p.amount} ({p.date})"])
            with transaction.atomic():
                p.delete()
                inv.recalc()
            messages.success(request, tr("payment_voided"))
        return redirect("invoice_detail", p.invoice_id)


class InvoiceCancel(PermissionMixin, View):
    module, action = "invoices", "edit"

    def post(self, request, pk):
        inv = get_object_or_404(Invoice, pk=pk)
        if inv.paid > 0 or inv.is_cancelled:
            messages.error(request, tr("cancel_needs_no_payments"))
        else:
            inv.is_cancelled = True
            inv.cancel_reason = request.POST.get("reason", "")[:255]
            inv.save(update_fields=["is_cancelled", "cancel_reason", "updated_at"])
            log(request, "status", inv, ["cancelled"])
            messages.success(request, tr("invoice_cancelled"))
        return redirect("invoice_detail", inv.pk)


# ======================================================================= doctor accounts
def visible_doctors(user):
    qs = User.objects.filter(role=Role.DOCTOR)
    if user.role == Role.DOCTOR and not user.is_admin:
        qs = qs.filter(pk=user.pk)
    return qs


class DoctorAccounts(PermissionMixin, TemplateView):
    """Month view: what each doctor earned, and closing the month."""

    module = "doctor_accounts"
    template_name = "finance/doctor_accounts.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        period = parse_month(self.request.GET.get("month"))
        user = self.request.user
        rows = []
        totals = {"collected": ZERO, "material": ZERO, "net": ZERO, "share": ZERO}
        for d in visible_doctors(user):
            fig = month_figures(d, period)
            if not fig.payments and not fig.settlement and not d.is_active:
                continue
            due, paid, balance = doctor_balance(d)
            rows.append({"doctor": d, "fig": fig, "balance": balance})
            totals["collected"] += fig.collected
            totals["material"] += fig.material
            totals["net"] += fig.net
            totals["share"] += fig.doctor_share
        ctx.update(month_nav(period, "doctor_accounts"))
        ctx.update(title=tr("nav_doctor_accounts"), rows=rows, totals=totals,
                   can_close=has_perm(user, "doctor_accounts", "edit"),
                   month_value=f"{period:%Y-%m}")
        return ctx


class DoctorMonth(PermissionMixin, TemplateView):
    module = "doctor_accounts"
    template_name = "finance/doctor_month.html"

    def doctor(self, pk):
        return get_object_or_404(visible_doctors(self.request.user), pk=pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        doctor = self.doctor(kwargs["pk"])
        period = parse_month(self.request.GET.get("month"))
        fig = month_figures(doctor, period)
        user = self.request.user
        base = reverse("doctor_month", args=[doctor.pk])
        prev = month_start(period - timedelta(days=1))
        ctx.update(
            title=f"{doctor} · {period:%Y-%m}", doctor=doctor, fig=fig, period=period,
            prev_url=f"{base}?month={prev:%Y-%m}", next_url=f"{base}?month={next_month(period):%Y-%m}",
            can_close=has_perm(user, "doctor_accounts", "edit") and not fig.is_closed,
            can_reopen=has_perm(user, "doctor_accounts", "reopen") and fig.is_closed,
            close_form=kwargs.get("close_form") or CloseMonthForm(),
            month_ended=next_month(period) <= timezone.localdate(),
        )
        return ctx

    def post(self, request, pk):
        if not has_perm(request.user, "doctor_accounts", "edit"):
            raise PermissionDenied
        doctor = self.doctor(pk)
        period = parse_month(request.POST.get("month"))
        url = f"{reverse('doctor_month', args=[pk])}?month={period:%Y-%m}"
        if request.POST.get("do") == "reopen":
            if not has_perm(request.user, "doctor_accounts", "reopen"):
                raise PermissionDenied
            s = DoctorSettlement.objects.filter(doctor=doctor, period=period).first()
            if s:
                log(request, "update", s, ["reopened"])
                reopen_month(s)
                messages.success(request, tr("month_reopened"))
            return redirect(url)
        form = CloseMonthForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(pk=pk, close_form=form))
        try:
            s = close_month(doctor, period, request.user, form.cleaned_data.get("adjustment") or ZERO,
                            form.cleaned_data.get("adjustment_note") or "")
        except ValueError:
            messages.error(request, tr("month_already_closed"))
            return redirect(url)
        log(request, "create", s)
        messages.success(request, tr("month_closed_ok", f"{s.total_due:,.2f}"))
        return redirect(url)


class DoctorStatement(PermissionMixin, TemplateView):
    """All closed months and payouts for one doctor, with the running balance."""

    module = "doctor_accounts"
    template_name = "finance/doctor_statement.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        doctor = get_object_or_404(visible_doctors(self.request.user), pk=kwargs["pk"])
        due, paid, balance = doctor_balance(doctor)
        lines = []
        for s in doctor.settlements.all():
            lines.append({"date": next_month(s.period) - timedelta(days=1), "kind": "settlement", "obj": s,
                          "label": tr("month_share", f"{s.period:%Y-%m}"), "credit": s.total_due, "debit": ZERO})
        for p in doctor.payouts.select_related("method"):
            lines.append({"date": p.date, "kind": "payout", "obj": p,
                          "label": f"{tr('payout')} · {p.method.name}", "credit": ZERO, "debit": p.amount})
        lines.sort(key=lambda r: (r["date"], r["kind"] == "payout"))
        running = ZERO
        for r in lines:
            running += r["credit"] - r["debit"]
            r["balance"] = running
        ctx.update(title=str(doctor), doctor=doctor, due=due, paid=paid, balance=balance,
                   lines=lines[::-1], can_pay=has_perm(self.request.user, "payouts", "edit"))
        return ctx


class PayoutList(CrudListView):
    module = "payouts"
    title_key, subtitle_key = "nav_payouts", "payouts_sub"
    has_status = False
    create_url = "payout_create"
    search_fields = ["doctor__first_name", "doctor__last_name", "reference", "notes"]
    columns = [
        Col("date", lambda o: o.date, "date"),
        Col("doctor", lambda o: str(o.doctor), "title", sub=lambda o: o.notes),
        Col("amount", lambda o: o.amount, "money"),
        Col("payment_method", lambda o: o.method.name, "badge"),
        Col("reference", lambda o: o.reference, "ltr"),
        Col("paid_by", lambda o: str(o.paid_by or "—"), "muted"),
    ]

    def get_queryset(self):
        self.queryset = DoctorPayout.objects.select_related("doctor", "method", "paid_by")
        self.filters = [("doctor", "doctor_id", [(d.pk, str(d)) for d in User.objects.filter(role=Role.DOCTOR)])]
        return super().get_queryset()


class PayoutCreate(CrudFormMixin, CreateView):
    module, model, form_class = "payouts", DoctorPayout, PayoutForm
    entity_key, list_url = "ent_payout", "payout_list"

    def get_initial(self):
        initial = super().get_initial()
        initial["date"] = timezone.localdate()
        doctor = self.request.GET.get("doctor")
        if doctor:
            initial["doctor"] = doctor
            d = User.objects.filter(pk=doctor).first()
            if d:
                _, _, balance = doctor_balance(d)
                if balance > 0:
                    initial["amount"] = balance
        return initial

    def form_valid(self, form):
        form.instance.paid_by = self.request.user
        form.instance.branch = self.request.branch
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("doctor_statement", args=[self.object.doctor_id])


# ======================================================================= expenses
class ExpenseList(CrudListView):
    module = "expenses"
    title_key, subtitle_key = "nav_expenses", "expenses_sub"
    has_status = False
    create_url, edit_url = "expense_create", "expense_edit"
    search_fields = ["description", "reference"]
    filters = [("category", "category", ExpenseCategory.choices)]
    columns = [
        Col("date", lambda o: o.date, "date"),
        Col("description", lambda o: o.description, "title", sub=lambda o: o.reference),
        Col("expense_category", lambda o: o.get_category_display(), "badge"),
        Col("amount", lambda o: o.amount, "money"),
        Col("payment_method", lambda o: o.method.name),
        Col("created_by", lambda o: str(o.created_by or "—"), "muted"),
    ]

    def get_queryset(self):
        self.queryset = Expense.objects.filter(branch=self.request.branch).select_related("method", "created_by")
        return super().get_queryset()


class ExpenseFormMixin(CrudFormMixin):
    module, model, form_class = "expenses", Expense, ExpenseForm
    entity_key, list_url = "ent_expense", "expense_list"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.branch is None:
            messages.error(request, tr("need_branch"))
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)


class ExpenseCreate(ExpenseFormMixin, CreateView):
    def get_initial(self):
        return {**super().get_initial(), "date": timezone.localdate()}

    def form_valid(self, form):
        form.instance.branch = self.request.branch
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class ExpenseUpdate(ExpenseFormMixin, UpdateView):
    def get_queryset(self):
        return Expense.objects.filter(branch=self.request.branch)


# ======================================================================= reports
def by_method(qs, field="amount"):
    return {row["method"]: row["s"] or ZERO for row in qs.values("method").annotate(s=Sum(field))}


class DailyCash(PermissionMixin, TemplateView):
    """The cash box for one day in the current branch, per payment method."""

    module = "cashbox"
    template_name = "finance/daily_cash.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        day = parse_date(self.request.GET.get("date"))
        branch = self.request.branch
        payments = Payment.objects.filter(branch=branch, date=day).select_related(
            "invoice", "invoice__patient", "method", "received_by")
        expenses = Expense.objects.filter(branch=branch, date=day).select_related("method")
        payouts = DoctorPayout.objects.filter(branch=branch, date=day).select_related("method", "doctor")
        pin, eout, dout = by_method(payments), by_method(expenses), by_method(payouts)
        rows = []
        for m in PaymentMethod.objects.all():
            i, e, d = pin.get(m.pk, ZERO), eout.get(m.pk, ZERO), dout.get(m.pk, ZERO)
            if m.is_active or i or e or d:
                rows.append({"method": m, "in": i, "expenses": e, "payouts": d, "net": i - e - d})
        totals = {k: sum((r[k] for r in rows), ZERO) for k in ("in", "expenses", "payouts", "net")}
        ctx.update(
            title=tr("nav_cashbox"), day=day, rows=rows, totals=totals,
            payments=payments, expenses=expenses, payouts=payouts,
            prev_url=f"?date={day - timedelta(days=1):%Y-%m-%d}", next_url=f"?date={day + timedelta(days=1):%Y-%m-%d}",
            today_url=f"?date={timezone.localdate():%Y-%m-%d}", is_today=day == timezone.localdate(),
        )
        return ctx


class MonthlyReport(PermissionMixin, TemplateView):
    module = "finance_reports"
    template_name = "finance/monthly.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        period = parse_month(self.request.GET.get("month"))
        end = next_month(period)
        scope = self.request.GET.get("scope", "branch")
        branch = self.request.branch
        inv = Invoice.objects.filter(date__gte=period, date__lt=end, is_cancelled=False)
        pay = Payment.objects.filter(date__gte=period, date__lt=end)
        exp = Expense.objects.filter(date__gte=period, date__lt=end)
        pout = DoctorPayout.objects.filter(date__gte=period, date__lt=end)
        if scope != "all" and branch:
            inv, pay, exp, pout = (inv.filter(branch=branch), pay.filter(branch=branch),
                                   exp.filter(branch=branch), pout.filter(branch=branch))
        invoiced = inv.aggregate(s=Sum("total"))["s"] or ZERO
        collected = pay.aggregate(s=Sum("amount"))["s"] or ZERO
        material = doctor_share = ZERO
        doctors = OrderedDict()
        for p in pay.select_related("invoice", "invoice__doctor"):
            m, n, s = ((p.material_part, p.amount - p.material_part, p.doctor_share)
                       if p.settlement_id else p.share_breakdown())
            material += m
            doctor_share += s
            row = doctors.setdefault(p.invoice.doctor_id, {"doctor": p.invoice.doctor, "collected": ZERO,
                                                           "material": ZERO, "share": ZERO})
            row["collected"] += p.amount
            row["material"] += m
            row["share"] += s
        for row in doctors.values():
            row["clinic"] = row["collected"] - row["share"]
        expenses_total = exp.aggregate(s=Sum("amount"))["s"] or ZERO
        by_cat = {r["category"]: r["s"] for r in exp.values("category").annotate(s=Sum("amount"))}
        methods = {m.pk: m for m in PaymentMethod.objects.all()}
        ctx.update(month_nav(period, "finance_monthly", scope=scope if scope == "all" else ""))
        ctx.update(
            title=tr("nav_fin_reports"), scope=scope,
            invoiced=invoiced, collected=collected, material=money(material), doctor_share=money(doctor_share),
            expenses_total=expenses_total, payouts_total=pout.aggregate(s=Sum("amount"))["s"] or ZERO,
            clinic_net=money(collected - doctor_share - expenses_total),
            outstanding=Invoice.objects.filter(is_cancelled=False, **({} if scope == "all" else {"branch": branch}))
            .aggregate(s=Sum("total"), p=Sum("paid")),
            doctors=list(doctors.values()),
            categories=[{"label": c.label, "amount": by_cat.get(c.value, ZERO)} for c in ExpenseCategory
                        if by_cat.get(c.value)],
            methods=[{"method": methods[k], "amount": v} for k, v in by_method(pay).items()],
        )
        o = ctx["outstanding"]
        ctx["outstanding"] = max((o["s"] or ZERO) - (o["p"] or ZERO), ZERO)
        return ctx
