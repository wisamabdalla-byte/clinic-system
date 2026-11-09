from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role
from core.forms import DateInput, StyledFormMixin, StyledModelForm, sniff_image
from core.i18n import tr
from core.models import Service

from .models import (
    TEETH, ClinicalNote, PatientPhoto, PhotoStage, PlanItem, Prescription, PrescriptionItem, ToothCondition,
    TreatmentPlan,
)

ROWS3 = {"rows": 3}


def doctors():
    return User.objects.filter(role=Role.DOCTOR, is_active=True)


class DoctorFieldMixin:
    def setup_doctor(self):
        qs = doctors()
        if self.instance.pk:
            qs = qs | User.objects.filter(pk=self.instance.doctor_id)
        self.fields["doctor"].queryset = qs.distinct()


class NoteForm(DoctorFieldMixin, StyledModelForm):
    class Meta:
        model = ClinicalNote
        fields = ["doctor", "date", "department", "complaint", "examination", "diagnosis", "treatment",
                  "plan_next", "follow_up"]
        widgets = {"date": DateInput(), "follow_up": DateInput(),
                   **{f: forms.Textarea(attrs=ROWS3) for f in
                      ("complaint", "examination", "diagnosis", "treatment", "plan_next")}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_doctor()

    def clean(self):
        data = super().clean()
        if not any((data.get(f) or "").strip() for f in ("complaint", "examination", "diagnosis", "treatment")):
            raise ValidationError(tr("note_empty"))
        return data


class ToothForm(StyledFormMixin, forms.Form):
    tooth = forms.TypedChoiceField(choices=[(t, t) for t in TEETH], coerce=int)
    condition = forms.ChoiceField(choices=ToothCondition.choices)
    note = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class RxForm(DoctorFieldMixin, StyledModelForm):
    class Meta:
        model = Prescription
        fields = ["doctor", "date", "notes"]
        widgets = {"date": DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_doctor()


class RxItemForm(StyledModelForm):
    class Meta:
        model = PrescriptionItem
        fields = ["drug", "dose", "frequency", "duration", "instructions"]


class AtLeastOne(BaseInlineFormSet):
    message_key = "rx_needs_items"

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not any(f.cleaned_data and not f.cleaned_data.get("DELETE") for f in self.forms):
            raise ValidationError(tr(self.message_key))


RxItemFormSet = inlineformset_factory(Prescription, PrescriptionItem, form=RxItemForm, formset=AtLeastOne,
                                      extra=1, can_delete=True)


class PlanForm(DoctorFieldMixin, StyledModelForm):
    label_keys = {"title": "plan_title", "status": "status"}

    class Meta:
        model = TreatmentPlan
        fields = ["title", "doctor", "status", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_doctor()


class PlanItemForm(StyledModelForm):
    class Meta:
        model = PlanItem
        fields = ["service", "tooth", "quantity", "price", "is_done"]
        widgets = {"quantity": forms.NumberInput(attrs={"min": 1}),
                   "price": forms.NumberInput(attrs={"min": 0, "step": "0.01"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.filter(is_active=True)
        self.fields["tooth"].widget.attrs.update(dir="ltr", placeholder="16")

    def save(self, commit=True):
        item = super().save(commit=False)
        if item.is_done and not item.done_on:
            item.done_on = timezone.localdate()
        if not item.is_done:
            item.done_on = None
        if commit:
            item.save()
        return item


class PlanItems(AtLeastOne):
    message_key = "plan_needs_items"


PlanItemFormSet = inlineformset_factory(TreatmentPlan, PlanItem, form=PlanItemForm, formset=PlanItems,
                                        extra=1, can_delete=True)


class PhotoForm(StyledModelForm):
    MAX_BYTES = 3 * 1024 * 1024

    image = forms.FileField(widget=forms.ClearableFileInput(attrs={"accept": "image/*", "data-shrink": "1"}))

    class Meta:
        model = PatientPhoto
        fields = ["taken_on", "area", "stage", "note"]
        widgets = {"taken_on": DateInput()}

    field_order = ["image", "taken_on", "area", "stage", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage"].initial = PhotoStage.BEFORE
        self.fields["image"].label = tr("photo")
        self.fields["area"].widget.attrs.update(list="photo-areas", placeholder=tr("area_ph"))

    def clean_image(self):
        f = self.cleaned_data["image"]
        if f.size > self.MAX_BYTES:
            raise ValidationError(tr("photo_too_big"))
        data = f.read()
        ctype = sniff_image(data)
        if not ctype:
            raise ValidationError(tr("image_type_error"))
        return ctype, data

    def save(self, commit=True):
        photo = super().save(commit=False)
        photo.content_type, raw = self.cleaned_data["image"]
        photo.data, photo.size = raw, len(raw)
        if commit:
            photo.save()
        return photo
