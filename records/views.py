from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, TemplateView

from accounts.roles import Role, has_perm
from clinic.models import Appointment, Patient, Status
from core.audit import log
from core.i18n import tr
from core.mixins import PermissionMixin
from finance.views import patient_price_list, price_tables

from .forms import (
    NoteForm, PhotoForm, PlanForm, PlanItemFormSet, RxForm, RxItemFormSet, ToothForm,
)
from .models import (
    LOWER, UPPER, ClinicalNote, PatientPhoto, PlanStatus, Prescription, ToothCondition, ToothRecord,
    TreatmentPlan,
)

TABS = ["notes", "teeth", "rx", "plans", "photos"]
TOOTH_STYLE = {c.value: f"tc-{c.value}" for c in ToothCondition}
PLAN_STYLE = {"proposed": "", "accepted": "badge-info", "in_progress": "badge-live", "done": "badge-good",
              "cancelled": "badge-muted"}


def file_url(patient_id, tab):
    return f"{reverse('clinical_patient', args=[patient_id])}?tab={tab}"


class ClinicalHome(PermissionMixin, TemplateView):
    """Start page for doctors and nurses: today's patients and the latest notes."""

    module = "clinical"
    template_name = "records/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        today = Appointment.objects.filter(
            branch=self.request.branch, start__date=timezone.localdate(),
            status__in=[Status.ARRIVED, Status.IN_PROGRESS, Status.COMPLETED],
        ).select_related("patient", "doctor", "service").order_by("status", "start")
        notes = ClinicalNote.objects.select_related("patient", "doctor")
        if user.role == Role.DOCTOR and not user.is_admin:
            today = today.filter(doctor=user)
            notes = notes.filter(doctor=user)
        for a in today:
            a.local_start = timezone.localtime(a.start)
        ctx.update(title=tr("nav_clinical"), today=today, notes=notes[:30])
        return ctx


class PatientClinical(PermissionMixin, DetailView):
    """The patient's clinical file, one tab at a time."""

    module = "clinical"
    model = Patient
    template_name = "records/patient.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        p = self.object
        user = self.request.user
        tab = self.request.GET.get("tab")
        if tab not in TABS:
            tab = "notes"
        counts = {"notes": p.clinical_notes.count(), "rx": p.prescriptions.count(),
                  "plans": p.treatment_plans.count(), "photos": p.photos.count()}
        ctx.update(
            title=p.full_name, tab=tab,
            tabs=[{"key": t, "label": tr(f"tab_{t}"), "url": file_url(p.pk, t), "n": counts.get(t)}
                  for t in TABS],
            can_edit=has_perm(user, "clinical", "edit"),
            can_prescribe=has_perm(user, "clinical", "prescribe"),
        )
        if tab == "notes":
            ctx["notes"] = p.clinical_notes.select_related("doctor", "appointment")
        elif tab == "teeth":
            records = {r.tooth: r for r in p.teeth.select_related("updated_by")}

            def row(numbers):
                out = []
                for n in numbers:
                    r = records.get(n)
                    cond = r.condition if r else "healthy"
                    out.append({"n": n, "condition": cond, "label": ToothCondition(cond).label,
                                "note": r.note if r else "", "css": TOOTH_STYLE[cond], "record": r})
                return out

            ctx.update(upper=row(UPPER), lower=row(LOWER), conditions=ToothCondition.choices,
                       charted=[r for r in records.values() if r.condition != "healthy"],
                       tooth_form=ToothForm())
        elif tab == "rx":
            ctx["prescriptions"] = p.prescriptions.select_related("doctor").prefetch_related("items")
        elif tab == "plans":
            plans = list(p.treatment_plans.select_related("doctor").prefetch_related("items__service"))
            for pl in plans:
                pl.status_class = PLAN_STYLE.get(pl.status, "")
            ctx["plans"] = plans
            ctx["can_invoice"] = has_perm(user, "invoices", "edit")
        elif tab == "photos":
            groups = {}
            for ph in p.photos.defer("data"):
                groups.setdefault(ph.area, []).append(ph)
            ctx["photo_groups"] = [{"area": k, "photos": sorted(v, key=lambda x: (x.taken_on, x.pk))}
                                   for k, v in groups.items()]
        return ctx


class PatientChildMixin(PermissionMixin):
    """Pages that add something to one patient's file."""

    module, action = "clinical", "edit"
    tab = "notes"

    def dispatch(self, request, *args, **kwargs):
        self.patient = get_object_or_404(Patient, pk=kwargs["pk"]) if "pk" in kwargs else None
        return super().dispatch(request, *args, **kwargs)

    def back(self, patient_id=None):
        return file_url(patient_id or self.patient.pk, self.tab)

    def render_page(self, template, **ctx):
        from django.shortcuts import render

        return render(self.request, template, {"back_url": self.back(ctx.get("patient_id")),
                                               "patient": self.patient, **ctx})


def default_doctor(request):
    return request.user.pk if request.user.role == Role.DOCTOR else None


# ---------------------------------------------------------------- notes
class NoteEdit(PatientChildMixin, View):
    tab = "notes"

    def get_note(self, note_id):
        if note_id:
            note = get_object_or_404(ClinicalNote, pk=note_id)
            self.patient = note.patient
            return note
        return ClinicalNote(patient=self.patient)

    def get(self, request, pk=None, note_id=None):
        note = self.get_note(note_id)
        initial = {}
        if not note.pk:
            initial = {"date": timezone.localdate(), "doctor": default_doctor(request)}
            appt = Appointment.objects.filter(pk=request.GET.get("appointment") or 0, patient=self.patient).first()
            if appt:
                initial.update(doctor=appt.doctor_id, date=timezone.localdate(appt.start),
                               department=appt.service.department if appt.service else "")
        return self.show(NoteForm(instance=note, initial=initial), note)

    def post(self, request, pk=None, note_id=None):
        note = self.get_note(note_id)
        form = NoteForm(request.POST, instance=note)
        if form.is_valid():
            is_new = note.pk is None
            note = form.save(commit=False)
            note.patient = self.patient
            if is_new:
                note.created_by = request.user
                note.appointment = Appointment.objects.filter(
                    pk=request.POST.get("appointment") or 0, patient=self.patient).first()
            note.save()
            log(request, "create" if is_new else "update", note, form.changed_data)
            messages.success(request, tr("saved"))
            return redirect(self.back())
        messages.error(request, tr("fix_errors"))
        return self.show(form, note)

    def show(self, form, note):
        verb = tr("edit") if note.pk else tr("add")
        return self.render_page("records/form.html", form=form, title=f"{verb} {tr('ent_note')}",
                                appointment=self.request.GET.get("appointment", ""))


# ---------------------------------------------------------------- tooth chart
class ToothSave(PatientChildMixin, View):
    tab = "teeth"

    def post(self, request, pk):
        form = ToothForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            rec, _ = ToothRecord.objects.update_or_create(
                patient=self.patient, tooth=d["tooth"],
                defaults={"condition": d["condition"], "note": d["note"], "updated_by": request.user})
            log(request, "update", rec, [f"{d['tooth']}: {d['condition']}"])
            messages.success(request, tr("tooth_saved", d["tooth"]))
        else:
            messages.error(request, tr("fix_errors"))
        return redirect(self.back())


# ---------------------------------------------------------------- formset pages (Rx, plans)
class FormsetEdit(PatientChildMixin, View):
    model = form_class = formset_class = None
    template = "records/formset_form.html"
    entity_key = ""

    def get_obj(self, obj_id):
        if obj_id:
            obj = get_object_or_404(self.model, pk=obj_id)
            self.patient = obj.patient
            return obj
        return self.model(patient=self.patient)

    def initial(self):
        return {"doctor": default_doctor(self.request)}

    def get(self, request, pk=None, obj_id=None):
        obj = self.get_obj(obj_id)
        form = self.form_class(instance=obj, initial={} if obj.pk else self.initial())
        return self.show(form, self.formset_class(prefix="items", instance=obj), obj)

    def post(self, request, pk=None, obj_id=None):
        obj = self.get_obj(obj_id)
        form = self.form_class(request.POST, instance=obj)
        formset = self.formset_class(request.POST, prefix="items", instance=obj)
        if form.is_valid() and formset.is_valid():
            is_new = obj.pk is None
            with transaction.atomic():
                obj = form.save(commit=False)
                obj.patient = self.patient
                obj.save()
                formset.instance = obj
                formset.save()
            log(request, "create" if is_new else "update", obj, form.changed_data)
            messages.success(request, tr("saved"))
            return redirect(self.success_url(obj))
        messages.error(request, tr("fix_errors"))
        return self.show(form, formset, obj)

    def success_url(self, obj):
        return self.back()

    def show(self, form, formset, obj, **extra):
        verb = tr("edit") if obj.pk else tr("add")
        return self.render_page(self.template, form=form, formset=formset,
                                title=f"{verb} {tr(self.entity_key)}", obj=obj, **self.extra_context(), **extra)

    def extra_context(self):
        return {}


class RxEdit(FormsetEdit):
    action = "prescribe"
    tab = "rx"
    model, form_class, formset_class = Prescription, RxForm, RxItemFormSet
    entity_key = "ent_rx"
    template = "records/rx_form.html"

    def initial(self):
        return {**super().initial(), "date": timezone.localdate()}

    def success_url(self, obj):
        return reverse("rx_print", args=[obj.pk])


class RxPrint(PermissionMixin, DetailView):
    module = "clinical"
    model = Prescription
    template_name = "records/rx_print.html"

    def get_queryset(self):
        return Prescription.objects.select_related("patient", "doctor").prefetch_related("items")


class PlanEdit(FormsetEdit):
    tab = "plans"
    model, form_class, formset_class = TreatmentPlan, PlanForm, PlanItemFormSet
    entity_key = "ent_plan"
    template = "records/plan_form.html"

    def extra_context(self):
        pl = patient_price_list(self.patient)
        return {"prices": price_tables(), "patient_list": pl.pk if pl else ""}

    def initial(self):
        return {**super().initial(), "status": PlanStatus.PROPOSED}


# ---------------------------------------------------------------- photos
class PhotoUpload(PatientChildMixin, View):
    tab = "photos"

    def get(self, request, pk):
        return self.render_page("records/photo_form.html", form=PhotoForm(initial={"taken_on": timezone.localdate()}),
                                title=tr("add_photo"), areas=self.areas())

    def areas(self):
        return list(self.patient.photos.values_list("area", flat=True).distinct())

    def post(self, request, pk):
        form = PhotoForm(request.POST, request.FILES)
        if form.is_valid():
            photo = form.save(commit=False)
            photo.patient, photo.uploaded_by = self.patient, request.user
            photo.save()
            log(request, "create", photo)
            messages.success(request, tr("photo_saved"))
            return redirect(self.back())
        messages.error(request, tr("fix_errors"))
        return self.render_page("records/photo_form.html", form=form, title=tr("add_photo"), areas=self.areas())


class PhotoImage(PermissionMixin, View):
    """Serves a photo only to signed-in staff allowed to see clinical records."""

    module = "clinical"

    def get(self, request, photo_id):
        photo = PatientPhoto.objects.filter(pk=photo_id).first()
        if photo is None:
            raise Http404
        resp = HttpResponse(bytes(photo.data), content_type=photo.content_type)
        resp["Cache-Control"] = "private, max-age=86400"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


class PhotoDelete(PermissionMixin, View):
    module, action = "clinical", "edit"

    def post(self, request, photo_id):
        photo = get_object_or_404(PatientPhoto.objects.defer("data"), pk=photo_id)
        pid = photo.patient_id
        log(request, "update", photo.patient, [f"photo removed: {photo}"])
        photo.delete()
        messages.success(request, tr("photo_deleted"))
        return redirect(file_url(pid, "photos"))
