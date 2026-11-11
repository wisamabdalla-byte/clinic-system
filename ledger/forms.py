from decimal import Decimal

from django import forms
from django.conf import settings
from django.forms import BaseFormSet, BaseInlineFormSet, formset_factory, inlineformset_factory
from django.utils import timezone

from core.forms import DateInput, StyledFormMixin, StyledModelForm
from core.i18n import tr

from .models import (
    Account, AssetCategory, BankReconciliation, Bill, BillKind, BillLine, Currency, Custody, CustodyMovement,
    ExchangeRate, ExpenseCategory, FixedAsset, FundingKind, LedgerSettings, MovementKind, Party, PartyKind,
    PaymentMethodAccount, Project, SETTING_ACCOUNTS, TaxRate, Transfer, Treasury, Voucher, VoucherKind,
)

MONEY_W = forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal", "dir": "ltr"})
RATE_W = forms.NumberInput(attrs={"step": "0.000001", "min": "0", "dir": "ltr"})


def postable(qs=None, **filters):
    qs = qs if qs is not None else Account.objects.all()
    return qs.filter(is_group=False, is_active=True, **filters).order_by("code")


def treasuries():
    return Treasury.objects.filter(is_active=True).select_related("account", "account__currency")


class PartyCodeField(forms.CharField):
    """Type or pick a party by its code (datalist), so long patient lists stay usable."""

    def __init__(self, *args, kinds=None, **kwargs):
        self.kinds = kinds
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)
        self.widget.attrs.update({"list": "party-list", "dir": "ltr", "autocomplete": "off"})

    def prepare_value(self, value):
        if isinstance(value, Party):
            return value.code
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            p = Party.objects.filter(pk=value).first()
            return p.code if p else value
        return value

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return None
        code = value.split(" ")[0].strip()
        qs = Party.objects.filter(code=code)
        if self.kinds:
            qs = qs.filter(kind__in=self.kinds)
        party = qs.first()
        if party is None:
            raise forms.ValidationError(tr("party_not_found"))
        return party


# ---------------------------------------------------------------- chart of accounts
class AccountForm(StyledModelForm):
    label_keys = {"type": "acc_type", "is_group": "is_group", "currency": "acc_currency"}

    class Meta:
        model = Account
        fields = ["code", "name_ar", "name_en", "parent", "type", "nature", "is_group", "is_control",
                  "allow_manual", "cash_flow", "currency", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = Account.objects.filter(is_group=True).order_by("code")
        self.fields["currency"].queryset = Currency.objects.filter(is_base=False)
        if not self.instance.pk and self.initial.get("parent"):
            parent = Account.objects.filter(pk=self.initial["parent"]).first()
            if parent:
                self.initial.setdefault("type", parent.type)
                self.initial.setdefault("nature", parent.nature)
                kids = parent.children.order_by("-code").values_list("code", flat=True)
                last = next((c for c in kids if c.isdigit()), None)
                self.initial.setdefault("code", str(int(last) + 1) if last else parent.code + "1")
        if self.instance.pk and self.instance.lines.exists():
            # an account with history keeps its type and side
            for name in ("type", "nature"):
                self.fields[name].disabled = True


# ---------------------------------------------------------------- journal entries
class EntryForm(StyledModelForm):
    label_keys = {"auto_reverse_on": "auto_reverse_on"}

    class Meta:
        from .models import JournalEntry

        model = JournalEntry
        fields = ["date", "kind", "description", "reference", "auto_reverse_on"]
        widgets = {"date": DateInput(), "auto_reverse_on": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [(k, v) for k, v in self.fields["kind"].choices
                                       if k in ("manual", "opening", "adjustment")]

    def clean(self):
        data = super().clean()
        if data.get("auto_reverse_on") and data.get("date") and data["auto_reverse_on"] <= data["date"]:
            self.add_error("auto_reverse_on", tr("auto_reverse_after"))
        return data


class LineForm(StyledFormMixin, forms.Form):
    account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False)
    description = forms.CharField(required=False, max_length=255)
    debit = forms.DecimalField(required=False, max_digits=12, decimal_places=2, min_value=0, widget=MONEY_W)
    credit = forms.DecimalField(required=False, max_digits=12, decimal_places=2, min_value=0, widget=MONEY_W)
    party = PartyCodeField(max_length=60)
    project = forms.ModelChoiceField(queryset=Project.objects.none(), required=False)
    fx_amount = forms.DecimalField(required=False, max_digits=12, decimal_places=2, min_value=0, widget=MONEY_W)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = postable(allow_manual=True).select_related("currency")
        self.fields["project"].queryset = Project.objects.filter(is_active=True)
        self.style_fields()

    @property
    def is_blank(self):
        d = self.cleaned_data if hasattr(self, "cleaned_data") else {}
        return not d.get("account") and not d.get("debit") and not d.get("credit")

    def clean(self):
        data = super().clean()
        d, c = data.get("debit") or 0, data.get("credit") or 0
        if self.is_blank:
            return data
        if not data.get("account"):
            self.add_error("account", tr("required"))
        if d and c:
            self.add_error("credit", tr("line_one_side"))
        if not d and not c:
            self.add_error("debit", tr("line_needs_amount"))
        acc = data.get("account")
        if acc and acc.is_control and not data.get("party"):
            self.add_error("party", tr("acc_needs_party", ""))
        return data


class BaseLineFormSet(BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        rows = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.is_blank]
        if len(rows) < 2:
            raise forms.ValidationError(tr("entry_min_lines"))
        d = sum((r.get("debit") or Decimal(0) for r in rows), Decimal(0))
        c = sum((r.get("credit") or Decimal(0) for r in rows), Decimal(0))
        if d != c:
            raise forms.ValidationError(tr("entry_unbalanced", f"{d:,.2f}", f"{c:,.2f}"))


LineFormSet = formset_factory(LineForm, formset=BaseLineFormSet, extra=0, min_num=2)


# ---------------------------------------------------------------- parties, projects, settings lists
class PartyForm(StyledModelForm):
    label_keys = {"kind": "party_kind", "account": "party_account"}

    class Meta:
        model = Party
        fields = ["kind", "name", "phone", "email", "address", "tax_no", "payment_terms_days", "credit_limit",
                  "account", "notes", "is_active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2}), "credit_limit": MONEY_W}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = postable(is_control=True)
        if self.instance.pk and self.instance.kind in ("patient", "doctor"):
            self.fields["kind"].disabled = True
            self.fields["name"].disabled = True
        else:
            self.fields["kind"].choices = [c for c in PartyKind.choices if c[0] not in ("patient", "doctor")]


class ProjectForm(StyledModelForm):
    class Meta:
        model = Project
        fields = ["code", "name_ar", "name_en", "budget", "description", "is_active"]
        widgets = {"budget": MONEY_W}


class ExpenseCategoryForm(StyledModelForm):
    label_keys = {"group": "expense_group", "account": "account"}

    class Meta:
        model = ExpenseCategory
        fields = ["code", "name_ar", "name_en", "group", "account", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = postable(type="expense")
        if self.instance.pk:
            self.fields["code"].disabled = True


class TaxRateForm(StyledModelForm):
    label_keys = {"kind": "tax_kind", "rate": "tax_rate_pct"}

    class Meta:
        model = TaxRate
        fields = ["name_ar", "name_en", "rate", "kind", "account", "legal_reference", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = postable()


class CurrencyForm(StyledModelForm):
    class Meta:
        model = Currency
        fields = ["code", "name_ar", "name_en", "symbol", "is_active"]

    def clean_code(self):
        return self.cleaned_data["code"].upper()


class RateForm(StyledModelForm):
    class Meta:
        model = ExchangeRate
        fields = ["currency", "date", "rate"]
        widgets = {"date": DateInput(), "rate": RATE_W}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].queryset = Currency.objects.filter(is_base=False, is_active=True)
        self.fields["rate"].help_text = tr("rate_help")


class AssetCategoryForm(StyledModelForm):
    label_keys = {"method": "dep_method"}

    class Meta:
        model = AssetCategory
        fields = ["name_ar", "name_en", "asset_account", "accumulated_account", "expense_account", "life_months",
                  "method", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asset_account"].queryset = postable(type="asset")
        self.fields["accumulated_account"].queryset = postable(type="asset", nature="credit")
        self.fields["expense_account"].queryset = postable(type="expense")


class SettingsForm(StyledModelForm):
    class Meta:
        model = LedgerSettings
        fields = ["year_start_month", "require_review", "require_approval", "allow_self_approval"] + [
            k for k, _, _ in SETTING_ACCOUNTS]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, label, typ in SETTING_ACCOUNTS:
            f = self.fields[key]
            f.label = tr(label)
            f.queryset = postable(type=typ)
            f.required = True
        if self.instance.pk and self.instance.year_start_month and self.instance.pk:
            from .models import FiscalYear

            if FiscalYear.objects.exists():
                self.fields["year_start_month"].disabled = True
                self.fields["year_start_month"].help_text = tr("year_start_locked")


class MethodAccountForm(StyledFormMixin, forms.Form):
    """One account select per payment method."""

    def __init__(self, *args, methods=(), **kwargs):
        super().__init__(*args, **kwargs)
        current = {m.method_id: m.account_id for m in PaymentMethodAccount.objects.all()}
        for m in methods:
            self.fields[f"m{m.pk}"] = forms.ModelChoiceField(
                queryset=postable(type="asset"), label=m.name, initial=current.get(m.pk), required=False)
        self.style_fields_keep_labels()

    def style_fields_keep_labels(self):
        for field in self.fields.values():
            field.widget.attrs["class"] = "input select"


# ---------------------------------------------------------------- treasury
class TreasuryForm(StyledModelForm):
    label_keys = {"kind": "treasury_kind", "account": "ledger_account"}
    currency = forms.ModelChoiceField(queryset=Currency.objects.filter(is_active=True), required=False)

    class Meta:
        model = Treasury
        fields = ["kind", "name_ar", "name_en", "account", "branch", "bank_name", "account_number", "iban",
                  "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        used = Treasury.objects.exclude(pk=self.instance.pk).values_list("account_id", flat=True)
        self.fields["account"].queryset = postable(type="asset").exclude(pk__in=used).exclude(is_control=True)
        self.fields["account"].required = False
        self.fields["account"].help_text = tr("treasury_account_help")
        self.fields["currency"].label = tr("currency")
        self.fields["currency"].widget.attrs["class"] = "input select"
        self.fields["currency"].help_text = tr("treasury_currency_help")
        if self.instance.pk:
            self.fields["account"].disabled = True
            self.fields.pop("currency")


class TransferForm(StyledModelForm):
    label_keys = {"source": "transfer_from", "target": "transfer_to"}

    class Meta:
        model = Transfer
        fields = ["date", "source", "target", "amount", "source_rate", "target_amount", "target_rate", "fees",
                  "reference", "description"]
        widgets = {"date": DateInput(), "amount": MONEY_W, "target_amount": MONEY_W, "fees": MONEY_W,
                   "source_rate": RATE_W, "target_rate": RATE_W}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = treasuries()
        self.fields["target"].queryset = treasuries()
        for f in ("source_rate", "target_rate", "target_amount"):
            self.fields[f].help_text = tr(f"{f}_help")
            self.fields[f].required = False

    def clean(self):
        data = super().clean()
        s, t = data.get("source"), data.get("target")
        if s and t and s == t:
            self.add_error("target", tr("transfer_same"))
        for key, treasury in (("source_rate", s), ("target_rate", t)):
            cur = treasury.account.currency if treasury else None
            if cur and not cur.is_base:
                if not data.get(key):
                    try:
                        data[key] = cur.rate_on(data.get("date") or timezone.localdate())
                    except Exception:
                        self.add_error(key, tr("rate_required"))
            else:
                data[key] = Decimal(1)
        if s and t and (s.account.currency_id or None) != (t.account.currency_id or None) and not data.get("target_amount"):
            self.add_error("target_amount", tr("target_amount_required"))
        return data


class ReconciliationForm(StyledModelForm):
    class Meta:
        model = BankReconciliation
        fields = ["treasury", "statement_date", "statement_balance", "notes"]
        widgets = {"statement_date": DateInput(), "statement_balance": forms.NumberInput(
            attrs={"step": "0.01", "dir": "ltr"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["treasury"].queryset = treasuries()


# ---------------------------------------------------------------- bills
class BillForm(StyledModelForm):
    label_keys = {"party_reference": "party_reference"}
    party = PartyCodeField(max_length=60, required=True)

    class Meta:
        model = Bill
        fields = ["party", "date", "due_date", "currency", "rate", "party_reference", "notes"]
        widgets = {"date": DateInput(), "due_date": DateInput(), "rate": RATE_W,
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, kind=BillKind.PURCHASE, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.fields["party"].kinds = ["supplier"] if kind == BillKind.PURCHASE else ["customer", "patient"]
        self.fields["party"].label = tr("pk_supplier" if kind == BillKind.PURCHASE else "pk_customer")
        self.fields["currency"].queryset = Currency.objects.filter(is_active=True)
        self.fields["currency"].required = False
        self.fields["rate"].help_text = tr("rate_help")
        self.fields["rate"].required = False

    def clean(self):
        data = super().clean()
        cur = data.get("currency")
        if cur and not cur.is_base:
            if not data.get("rate") or data["rate"] == 1:
                try:
                    data["rate"] = cur.rate_on(data.get("date") or timezone.localdate())
                except Exception:
                    self.add_error("rate", tr("rate_required"))
        else:
            data["rate"] = Decimal(1)
        if data.get("due_date") and data.get("date") and data["due_date"] < data["date"]:
            self.add_error("due_date", tr("due_before_date"))
        return data


class BillLineForm(StyledModelForm):
    label_keys = {"unit_price": "price", "category": "expense_category", "tax": "tax"}

    class Meta:
        model = BillLine
        fields = ["description", "category", "account", "project", "quantity", "unit_price", "tax"]
        widgets = {"unit_price": MONEY_W, "quantity": forms.NumberInput(attrs={"step": "0.001", "min": "0.001",
                                                                                "dir": "ltr"})}

    def __init__(self, *args, kind=BillKind.PURCHASE, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True)
        self.fields["account"].queryset = postable(type__in=["revenue"] if kind == BillKind.SALES
                                                   else ["expense", "asset"])
        self.fields["project"].queryset = Project.objects.filter(is_active=True)
        self.fields["tax"].queryset = TaxRate.objects.filter(is_active=True, kind__in=[
            "both", "sales" if kind == BillKind.SALES else "purchase"])
        if kind == BillKind.SALES:
            self.fields.pop("category")

    def clean(self):
        data = super().clean()
        if self.has_changed() and not data.get("DELETE") and not data.get("account") and not data.get("category"):
            self.add_error("account", tr("bill_line_needs_account", ""))
        return data


class BaseBillLineFormSet(BaseInlineFormSet):
    def __init__(self, *args, kind=BillKind.PURCHASE, **kwargs):
        self.kind = kind
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), "kind": self.kind}

    @property
    def empty_form(self):
        form = self.form(auto_id=self.auto_id, prefix=self.add_prefix("__prefix__"), empty_permitted=True,
                         use_required_attribute=False, kind=self.kind)
        self.add_fields(form, None)
        return form

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not any(f.cleaned_data and not f.cleaned_data.get("DELETE") for f in self.forms):
            raise forms.ValidationError(tr("bill_needs_lines"))


BillLineFormSet = inlineformset_factory(Bill, BillLine, form=BillLineForm, formset=BaseBillLineFormSet,
                                        extra=0, min_num=1, can_delete=True)


# ---------------------------------------------------------------- vouchers
class VoucherForm(StyledModelForm):
    label_keys = {"account": "counter_account", "category": "expense_category", "reference": "cheque_ref"}
    party = PartyCodeField(max_length=60)

    class Meta:
        model = Voucher
        fields = ["date", "treasury", "amount", "rate", "party", "account", "category", "project", "payee",
                  "description", "reference"]
        widgets = {"date": DateInput(), "amount": MONEY_W, "rate": RATE_W}

    def __init__(self, *args, kind=VoucherKind.RECEIPT, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.fields["treasury"].queryset = treasuries()
        self.fields["account"].queryset = postable(allow_manual=True, is_control=False).exclude(
            treasury__isnull=False)
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True)
        self.fields["project"].queryset = Project.objects.filter(is_active=True)
        self.fields["rate"].required = False
        self.fields["rate"].help_text = tr("rate_help")
        self.fields["party"].help_text = tr("voucher_party_help")
        if kind == VoucherKind.RECEIPT:
            self.fields.pop("category")

    def clean(self):
        data = super().clean()
        targets = [bool(data.get("party")), bool(data.get("account")), bool(data.get("category"))]
        if sum(targets) != 1:
            self.add_error(None, tr("voucher_one_target"))
        t = data.get("treasury")
        cur = t.account.currency if t else None
        if cur and not cur.is_base:
            if not data.get("rate") or data["rate"] == 1:
                try:
                    data["rate"] = cur.rate_on(data.get("date") or timezone.localdate())
                except Exception:
                    self.add_error("rate", tr("rate_required"))
        else:
            data["rate"] = Decimal(1)
        return data


class AllocationForm(forms.Form):
    bill = forms.IntegerField(widget=forms.HiddenInput)
    amount = forms.DecimalField(required=False, max_digits=12, decimal_places=2, min_value=0,
                                widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "input",
                                                                "dir": "ltr"}))


AllocationFormSet = formset_factory(AllocationForm, extra=0)


# ---------------------------------------------------------------- custody
class CustodyForm(StyledModelForm):
    label_keys = {"kind": "custody_kind", "holder": "custody_holder"}
    holder = PartyCodeField(max_length=60, required=True, kinds=["employee", "doctor"])
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"), widget=MONEY_W)
    treasury = forms.ModelChoiceField(queryset=Treasury.objects.none())

    class Meta:
        model = Custody
        fields = ["kind", "holder", "date", "purpose", "project"]
        widgets = {"date": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["treasury"].queryset = treasuries()
        self.fields["project"].queryset = Project.objects.filter(is_active=True)
        self.style_fields()
        self.fields["amount"].label = tr("issued_amount")
        self.fields["treasury"].label = tr("paid_from")


class MovementForm(StyledModelForm):
    class Meta:
        model = CustodyMovement
        fields = ["kind", "date", "amount", "treasury", "category", "account", "project", "description",
                  "reference"]
        widgets = {"date": DateInput(), "amount": MONEY_W}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["treasury"].queryset = treasuries()
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True)
        self.fields["account"].queryset = postable(type__in=["expense", "asset"], is_control=False)
        self.fields["project"].queryset = Project.objects.filter(is_active=True)
        self.fields["treasury"].help_text = tr("movement_treasury_help")

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        if kind in (MovementKind.ISSUE, MovementKind.RETURN) and not data.get("treasury"):
            self.add_error("treasury", tr("treasury_required"))
        if kind == MovementKind.EXPENSE and not (data.get("category") or data.get("account")):
            self.add_error("category", tr("voucher_needs_target"))
        return data


# ---------------------------------------------------------------- fixed assets
class AssetForm(StyledModelForm):
    label_keys = {"method": "dep_method", "funding": "funding", "supplier": "pk_supplier"}
    supplier = PartyCodeField(max_length=60, kinds=["supplier"])

    class Meta:
        model = FixedAsset
        fields = ["name_ar", "name_en", "category", "branch", "project", "purchase_date", "in_service_date",
                  "cost", "salvage_value", "life_months", "method", "declining_rate", "funding", "treasury",
                  "supplier", "opening_accumulated", "serial_number", "location", "notes"]
        widgets = {"purchase_date": DateInput(), "in_service_date": DateInput(), "cost": MONEY_W,
                   "salvage_value": MONEY_W, "opening_accumulated": MONEY_W,
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = AssetCategory.objects.filter(is_active=True)
        self.fields["treasury"].queryset = treasuries()
        self.fields["project"].queryset = Project.objects.filter(is_active=True)
        self.fields["life_months"].required = False
        self.fields["life_months"].help_text = tr("life_months_help")
        self.fields["declining_rate"].help_text = tr("declining_rate_help")
        self.fields["opening_accumulated"].help_text = tr("opening_accumulated_help")

    def clean(self):
        data = super().clean()
        if not data.get("life_months") and data.get("category"):
            data["life_months"] = data["category"].life_months
        funding = data.get("funding")
        if funding == FundingKind.TREASURY and not data.get("treasury"):
            self.add_error("treasury", tr("treasury_required"))
        if funding == FundingKind.SUPPLIER and not data.get("supplier"):
            self.add_error("supplier", tr("supplier_required"))
        if (data.get("cost") or 0) <= (data.get("salvage_value") or 0):
            self.add_error("salvage_value", tr("salvage_too_high"))
        if data.get("opening_accumulated") and funding != FundingKind.OPENING:
            self.add_error("opening_accumulated", tr("opening_acc_needs_opening"))
        if (data.get("in_service_date") and data.get("purchase_date")
                and data["in_service_date"] < data["purchase_date"]):
            self.add_error("in_service_date", tr("in_service_before_purchase"))
        return data


class ImproveForm(StyledFormMixin, forms.Form):
    date = forms.DateField(widget=DateInput())
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"), widget=MONEY_W)
    extend_months = forms.IntegerField(min_value=0, initial=0, required=False)
    funding = forms.ChoiceField(choices=[c for c in FundingKind.choices if c[0] != "opening"])
    treasury = forms.ModelChoiceField(queryset=Treasury.objects.none(), required=False)
    supplier = PartyCodeField(max_length=60, kinds=["supplier"])
    description = forms.CharField(max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["treasury"].queryset = treasuries()
        self.style_fields()
        self.fields["supplier"].label = tr("pk_supplier")


class DisposeForm(StyledFormMixin, forms.Form):
    date = forms.DateField(widget=DateInput())
    proceeds = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, initial=0, widget=MONEY_W)
    treasury = forms.ModelChoiceField(queryset=Treasury.objects.none(), required=False)
    description = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["treasury"].queryset = treasuries()
        self.style_fields()


class RunForm(StyledFormMixin, forms.Form):
    date = forms.DateField(widget=DateInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()
        self.fields["date"].label = tr("depreciate_up_to")


class AttachmentForm(forms.Form):
    file = forms.FileField()

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > settings.LEDGER_MAX_ATTACHMENT:
            raise forms.ValidationError(tr("attachment_too_big"))
        return f
