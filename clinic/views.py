from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from accounts.models import User
from accounts.roles import STATUS_ACTIONS, Role, has_perm
from core.audit import log
from core.i18n import tr
from core.mixins import Col, CrudFormMixin, CrudListView, PermissionMixin

from .forms import AppointmentForm, PatientForm, doctors_for
from .models import INACTIVE_STATUSES, TRANSITIONS, Appointment, Patient, Status

STATUS_STYLE = {  # badge class per status
    Status.BOOKED: "", Status.CONFIRMED: "badge-info", Status.ARRIVED: "badge-accent",
    Status.IN_PROGRESS: "badge-live", Status.COMPLETED: "badge-good",
    Status.CANCELLED: "badge-muted", Status.NO_SHOW: "badge-bad",
}
ACTION_ICONS = {"confirm": "check", "checkin": "login", "start": "play", "complete": "check-circle",
                "cancel": "x", "noshow": "user-x", "restore": "undo"}


def parse_day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return timezone.localdate()


def safe_next(request, fallback):
    target = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return target
    return fallback


def allowed_actions(user, appt):
    """Status buttons this user may press on this appointment."""
    if not has_perm(user, "appointments", "status"):
        return []
    actions = [a for a in TRANSITIONS if appt.can(a)]
    limit = None if user.is_admin else STATUS_ACTIONS.get(user.role)
    if limit is not None:
        actions = [a for a in actions if a in limit]
    if user.role == Role.DOCTOR and not user.is_admin and appt.doctor_id != user.pk:
        return []
    return actions


def decorate(appt, user):
    appt.status_class = STATUS_STYLE.get(appt.status, "")
    appt.actions = [{"key": a, "label": tr(f"do_{a}"), "icon": ACTION_ICONS[a],
                     "primary": a in ("checkin", "start", "complete")}
                    for a in allowed_actions(user, appt)]
    appt.local_start = timezone.localtime(appt.start)
    appt.local_end = timezone.localtime(appt.end)
    return appt


# ======================================================================= patients
class PatientList(CrudListView):
    module = "patients"
    title_key, subtitle_key = "nav_patients", "patients_sub"
    search_fields = ["full_name", "phone", "phone2", "file_no", "national_id"]
    create_url, edit_url = "patient_create", "patient_edit"
    row_actions = [
        ("open_file", "patient_detail", "clipboard", "patients.view"),
        ("book", "patient_book", "calendar", "appointments.edit"),
    ]
    columns = [
        Col("patient_name", lambda o: o.full_name, "title", sub=lambda o: o.file_no),
        Col("phone", lambda o: o.phone, "ltr"),
        Col("gender", lambda o: o.get_gender_display()),
        Col("age", lambda o: o.age if o.age is not None else "—", "num"),
        Col("patient_price_list", lambda o: o.price_list.name if o.price_list else "", "badge-opt"),
        Col("registered", lambda o: o.created_at, "date"),
    ]

    def get_queryset(self):
        self.queryset = Patient.objects.select_related("price_list")
        return super().get_queryset()


class PatientFormMixin(CrudFormMixin):
    module, model, form_class = "patients", Patient, PatientForm
    entity_key, list_url = "ent_patient", "patient_list"

    def get_success_url(self):
        then = self.request.POST.get("then")
        if then == "book":
            return f"{reverse('appointment_create')}?patient={self.object.pk}"
        if then == "invoice":
            return f"{reverse('invoice_create')}?patient={self.object.pk}"
        return reverse("patient_detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ctx["form"]
        groups = [
            ("patient_basic", ["full_name", "gender", "date_of_birth", "phone", "phone2", "email",
                               "national_id", "address", "branch", "source", "is_active"]),
            ("patient_billing", ["price_list", "insurance_no"]),
            ("patient_medical", ["allergies", "chronic_conditions", "notes"]),
        ]
        ctx["sections"] = [{"title": tr(t), "fields": [form[f] for f in names]} for t, names in groups]
        then = self.request.GET.get("then", "")
        ctx["then"] = then if then in ("book", "invoice") else ""
        return ctx


class PatientCreate(PatientFormMixin, CreateView):
    template_name = "clinic/patient_form.html"

    def get_initial(self):
        initial = super().get_initial()
        initial["branch"] = self.request.branch
        q = self.request.GET.get("q", "").strip()
        if q:  # carried over from a search that found nothing
            initial["phone" if q.replace("+", "").replace(" ", "").isdigit() else "full_name"] = q
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class PatientUpdate(PatientFormMixin, UpdateView):
    template_name = "clinic/patient_form.html"


class PatientDetail(PermissionMixin, DetailView):
    module = "patients"
    model = Patient
    template_name = "clinic/patient_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        appts = [decorate(a, user) for a in
                 self.object.appointments.select_related("doctor", "service", "branch").order_by("-start")]
        now = timezone.now()
        ctx.update(
            title=self.object.full_name,
            upcoming=[a for a in appts if a.start >= now and a.is_active][::-1],
            history=[a for a in appts if not (a.start >= now and a.is_active)],
            can_edit=has_perm(user, "patients", "edit"),
            can_book=has_perm(user, "appointments", "edit"),
            can_see_appts=has_perm(user, "appointments", "view"),
            visits=sum(1 for a in appts if a.status == Status.COMPLETED),
            can_clinical=has_perm(user, "clinical", "view"),
            can_invoice=has_perm(user, "invoices", "edit"),
        )
        if has_perm(user, "invoices", "view"):
            from finance.views import decorate_invoice

            invoices = [decorate_invoice(i) for i in self.object.invoices.select_related("doctor")]
            ctx["invoices"] = invoices
            ctx["patient_balance"] = sum((i.balance for i in invoices if not i.is_cancelled), 0)
        return ctx


class PatientBook(PermissionMixin, View):
    module, action = "appointments", "edit"

    def get(self, request, pk):
        return redirect(f"{reverse('appointment_create')}?patient={pk}")


class PatientSearch(PermissionMixin, View):
    """Small JSON search used by the booking form's patient picker."""

    module = "patients"

    def get(self, request):
        q = request.GET.get("q", "").strip()
        if len(q) < 2:
            return JsonResponse({"results": []})
        qs = Patient.objects.filter(is_active=True).filter(
            Q(full_name__icontains=q) | Q(phone__icontains=q) | Q(phone2__icontains=q) | Q(file_no__icontains=q)
        )[:10]
        return JsonResponse({"results": [
            {"id": p.pk, "name": p.full_name, "phone": p.phone, "file_no": p.file_no,
             "allergies": p.allergies[:80], "price_list": p.price_list_id or ""} for p in qs
        ]})


# ======================================================================= appointments
class DayMixin:
    def day_context(self, day, url_name):
        today = timezone.localdate()
        base = reverse(url_name)
        keep = self.request.GET.copy()

        def link(d):
            keep["date"] = d.isoformat()
            return f"{base}?{keep.urlencode()}"

        return {
            "day": day, "is_today": day == today,
            "prev_url": link(day - timedelta(days=1)), "next_url": link(day + timedelta(days=1)),
            "today_url": link(today),
        }

    def doctor_filter(self):
        """Doctors in this branch; a doctor sees their own schedule first."""
        doctors = list(doctors_for(self.request.branch))
        chosen = self.request.GET.get("doctor")
        user = self.request.user
        if chosen is None and user.role == Role.DOCTOR:
            chosen = str(user.pk)
        return doctors, chosen or ""


class AppointmentList(PermissionMixin, DayMixin, TemplateView):
    """The day's bookings for the current branch, with status tabs and actions."""

    module = "appointments"
    template_name = "clinic/appointments.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        day = parse_day(self.request.GET.get("date"))
        doctors, chosen = self.doctor_filter()
        qs = Appointment.objects.filter(branch=self.request.branch, start__date=day).select_related(
            "patient", "doctor", "service")
        if chosen:
            qs = qs.filter(doctor_id=chosen)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(patient__full_name__icontains=q) | Q(patient__phone__icontains=q)
                           | Q(patient__file_no__icontains=q))
        counts = dict(qs.values_list("status").annotate(n=Count("id")))
        status = self.request.GET.get("status", "")
        tabs = [{"key": "", "label": tr("all"), "n": sum(counts.values())}] + [
            {"key": s.value, "label": s.label, "n": counts.get(s.value, 0)} for s in Status
        ]
        if status in Status.values:
            qs = qs.filter(status=status)
        user = self.request.user
        ctx.update(self.day_context(day, "appointment_list"))
        ctx.update(
            title=tr("nav_appointments"),
            appointments=[decorate(a, user) for a in qs],
            tabs=tabs, status=status, q=q, doctors=doctors, chosen_doctor=chosen,
            can_book=has_perm(user, "appointments", "edit"),
            next_url=self.request.get_full_path(),
        )
        return ctx


class AppointmentCalendar(PermissionMixin, DayMixin, TemplateView):
    """Day view: one column per doctor, appointments placed by time."""

    module = "appointments"
    template_name = "clinic/calendar.html"
    PX_PER_MIN = 1.6

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        day = parse_day(self.request.GET.get("date"))
        doctors, chosen = self.doctor_filter()
        shown = [d for d in doctors if not chosen or str(d.pk) == chosen]
        appts = list(
            Appointment.objects.filter(branch=self.request.branch, start__date=day, doctor__in=shown)
            .exclude(status__in=INACTIVE_STATUSES).select_related("patient", "service")
        )
        first_hour, last_hour = settings.CLINIC_DAY_START, settings.CLINIC_DAY_END
        for a in appts:
            local = timezone.localtime(a.start)
            first_hour = min(first_hour, local.hour)
            last_hour = max(last_hour, min(24, timezone.localtime(a.end).hour + 1))
        origin = datetime.combine(day, datetime.min.time()).replace(hour=first_hour)
        px = self.PX_PER_MIN
        user = self.request.user
        by_doctor = {d.pk: [] for d in shown}
        for a in appts:
            decorate(a, user)
            minutes = (timezone.make_naive(a.start) - origin).total_seconds() / 60
            a.top = round(minutes * px)
            a.height = max(round(a.duration_minutes * px) - 2, 22)
            a.short = a.height < 50
            by_doctor.setdefault(a.doctor_id, []).append(a)
        now_top = None
        if day == timezone.localdate():
            minutes = (timezone.make_naive(timezone.now()) - origin).total_seconds() / 60
            if 0 <= minutes <= (last_hour - first_hour) * 60:
                now_top = round(minutes * px)
        ctx.update(self.day_context(day, "appointment_calendar"))
        ctx.update(
            title=tr("calendar"),
            columns=[{"doctor": d, "items": by_doctor.get(d.pk, [])} for d in shown],
            hours=[{"label": f"{h:02d}:00", "top": round((h - first_hour) * 60 * px)}
                   for h in range(first_hour, last_hour + 1)],
            grid_height=round((last_hour - first_hour) * 60 * px),
            first_hour=first_hour, px_per_min=px, now_top=now_top,
            doctors=doctors, chosen_doctor=chosen,
            can_book=has_perm(user, "appointments", "edit"),
        )
        return ctx


class AppointmentFormMixin(CrudFormMixin):
    module, model, form_class = "appointments", Appointment, AppointmentForm
    entity_key, list_url = "ent_appointment", "appointment_list"
    template_name = "clinic/appointment_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.branch is None:
            messages.error(request, tr("need_branch"))
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["branch"] = self.object.branch if getattr(self, "object", None) else self.request.branch
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ctx["form"]
        patient = None
        pid = form["patient"].value()
        if pid:
            patient = Patient.objects.filter(pk=pid).first()
        ctx.update(
            patient=patient,
            service_durations={str(s.pk): s.duration_minutes for s in form.fields["service"].queryset},
            fields=[form[f] for f in form.fields if f != "patient"],
            back_url=self.get_success_url_for_day(),
        )
        return ctx

    def get_success_url_for_day(self):
        day = None
        if getattr(self, "object", None) and self.object.pk:
            day = timezone.localdate(self.object.start)
        else:
            day = parse_day(self.request.GET.get("date") or self.request.POST.get("date"))
        return f"{reverse('appointment_list')}?date={day.isoformat()}"

    def get_success_url(self):
        return f"{reverse('appointment_list')}?date={timezone.localdate(self.object.start).isoformat()}"


class AppointmentCreate(AppointmentFormMixin, CreateView):
    def get_initial(self):
        initial = super().get_initial()
        g = self.request.GET
        for key in ("patient", "doctor", "service", "time"):
            if g.get(key):
                initial[key] = g[key]
        initial["date"] = parse_day(g.get("date"))
        if self.request.user.role == Role.DOCTOR and "doctor" not in initial:
            initial["doctor"] = self.request.user.pk
        return initial

    def form_valid(self, form):
        form.instance.branch = self.request.branch
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class AppointmentUpdate(AppointmentFormMixin, UpdateView):
    def get_queryset(self):
        return Appointment.objects.exclude(status=Status.COMPLETED)


class AppointmentAction(PermissionMixin, View):
    module, action = "appointments", "status"

    def post(self, request, pk, action):
        appt = get_object_or_404(Appointment, pk=pk)
        if action not in TRANSITIONS or action not in allowed_actions(request.user, appt):
            raise PermissionDenied
        if action == "cancel":
            appt.cancel_reason = request.POST.get("reason", "")[:255]
        before = appt.status
        appt.apply(action)
        log(request, "status", appt, [f"{before} → {appt.status}"])
        msg = tr(f"done_{action}")
        if action == "checkin":
            msg = tr("done_checkin_n", appt.queue_number)
        messages.success(request, msg)
        fallback = f"{reverse('appointment_list')}?date={timezone.localdate(appt.start).isoformat()}"
        return redirect(safe_next(request, fallback))


# ======================================================================= queue screen
def queue_snapshot(branch):
    today = timezone.localdate()
    qs = (Appointment.objects.filter(branch=branch, start__date=today,
                                     status__in=[Status.ARRIVED, Status.IN_PROGRESS])
          .select_related("patient", "doctor").order_by("checked_in_at", "start"))
    doctors = {}
    for a in qs:
        entry = doctors.setdefault(a.doctor_id, {"doctor": str(a.doctor), "color": a.doctor.color,
                                                 "now": [], "waiting": []})
        item = {"n": a.queue_number, "name": a.patient.first_name}
        entry["now" if a.status == Status.IN_PROGRESS else "waiting"].append(item)
    return list(doctors.values())


class QueueScreen(PermissionMixin, TemplateView):
    """Full-screen page for a TV in the waiting room. Shows first names only."""

    module = "appointments"
    template_name = "clinic/queue.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(title=tr("queue_screen"), doctors=queue_snapshot(self.request.branch))
        return ctx


class QueueData(PermissionMixin, View):
    module = "appointments"

    def get(self, request):
        return JsonResponse({"doctors": queue_snapshot(request.branch),
                             "time": timezone.localtime().strftime("%H:%M")})


def todays_counts(branch):
    """Used by the dashboard."""
    if branch is None:
        return {}
    qs = Appointment.objects.filter(branch=branch, start__date=timezone.localdate())
    return dict(qs.values_list("status").annotate(n=Count("id")))
