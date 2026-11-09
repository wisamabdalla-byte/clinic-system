from datetime import datetime, timedelta

from django import forms
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role
from core.forms import DateInput, StyledModelForm
from core.i18n import tr
from core.models import Branch, PriceList, Service

from .models import Appointment, Patient


class PatientForm(StyledModelForm):
    label_keys = {"full_name": "patient_name", "price_list": "patient_price_list", "branch": "home_branch"}

    class Meta:
        model = Patient
        fields = [
            "full_name", "gender", "date_of_birth", "phone", "phone2", "email", "national_id",
            "address", "branch", "source", "price_list", "insurance_no",
            "allergies", "chronic_conditions", "notes", "is_active",
        ]
        widgets = {
            "date_of_birth": DateInput(),
            "allergies": forms.Textarea(attrs={"rows": 2}),
            "chronic_conditions": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = Branch.objects.filter(is_active=True)
        self.fields["price_list"].queryset = PriceList.objects.filter(is_active=True)
        self.fields["price_list"].empty_label = tr("default_price_list")
        for name in ("phone2", "national_id", "insurance_no"):
            self.fields[name].widget.attrs["dir"] = "ltr"

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob and dob > timezone.localdate():
            raise forms.ValidationError(tr("dob_future"))
        return dob


def doctors_for(branch):
    qs = User.objects.filter(role=Role.DOCTOR, is_active=True)
    return qs.filter(branches=branch) if branch else qs


class AppointmentForm(StyledModelForm):
    label_keys = {"source": "booking_source"}
    date =forms.DateField(widget=DateInput())
    time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time", "step": 300}, format="%H:%M"))

    class Meta:
        model = Appointment
        fields = ["patient", "doctor", "service", "date", "time", "duration_minutes", "source", "notes",
                  "cancel_reason"]
        widgets = {"patient": forms.HiddenInput, "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, branch=None, **kwargs):
        self.branch = branch
        super().__init__(*args, **kwargs)
        self.fields["patient"].queryset = Patient.objects.filter(is_active=True)
        self.fields["patient"].error_messages["required"] = tr("choose_patient")
        doctors = doctors_for(branch)
        if self.instance.pk:  # keep the current doctor even if they left the branch
            doctors = doctors | User.objects.filter(pk=self.instance.doctor_id)
        self.fields["doctor"].queryset = doctors.distinct()
        self.fields["service"].queryset = Service.objects.filter(is_active=True).select_related("category")
        if self.instance.pk:
            local = timezone.localtime(self.instance.start)
            self.initial.setdefault("date", local.date())
            self.initial.setdefault("time", local.time().replace(second=0, microsecond=0))
        else:
            del self.fields["cancel_reason"]

    def clean(self):
        data = super().clean()
        day, time = data.get("date"), data.get("time")
        if day and time:
            start = timezone.make_aware(datetime.combine(day, time))
            self.instance.start = start
            doctor = data.get("doctor")
            minutes = data.get("duration_minutes") or 30
            if doctor:
                self.instance.doctor = doctor
                self.instance.duration_minutes = minutes
                clash = self.instance.overlapping().select_related("patient").first()
                if clash:
                    local = timezone.localtime(clash.start)
                    end = local + timedelta(minutes=clash.duration_minutes)
                    self.add_error("time", tr("doctor_busy", f"{local:%H:%M}–{end:%H:%M}", clash.patient.full_name))
        return data
