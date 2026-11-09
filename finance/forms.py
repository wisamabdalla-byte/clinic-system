from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role
from clinic.forms import doctors_for
from clinic.models import Patient
from core.forms import DateInput, StyledFormMixin, StyledModelForm
from core.i18n import tr
from core.models import PaymentMethod, Service

from .models import DoctorPayout, Expense, Invoice, InvoiceItem, Payment
from .services import is_month_closed


def active_methods():
    return PaymentMethod.objects.filter(is_active=True)


def check_reference(form, method_field="method", ref_field="reference"):
    method, ref = form.cleaned_data.get(method_field), form.cleaned_data.get(ref_field)
    if method and method.requires_reference and not (ref or "").strip():
        form.add_error(ref_field, tr("reference_required"))


class InvoiceForm(StyledModelForm):
    class Meta:
        model = Invoice
        fields = ["patient", "doctor", "date", "discount", "notes"]
        widgets = {"patient": forms.HiddenInput, "date": DateInput(),
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["patient"].queryset = Patient.objects.filter(is_active=True)
        self.fields["patient"].error_messages["required"] = tr("choose_patient")
        doctors = doctors_for(branch)
        if self.instance.pk:
            doctors = doctors | User.objects.filter(pk=self.instance.doctor_id)
        self.fields["doctor"].queryset = doctors.distinct()
        self.fields["discount"].widget.attrs.update(min=0, step="0.01")

    def clean_date(self):
        d = self.cleaned_data.get("date")
        if d and d > timezone.localdate():
            raise ValidationError(tr("date_future"))
        return d


class InvoiceItemForm(StyledModelForm):
    label_keys = {"description": "item_description", "unit_price": "price"}

    class Meta:
        model = InvoiceItem
        fields = ["service", "description", "tooth", "quantity", "unit_price", "material_cost"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"min": 1}),
            "unit_price": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "material_cost": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.filter(is_active=True).select_related("category")
        self.fields["material_cost"].required = False
        self.fields["tooth"].widget.attrs.update(dir="ltr", placeholder="16")
        self.fields["description"].widget.attrs["placeholder"] = tr("item_description_ph")
        self.fields["service"].empty_label = tr("other_item")

    def clean(self):
        data = super().clean()
        if not data.get("service") and not (data.get("description") or "").strip() and data.get("unit_price") is not None:
            self.add_error("description", tr("item_needs_service"))
        if data.get("material_cost") is None:
            data["material_cost"] = Decimal(0)
        return data


class BaseItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        kept = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")
                and (f.cleaned_data.get("service") or f.cleaned_data.get("description"))]
        if not kept:
            raise ValidationError(tr("invoice_needs_items"))
        for f in kept:
            d = f.cleaned_data
            line = (d.get("unit_price") or 0) * (d.get("quantity") or 0)
            if (d.get("material_cost") or 0) > line:
                f.add_error("material_cost", tr("material_too_high"))


ItemFormSet = inlineformset_factory(
    Invoice, InvoiceItem, form=InvoiceItemForm, formset=BaseItemFormSet, extra=1, can_delete=True
)


class FirstPaymentForm(StyledFormMixin, forms.Form):
    """Optional 'paid now' block on the new-invoice page."""

    amount = forms.DecimalField(required=False, min_value=0, decimal_places=2, max_digits=12,
                                widget=forms.NumberInput(attrs={"step": "0.01", "min": 0}))
    method = forms.ModelChoiceField(queryset=PaymentMethod.objects.none(), required=False)
    reference = forms.CharField(required=False, max_length=100)
    label_keys = {"amount": "paid_now", "method": "payment_method"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["method"].queryset = active_methods()
        self.fields["method"].empty_label = None
        self.style_fields()
        self.fields["reference"].widget.attrs["dir"] = "ltr"

    def clean(self):
        data = super().clean()
        if data.get("amount"):
            if not data.get("method"):
                self.add_error("method", tr("required"))
            check_reference(self)
        return data


class PaymentForm(StyledModelForm):
    label_keys = {"method": "payment_method"}

    class Meta:
        model = Payment
        fields = ["date", "amount", "method", "reference", "notes"]
        widgets = {"date": DateInput(), "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}

    def __init__(self, *args, invoice=None, **kwargs):
        self.invoice = invoice
        super().__init__(*args, **kwargs)
        self.fields["method"].queryset = active_methods()
        self.fields["method"].empty_label = None
        self.fields["reference"].widget.attrs["dir"] = "ltr"

    def clean(self):
        data = super().clean()
        amount, day = data.get("amount"), data.get("date")
        if amount and amount > self.invoice.balance:
            self.add_error("amount", tr("amount_over_balance", f"{self.invoice.balance:,.2f}"))
        if day and day > timezone.localdate():
            self.add_error("date", tr("date_future"))
        if day and is_month_closed(self.invoice.doctor, day):
            self.add_error("date", tr("month_closed_error"))
        check_reference(self)
        return data


class PayoutForm(StyledModelForm):
    label_keys = {"method": "payment_method"}

    class Meta:
        model = DoctorPayout
        fields = ["doctor", "date", "amount", "method", "reference", "notes"]
        widgets = {"date": DateInput(), "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["doctor"].queryset = User.objects.filter(role=Role.DOCTOR)
        self.fields["method"].queryset = active_methods()
        self.fields["method"].empty_label = None
        self.fields["reference"].widget.attrs["dir"] = "ltr"

    def clean(self):
        data = super().clean()
        check_reference(self)
        return data


class ExpenseForm(StyledModelForm):
    label_keys = {"method": "payment_method", "category": "expense_category"}

    class Meta:
        model = Expense
        fields = ["date", "category", "amount", "method", "description", "reference"]
        widgets = {"date": DateInput(), "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["method"].queryset = active_methods()
        self.fields["method"].empty_label = None
        self.fields["reference"].widget.attrs["dir"] = "ltr"


class CloseMonthForm(StyledFormMixin, forms.Form):
    adjustment = forms.DecimalField(required=False, decimal_places=2, max_digits=12, initial=0,
                                    widget=forms.NumberInput(attrs={"step": "0.01"}))
    adjustment_note = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()
