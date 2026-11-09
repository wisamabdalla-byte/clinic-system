from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from .i18n import T, tr
from .models import Branch, Package, PackageItem, PaymentMethod, PriceList, Service, ServiceCategory


class StyledFormMixin:
    """Bilingual labels/help from core.i18n and consistent CSS classes."""

    label_keys = {}

    def style_fields(self):
        for name, field in self.fields.items():
            key = self.label_keys.get(name, name)
            field.label = tr(key)
            if f"{key}_help" in T:
                field.help_text = tr(f"{key}_help")
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                css = "check"
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                css = "check-list"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = "input select"
            else:
                css = "input"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()
            if name in ("name_en", "code", "username", "email", "phone"):
                widget.attrs.setdefault("dir", "ltr")


class StyledModelForm(StyledFormMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class BranchForm(StyledModelForm):
    class Meta:
        model = Branch
        fields = ["name_ar", "name_en", "code", "phone", "address", "is_active"]


class PaymentMethodForm(StyledModelForm):
    label_keys = {"kind": "payment_kind"}

    class Meta:
        model = PaymentMethod
        fields = ["name_ar", "name_en", "kind", "requires_reference", "account_details", "sort_order", "is_active"]


class CategoryForm(StyledModelForm):
    class Meta:
        model = ServiceCategory
        fields = ["name_ar", "name_en", "department", "is_active"]


class ServiceForm(StyledModelForm):
    label_keys = {"price": "base_price"}

    class Meta:
        model = Service
        fields = [
            "code", "name_ar", "name_en", "department", "category",
            "price", "duration_minutes", "requires_tooth", "description", "is_active",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = ServiceCategory.objects.filter(is_active=True)
        if self.instance.pk:  # keep a now-inactive category selectable on old services
            qs = qs | ServiceCategory.objects.filter(pk=self.instance.category_id)
        self.fields["category"].queryset = qs.distinct()

    def clean(self):
        data = super().clean()
        category, department = data.get("category"), data.get("department")
        if category and department and category.department != department:
            self.add_error("category", tr("category_dept_mismatch"))
        return data


class PriceListForm(StyledModelForm):
    class Meta:
        model = PriceList
        fields = [
            "name_ar", "name_en", "kind", "branch", "discount_percent",
            "valid_from", "valid_to", "is_default", "is_active",
        ]
        widgets = {"valid_from": DateInput(), "valid_to": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = Branch.objects.filter(is_active=True)
        self.fields["branch"].empty_label = tr("all_branches")

    def clean(self):
        data = super().clean()
        start, end = data.get("valid_from"), data.get("valid_to")
        if start and end and end < start:
            self.add_error("valid_to", tr("valid_range_error"))
        return data


class PackageForm(StyledModelForm):
    class Meta:
        model = Package
        fields = ["name_ar", "name_en", "department", "price", "validity_days", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class PackageItemForm(StyledModelForm):
    class Meta:
        model = PackageItem
        fields = ["service", "quantity"]
        widgets = {"quantity": forms.NumberInput(attrs={"min": 1})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.filter(is_active=True).select_related("category")


class BasePackageItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen, kept = set(), 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            service = form.cleaned_data.get("service")
            if not service:
                continue
            if service.pk in seen:
                raise ValidationError(tr("duplicate_service", service))
            seen.add(service.pk)
            kept += 1
        if kept == 0:
            raise ValidationError(tr("package_needs_items"))


PackageItemFormSet = inlineformset_factory(
    Package, PackageItem, form=PackageItemForm, formset=BasePackageItemFormSet,
    extra=1, can_delete=True,
)


IMAGE_SIGNATURES = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}


def sniff_image(data):
    """Content type from the file's first bytes (PNG, JPEG, WebP only)."""
    for sig, ctype in IMAGE_SIGNATURES.items():
        if data.startswith(sig):
            if ctype == "image/webp" and data[8:12] != b"WEBP":
                return None
            return ctype
    return None


class ClinicProfileForm(StyledModelForm):
    MAX_LOGO = 400 * 1024

    logo_file = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}))
    remove_logo = forms.BooleanField(required=False)

    class Meta:
        from .models import ClinicProfile

        model = ClinicProfile
        fields = ["name_ar", "name_en", "tagline_ar", "tagline_en", "phone", "address", "primary_color",
                  "invoice_footer"]
        widgets = {"primary_color": forms.TextInput(attrs={"type": "color"}),
                   "invoice_footer": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["logo_file"].label = tr("logo")
        self.fields["logo_file"].help_text = tr("logo_help")
        self.fields["remove_logo"].label = tr("remove_logo")
        for name in ("name_en", "tagline_en", "phone"):
            self.fields[name].widget.attrs["dir"] = "ltr"

    def clean_logo_file(self):
        f = self.cleaned_data.get("logo_file")
        if not f:
            return None
        if f.size > self.MAX_LOGO:
            raise ValidationError(tr("logo_too_big"))
        data = f.read()
        ctype = sniff_image(data)
        if not ctype:
            raise ValidationError(tr("image_type_error"))
        return ctype, data

    def save(self, commit=True):
        import base64

        profile = super().save(commit=False)
        logo = self.cleaned_data.get("logo_file")
        if self.cleaned_data.get("remove_logo"):
            profile.logo = ""
        elif logo:
            ctype, data = logo
            profile.logo = f"data:{ctype};base64,{base64.b64encode(data).decode()}"
        if commit:
            profile.save()
        return profile
