"""Pages of the accounting module. Money rules live in engine/posting/assets/closing; views only
collect input, check permissions and call them."""
import json
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView

from accounts.models import User
from accounts.roles import PERMISSIONS, Role, has_perm, role_allows
from core.audit import log
from core.i18n import current_lang, tr
from core.mixins import Col, CrudFormMixin, CrudListView, PermissionMixin
from core.models import PaymentMethod

from . import assets as asset_svc
from . import closing, engine, posting, reports
from .balances import Tree, account_balance, party_balance, totals_by_account, treasury_balance
from .balances import lines as line_qs
from .forms import (
    AccountForm, AllocationFormSet, AssetCategoryForm, AssetForm, AttachmentForm, BillForm, BillLineFormSet,
    CurrencyForm, CustodyForm, DisposeForm, EntryForm, ExpenseCategoryForm, ImproveForm, LineFormSet,
    MethodAccountForm, MovementForm, PartyForm, ProjectForm, RateForm, ReconciliationForm, RunForm, SettingsForm,
    TaxRateForm, TransferForm, TreasuryForm, VoucherForm,
)
from .models import (
    ZERO, Account, AssetCategory, AssetStatus, Attachment, BankReconciliation, Bill, BillKind, Currency, Custody,
    CustodyMovement, DepreciationRun, DocStatus, EntryKind, EntryStatus, ExchangeRate, ExpenseCategory,
    FiscalYear, FixedAsset, JournalEntry, JournalLine, LedgerError, LedgerSettings, MovementKind,
    Party, PartyKind, PaymentMethodAccount, PermissionOverride, Project, TaxRate, Transfer, Treasury, Voucher,
    VoucherAllocation, VoucherKind,
)

STATUS_BADGE = {"draft": "badge-muted", "reviewed": "badge-info", "approved": "badge-accent", "posted": "badge-good",
                "cancelled": "badge-bad", "paid": "badge-good", "partial": "badge-accent", "unpaid": "badge-bad"}


def need(request, module, action):
    if not has_perm(request.user, module, action):
        raise PermissionDenied


def party_datalist():
    return Party.objects.filter(is_active=True).only("code", "name").order_by("kind", "name")


# ======================================================================= dashboard
class Home(PermissionMixin, TemplateView):
    module = "acc_home"
    template_name = "ledger/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        s = LedgerSettings.get()
        today = timezone.localdate()
        m_start = date(today.year, today.month, 1)
        y_start, _ = engine.year_bounds(today)
        tree = Tree()

        def pnl(start):
            t = tree.rollup(totals_by_account(line_qs(date_from=start, date_to=today).exclude(
                entry__kind=EntryKind.CLOSING)))
            rev = sum(((t[a.pk][1] - t[a.pk][0]) for a in tree.roots() if a.type == "revenue"), ZERO)
            exp = sum(((t[a.pk][0] - t[a.pk][1]) for a in tree.roots() if a.type == "expense"), ZERO)
            return rev, exp, rev - exp

        def bal(key):
            a = getattr(s, key)
            return account_balance(a) if a else ZERO

        treas = [{"t": t, "balance": treasury_balance(t),
                  "fx": treasury_balance(t, in_currency=True) if t.currency and not t.currency.is_base else None}
                 for t in Treasury.objects.filter(is_active=True).select_related("account__currency")]
        pending = JournalEntry.objects.exclude(status=EntryStatus.POSTED).values("status").annotate(n=Count("id"))
        overdue = [b for b in Bill.objects.filter(status=DocStatus.POSTED, due_date__lt=today)
                   .select_related("party") if b.balance > 0][:8]
        ctx.update(
            title=tr("nav_acc_home"), month=pnl(m_start), year=pnl(y_start), treasuries=treas,
            cash_total=sum((x["balance"] for x in treas), ZERO),
            receivable=bal("patients_receivable") + bal("customers_receivable"),
            payable=bal("suppliers_payable"), doctors=bal("doctors_payable"),
            pending={p["status"]: p["n"] for p in pending}, overdue=overdue,
            recent=JournalEntry.objects.select_related("created_by")[:8],
            unsynced=self.unsynced_count(), today=today,
            open_custody=Custody.objects.filter(is_closed=False).count(),
        )
        return ctx

    def unsynced_count(self):
        from finance.models import Invoice, Payment

        synced = set(JournalEntry.objects.filter(source_model="finance.payment").values_list("source_id", flat=True))
        n = Payment.objects.exclude(pk__in=[int(x) for x in synced if x.isdigit()]).count()
        synced_inv = set(JournalEntry.objects.filter(source_model="finance.invoice").values_list("source_id",
                                                                                                flat=True))
        n += Invoice.objects.filter(is_cancelled=False, total__gt=0).exclude(
            pk__in=[int(x) for x in synced_inv if x.isdigit()]).count()
        return n


class SyncFinance(PermissionMixin, View):
    module, action = "acc_settings", "edit"

    def post(self, request):
        n = posting.sync_all_finance()
        log(request, "post", None, [f"backfill {n} entries"])
        messages.success(request, tr("sync_done", n))
        return redirect("acc_home")


# ======================================================================= chart of accounts
class AccountTree(PermissionMixin, TemplateView):
    module = "coa"
    template_name = "ledger/accounts.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        show_inactive = self.request.GET.get("inactive") == "1"
        tree = Tree(Account.objects.select_related("currency", "treasury").order_by("code"))
        sums = tree.rollup(totals_by_account(line_qs()))
        rows = []
        for a, depth in tree.ordered():
            if not a.is_active and not show_inactive:
                continue
            d, c = sums[a.pk]
            rows.append({"a": a, "depth": depth, "balance": (d - c) if a.nature == "debit" else (c - d)})
        ctx.update(title=tr("nav_coa"), rows=rows, show_inactive=show_inactive,
                   can_edit=has_perm(self.request.user, "coa", "edit"))
        return ctx


class AccountFormMixin(CrudFormMixin):
    module, model, form_class = "coa", Account, AccountForm
    entity_key, list_url = "ent_account", "account_list"


class AccountCreate(AccountFormMixin, CreateView):
    def get_initial(self):
        return {**super().get_initial(), "parent": self.request.GET.get("parent")}


class AccountUpdate(AccountFormMixin, UpdateView):
    pass


# ======================================================================= journal entries
class JournalList(CrudListView):
    module = "journal"
    title_key, subtitle_key = "nav_journal", "journal_sub"
    has_status = False
    create_url = "journal_create"
    search_fields = ["number", "description", "reference", "source_label"]
    row_actions = [("open", "journal_detail", "clipboard", "journal.view")]
    columns = [
        Col("entry_no", lambda o: o.number, "title", sub=lambda o: o.date.strftime("%Y-%m-%d")),
        Col("description", lambda o: o.description, sub=lambda o: o.reference),
        Col("entry_kind", lambda o: o.get_kind_display(), "badge"),
        Col("amount", lambda o: o.total, "money"),
        Col("status", lambda o: o.get_status_display() + (f" · {tr('reversed')}" if o.is_reversed else ""), "badge"),
        Col("created_by", lambda o: str(o.created_by or tr("system")), "muted"),
    ]

    def get_queryset(self):
        self.queryset = JournalEntry.objects.select_related("created_by")
        self.filters = [("state", "status", EntryStatus.choices), ("kind", "kind", EntryKind.choices)]
        qs = super().get_queryset()
        p = self.request.GET
        if p.get("from"):
            qs = qs.filter(date__gte=p["from"])
        if p.get("to"):
            qs = qs.filter(date__lte=p["to"])
        return qs


def entry_initial_lines(entry=None, copy_from=None):
    src = entry or copy_from
    if src is None:
        return [{}, {}]
    return [{"account": ln.account_id, "description": ln.description, "debit": ln.debit or None,
             "credit": ln.credit or None, "party": ln.party.code if ln.party_id else "", "project": ln.project_id,
             "fx_amount": ln.fx_amount}
            for ln in src.lines.select_related("party")]


class EntryFormView(PermissionMixin, TemplateView):
    module, action = "journal", "edit"
    template_name = "ledger/entry_form.html"
    entry = None

    def setup_entry(self, pk):
        if pk:
            self.entry = get_object_or_404(JournalEntry, pk=pk)
            if not self.entry.is_editable:
                raise LedgerError(tr("posted_cannot_change"))

    def get(self, request, pk=None):
        self.setup_entry(pk)
        copy = JournalEntry.objects.filter(pk=request.GET.get("copy") or 0).first()
        form = EntryForm(instance=self.entry, initial={"date": timezone.localdate(),
                                                       "description": copy.description if copy else ""})
        formset = LineFormSet(prefix="lines", initial=entry_initial_lines(self.entry, copy))
        return self.show(form, formset)

    def show(self, form, formset):
        accounts = {str(a.pk): {"control": a.is_control, "fx": a.currency.code if a.currency_id else ""}
                    for a in Account.objects.filter(is_group=False).select_related("currency")}
        return self.render_to_response(self.get_context_data(
            form=form, formset=formset, entry=self.entry, parties=party_datalist(), accounts=accounts,
            title=f"{tr('edit')} {self.entry.number}" if self.entry else tr("new_entry"),
            back_url=reverse("journal_detail", args=[self.entry.pk]) if self.entry else reverse("journal_list")))

    def post(self, request, pk=None):
        self.setup_entry(pk)
        form = EntryForm(request.POST, instance=self.entry)
        formset = LineFormSet(request.POST, prefix="lines")
        if not (form.is_valid() and formset.is_valid()):
            messages.error(request, tr("fix_errors"))
            return self.show(form, formset)
        lines = []
        for f in formset.forms:
            d = f.cleaned_data
            if not d or f.is_blank:
                continue
            acc = d["account"]
            cur = acc.currency if acc.currency_id and not acc.currency.is_base else None
            amount = d.get("debit") or d.get("credit") or ZERO
            fx_kw = {}
            if cur:
                fx = d.get("fx_amount") or None
                fx_kw = {"currency": cur, "fx_amount": fx, "fx_rate": (amount / fx) if fx else None}
            lines.append(engine.Line(account=acc, debit=d.get("debit") or ZERO, credit=d.get("credit") or ZERO,
                                     description=d.get("description", ""), party=d.get("party"),
                                     project=d.get("project"), branch=request.branch, **fx_kw))
        data = form.cleaned_data
        if self.entry is None:
            entry = engine.create_entry(
                date=data["date"], description=data["description"], lines=lines, user=request.user,
                kind=data["kind"], status=EntryStatus.DRAFT, branch=request.branch, reference=data["reference"],
                auto_reverse_on=data.get("auto_reverse_on"))
            log(request, "create", entry)
        else:
            entry = form.save(commit=False)
            entry.period = engine.get_period(entry.date)
            entry.total = engine.validate_lines(engine.clean_lines(lines), manual=True)
            entry.status = EntryStatus.DRAFT
            entry.save()
            before = sorted(f"{ln.account.code} {ln.debit}/{ln.credit}" for ln in engine.lines_of(entry))
            engine.write_lines(entry, engine.clean_lines(lines))
            after = sorted(f"{ln.account.code} {ln.debit}/{ln.credit}" for ln in engine.lines_of(entry))
            changes = form.changed_data + ([f"lines: {', '.join(before)} → {', '.join(after)}"]
                                           if before != after else [])
            log(request, "update", entry, changes)
        messages.success(request, tr("entry_saved", entry.number))
        return redirect("journal_detail", entry.pk)


def attachments_of(obj):
    return Attachment.objects.filter(target_model=obj._meta.label_lower, target_id=str(obj.pk)).defer("data")


def history_of(obj):
    from core.models import AuditLog

    return AuditLog.objects.filter(model=obj._meta.model_name, object_id=str(obj.pk))[:50]


class EntryDetail(PermissionMixin, TemplateView):
    module = "journal"
    template_name = "ledger/entry_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        e = get_object_or_404(JournalEntry.objects.select_related(
            "created_by", "reviewed_by", "approved_by", "posted_by", "period", "reversal_of", "branch"),
            pk=kwargs["pk"])
        u = self.request.user
        s = LedgerSettings.get()
        lines = list(e.lines.select_related("account", "party", "project", "currency", "branch"))
        draftish = e.status != EntryStatus.POSTED
        editable = e.is_editable
        ctx.update(
            title=e.number, e=e, lines=lines, debit=sum((ln.debit for ln in lines), ZERO),
            credit=sum((ln.credit for ln in lines), ZERO), attachments=attachments_of(e), history=history_of(e),
            reversed_by=JournalEntry.objects.filter(reversal_of=e).first(),
            source_url=source_url(e), badge=STATUS_BADGE.get(e.status, ""), settings=s,
            can=dict(
                edit=editable and has_perm(u, "journal", "edit"),
                delete=editable and has_perm(u, "journal", "edit"),
                review=editable and e.status == EntryStatus.DRAFT and s.require_review
                and has_perm(u, "journal", "review"),
                approve=editable and e.status in (EntryStatus.DRAFT, EntryStatus.REVIEWED)
                and (s.require_approval or s.require_review) and has_perm(u, "journal", "approve"),
                post=editable and draftish and has_perm(u, "journal", "post"),
                give_back=editable and e.status in (EntryStatus.REVIEWED, EntryStatus.APPROVED)
                and (has_perm(u, "journal", "review") or has_perm(u, "journal", "approve")),
                reverse=e.status == EntryStatus.POSTED and not e.is_reversed and e.kind != EntryKind.REVERSAL
                and not e.source_model and has_perm(u, "journal", "reverse"),
                attach=has_perm(u, "journal", "edit"),
                copy=has_perm(u, "journal", "edit"),
            ),
            today=timezone.localdate(),
        )
        return ctx


def source_url(entry):
    m, pk = entry.source_model, entry.source_id
    if not m or not pk:
        return ""
    names = {"finance.invoice": "invoice_detail", "ledger.voucher": "voucher_detail", "ledger.bill": "bill_detail",
             "ledger.transfer": "transfer_detail", "ledger.fixedasset": "asset_detail",
             "ledger.depreciationrun": "depreciation"}
    if m == "finance.payment":
        from finance.models import Payment

        p = Payment.objects.filter(pk=pk).first()
        return reverse("invoice_detail", args=[p.invoice_id]) if p else ""
    if m == "ledger.custodymovement":
        mv = CustodyMovement.objects.filter(pk=pk).first()
        return reverse("custody_detail", args=[mv.custody_id]) if mv else ""
    if m == "finance.doctorsettlement" or m == "finance.doctorpayout":
        return reverse("doctor_accounts")
    if m == "finance.expense":
        return reverse("expense_list")
    name = names.get(m)
    if name == "depreciation":
        return reverse(name)
    return reverse(name, args=[pk]) if name else ""


class EntryAction(PermissionMixin, View):
    module = "journal"

    def post(self, request, pk):
        e = get_object_or_404(JournalEntry, pk=pk)
        do = request.POST.get("do")
        note = request.POST.get("note", "")[:255]
        u = request.user
        if do == "review":
            need(request, "journal", "review")
            engine.submit_review(e, u)
            log(request, "review", e)
            messages.success(request, tr("entry_reviewed"))
        elif do == "approve":
            need(request, "journal", "approve")
            engine.approve(e, u)
            log(request, "approve", e)
            messages.success(request, tr("entry_approved"))
        elif do == "post":
            need(request, "journal", "post")
            engine.post(e, u)
            log(request, "post", e)
            messages.success(request, tr("entry_posted", e.number))
        elif do == "return":
            if not (has_perm(u, "journal", "review") or has_perm(u, "journal", "approve")):
                raise PermissionDenied
            engine.return_to_draft(e, u, note)
            log(request, "reject", e, [note] if note else [])
            messages.success(request, tr("entry_returned"))
        elif do in ("reverse", "correct"):
            need(request, "journal", "reverse")
            if do == "correct":
                need(request, "journal", "edit")
            if e.source_model:
                raise LedgerError(tr("reverse_from_document"))
            day = reports._date(request.POST.get("date"), e.date)
            rev = engine.reverse(e, u, day=day, note=note)
            log(request, "reverse", e, [rev.number] + ([note] if note else []))
            if do == "correct":
                new = engine.copy_as_draft(e, u)
                log(request, "create", new, [f"correction of {e.number}"])
                messages.success(request, tr("entry_corrected", rev.number, new.number))
                return redirect("journal_edit", new.pk)
            messages.success(request, tr("entry_reversed", rev.number))
            return redirect("journal_detail", rev.pk)
        elif do == "delete":
            need(request, "journal", "edit")
            if not e.is_editable:
                raise LedgerError(tr("posted_cannot_delete"))
            log(request, "delete", e)
            Attachment.objects.filter(target_model="ledger.journalentry", target_id=str(e.pk)).delete()
            e.delete()
            messages.success(request, tr("entry_deleted"))
            return redirect("journal_list")
        else:
            raise Http404
        return redirect("journal_detail", e.pk)


# ---------------------------------------------------------------- attachments (any document)
ATTACH_TARGETS = {
    "journalentry": (JournalEntry, "journal"), "voucher": (Voucher, "vouchers"), "bill": (Bill, "bills"),
    "transfer": (Transfer, "treasury"), "custody": (Custody, "custody"), "fixedasset": (FixedAsset, "assets"),
}


class AttachmentUpload(PermissionMixin, View):
    module = "acc_home"

    def post(self, request, target, pk):
        model, module = ATTACH_TARGETS.get(target, (None, None))
        if model is None:
            raise Http404
        need(request, module, "edit")
        obj = get_object_or_404(model, pk=pk)
        form = AttachmentForm(request.POST, request.FILES)
        if form.is_valid():
            f = form.cleaned_data["file"]
            data = f.read()
            att = Attachment.objects.create(target_model=obj._meta.label_lower, target_id=str(obj.pk),
                                            name=f.name[:200], content_type=f.content_type or "application/octet-stream",
                                            size=len(data), data=data, uploaded_by=request.user)
            log(request, "create", obj, [f"attachment: {att.name}"])
            messages.success(request, tr("attachment_added"))
        else:
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect(request.META.get("HTTP_REFERER") or "acc_home")


class AttachmentGet(PermissionMixin, View):
    module = "acc_home"

    def get(self, request, pk):
        att = get_object_or_404(Attachment, pk=pk)
        module = next((m for _, (model, m) in ATTACH_TARGETS.items() if model._meta.label_lower == att.target_model),
                      "acc_home")
        need(request, module, "view")
        resp = HttpResponse(bytes(att.data), content_type=att.content_type)
        inline = att.content_type.startswith("image/") or att.content_type == "application/pdf"
        resp["Content-Disposition"] = f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{_quote(att.name)}"
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
        return resp

    def post(self, request, pk):
        att = get_object_or_404(Attachment, pk=pk)
        module = next((m for _, (model, m) in ATTACH_TARGETS.items() if model._meta.label_lower == att.target_model),
                      "acc_home")
        need(request, module, "edit")
        if att.target_model == "ledger.journalentry":
            e = JournalEntry.objects.filter(pk=att.target_id).first()
            if e and e.status == EntryStatus.POSTED:
                raise LedgerError(tr("posted_attachment_locked"))
        log(request, "delete", att, [att.name])
        att.delete()
        messages.success(request, tr("attachment_removed"))
        return redirect(request.META.get("HTTP_REFERER") or "acc_home")


def _quote(name):
    from urllib.parse import quote

    return quote(name)


# ======================================================================= documents (shared)
class DocumentDetail(PermissionMixin, TemplateView):
    template_name = "ledger/document.html"
    model = None
    print_template = "ledger/document_print.html"

    def get_template_names(self):
        return [self.print_template] if self.request.GET.get("print") else [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        doc = get_object_or_404(self.model, pk=kwargs["pk"])
        u = self.request.user
        entry_lines = list(doc.entry.lines.select_related("account", "party")) if doc.entry_id else []
        ctx.update(
            doc=doc, title=f"{self.doc_title(doc)} {doc.number}", attachments=attachments_of(doc),
            entry_lines=entry_lines, history=history_of(doc), badge=STATUS_BADGE.get(doc.status, ""),
            target=doc._meta.model_name, fields=self.fields(doc), back_url=reverse(self.list_url),
            can=dict(edit=doc.status == DocStatus.DRAFT and has_perm(u, self.module, "edit"),
                     post=doc.status == DocStatus.DRAFT and has_perm(u, self.module, "post"),
                     cancel=doc.status != DocStatus.CANCELLED and has_perm(u, self.module, "cancel"),
                     attach=has_perm(u, self.module, "edit")),
            edit_url=reverse(self.edit_url, args=[doc.pk]) if self.edit_url else "",
            action_url=reverse(self.action_url, args=[doc.pk]),
        )
        ctx.update(self.extra(doc))
        return ctx

    def doc_title(self, doc):
        return doc.get_kind_display() if hasattr(doc, "get_kind_display") else ""

    def fields(self, doc):
        return []

    def extra(self, doc):
        return {}


class DocumentAction(PermissionMixin, View):
    model = None

    def post(self, request, pk):
        doc = get_object_or_404(self.model, pk=pk)
        do = request.POST.get("do")
        if do == "post":
            need(request, self.module, "post")
            posting.post_document(doc, request.user)
            log(request, "post", doc)
            messages.success(request, tr("doc_posted", doc.number))
        elif do == "cancel":
            need(request, self.module, "cancel")
            reason = request.POST.get("reason", "")
            posting.cancel_document(doc, request.user, reason)
            log(request, "status", doc, ["cancelled", reason])
            messages.success(request, tr("doc_cancelled", doc.number))
        else:
            raise Http404
        return redirect(self.detail_url, doc.pk)


def maybe_post(request, doc, module):
    """Post straight away when the user may post and asked for it ("save and post")."""
    if request.POST.get("and_post") and has_perm(request.user, module, "post"):
        posting.post_document(doc, request.user)
        log(request, "post", doc)
        messages.success(request, tr("doc_posted", doc.number))
    else:
        messages.success(request, tr("doc_saved_draft", doc.number))


# ======================================================================= bills
BILL_STATES = ["draft", "unpaid", "partial", "paid", "cancelled"]


class BillList(CrudListView):
    module = "bills"
    title_key, subtitle_key = "nav_bills", "bills_sub"
    has_status = False
    search_fields = ["number", "party__name", "party_reference", "notes"]
    row_actions = [("open", "bill_detail", "clipboard", "bills.view")]
    header_links = [("new_sales_bill", "bill_create_sales", "plus", "bills.edit"),
                    ("new_purchase_bill", "bill_create_purchase", "plus", "bills.edit")]
    columns = [
        Col("number", lambda o: o.number, "title", sub=lambda o: o.date.strftime("%Y-%m-%d")),
        Col("type", lambda o: o.get_kind_display(), "badge"),
        Col("party", lambda o: o.party.name, sub=lambda o: o.party_reference),
        Col("total", lambda o: o.total, "money", sub=lambda o: o.currency.code if o.currency_id else ""),
        Col("paid", lambda o: o.paid, "money-good"),
        Col("balance", lambda o: o.balance, "money"),
        Col("due_date", lambda o: o.due_date or "", "date"),
        Col("status", lambda o: tr(f"bs_{o.payment_state}") + (f" · {tr('overdue')}" if o.is_overdue else ""),
            "badge"),
    ]

    def get_queryset(self):
        self.queryset = Bill.objects.select_related("party", "currency")
        self.filters = [("kind", "kind", BillKind.choices)]
        qs = super().get_queryset()
        state = self.request.GET.get("state")
        if state in ("draft", "cancelled"):
            qs = qs.filter(status=state)
        elif state == "paid":
            qs = qs.filter(status="posted", total__gt=0, paid__gte=models_F("total"))
        elif state == "partial":
            qs = qs.filter(status="posted", paid__gt=0, paid__lt=models_F("total"))
        elif state == "unpaid":
            qs = qs.filter(status="posted", paid=0)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_controls"].insert(0, {"param": "state", "value": self.request.GET.get("state", ""),
                                          "label": tr("status"),
                                          "choices": [(s, tr(f"bs_{s}")) for s in BILL_STATES]})
        return ctx


def models_F(name):
    from django.db.models import F

    return F(name)


class BillFormView(PermissionMixin, TemplateView):
    module, action = "bills", "edit"
    template_name = "ledger/bill_form.html"
    bill = None
    kind = BillKind.PURCHASE

    def setup_bill(self, pk):
        if pk:
            self.bill = get_object_or_404(Bill, pk=pk)
            if self.bill.status != DocStatus.DRAFT:
                raise LedgerError(tr("posted_cannot_change"))
            self.kind = self.bill.kind

    def get(self, request, pk=None, kind=None):
        self.kind = kind or self.kind
        self.setup_bill(pk)
        initial = {"date": timezone.localdate()}
        if request.GET.get("party"):
            initial["party"] = request.GET["party"]
        form = BillForm(instance=self.bill, kind=self.kind, initial=initial)
        formset = BillLineFormSet(instance=self.bill or Bill(), prefix="lines", kind=self.kind)
        return self.show(form, formset)

    def show(self, form, formset):
        taxes = {str(t.pk): str(t.rate) for t in TaxRate.objects.filter(is_active=True)}
        title = (f"{tr('edit')} {self.bill.number}" if self.bill
                 else tr("new_sales_bill" if self.kind == BillKind.SALES else "new_purchase_bill"))
        return self.render_to_response(self.get_context_data(
            form=form, formset=formset, bill=self.bill, kind=self.kind, taxes=taxes, parties=party_datalist(),
            title=title, can_post=has_perm(self.request.user, "bills", "post"),
            back_url=reverse("bill_detail", args=[self.bill.pk]) if self.bill else reverse("bill_list")))

    def post(self, request, pk=None, kind=None):
        self.kind = kind or self.kind
        self.setup_bill(pk)
        form = BillForm(request.POST, instance=self.bill, kind=self.kind)
        formset = BillLineFormSet(request.POST, instance=self.bill or Bill(), prefix="lines", kind=self.kind)
        if not (form.is_valid() and formset.is_valid()):
            messages.error(request, tr("fix_errors"))
            return self.show(form, formset)
        is_new = self.bill is None
        bill = form.save(commit=False)
        bill.kind = self.kind
        bill.rate = form.cleaned_data["rate"]
        if is_new:
            bill.created_by, bill.branch = request.user, request.branch
        bill.save()
        formset.instance = bill
        formset.save()
        bill.recalc()
        log(request, "create" if is_new else "update", bill, form.changed_data)
        maybe_post(request, bill, "bills")
        return redirect("bill_detail", bill.pk)


class BillDetail(DocumentDetail):
    module, model = "bills", Bill
    list_url, edit_url, action_url = "bill_list", "bill_edit", "bill_action"

    def fields(self, b):
        return [(tr("party"), b.party.name), (tr("party_reference"), b.party_reference or "—"),
                (tr("due_date"), b.due_date or "—"),
                (tr("currency"), f"{b.currency.code} @ {b.rate}" if b.currency_id and not b.currency.is_base else "LYD")]

    def extra(self, b):
        lines = list(b.lines.select_related("account", "category", "project", "tax"))
        allocations = b.allocations.select_related("voucher").order_by("voucher__date")
        new_voucher = reverse("voucher_create_receipt" if b.kind == BillKind.SALES else "voucher_create_payment")
        return {"bill_lines": lines, "allocations": allocations, "state": b.payment_state,
                "badge": STATUS_BADGE.get(b.payment_state, ""),
                "pay_url": f"{new_voucher}?party={b.party.code}&bill={b.pk}"
                if b.status == DocStatus.POSTED and b.balance > 0 and has_perm(self.request.user, "vouchers", "edit")
                else ""}


class BillAction(DocumentAction):
    module, model, detail_url = "bills", Bill, "bill_detail"


# ======================================================================= vouchers
class VoucherList(CrudListView):
    module = "vouchers"
    title_key, subtitle_key = "nav_vouchers", "vouchers_sub"
    has_status = False
    search_fields = ["number", "party__name", "payee", "description", "reference"]
    row_actions = [("open", "voucher_detail", "clipboard", "vouchers.view")]
    header_links = [("new_receipt", "voucher_create_receipt", "plus", "vouchers.edit"),
                    ("new_payment", "voucher_create_payment", "plus", "vouchers.edit")]
    columns = [
        Col("number", lambda o: o.number, "title", sub=lambda o: o.date.strftime("%Y-%m-%d")),
        Col("type", lambda o: o.get_kind_display(), "badge"),
        Col("party", lambda o: o.party.name if o.party_id else (o.payee or str(o.account or o.category or "—")),
            sub=lambda o: o.description),
        Col("treasury", lambda o: o.treasury.name),
        Col("amount", lambda o: o.amount, "money", sub=lambda o: o.treasury.account.currency.code
            if o.treasury.account.currency_id else ""),
        Col("status", lambda o: o.get_status_display(), "badge"),
    ]

    def get_queryset(self):
        self.queryset = Voucher.objects.select_related("party", "treasury__account__currency", "account",
                                                       "category")
        self.filters = [("kind", "kind", VoucherKind.choices), ("state", "status", DocStatus.choices),
                        ("treasury", "treasury_id", [(t.pk, t.name) for t in Treasury.objects.all()])]
        return super().get_queryset()


class VoucherFormView(PermissionMixin, TemplateView):
    module, action = "vouchers", "edit"
    template_name = "ledger/voucher_form.html"
    voucher = None
    kind = VoucherKind.RECEIPT

    def setup_voucher(self, pk):
        if pk:
            self.voucher = get_object_or_404(Voucher, pk=pk)
            if self.voucher.status != DocStatus.DRAFT:
                raise LedgerError(tr("posted_cannot_change"))
            self.kind = self.voucher.kind

    def open_bills(self, party, current=None):
        if not party:
            return []
        want = BillKind.SALES if self.kind == VoucherKind.RECEIPT else BillKind.PURCHASE
        bills = list(Bill.objects.filter(party=party, kind=want, status=DocStatus.POSTED).select_related("currency"))
        current = current or {}
        return [b for b in bills if b.balance > 0 or b.pk in current]

    def get(self, request, pk=None, kind=None):
        self.kind = kind or self.kind
        self.setup_voucher(pk)
        initial = {"date": timezone.localdate()}
        party = None
        if request.GET.get("party"):
            party = Party.objects.filter(code=request.GET["party"]).first()
            initial["party"] = request.GET["party"]
        if self.voucher:
            party = self.voucher.party
        form = VoucherForm(instance=self.voucher, kind=self.kind, initial=initial)
        current = {a.bill_id: a.amount for a in self.voucher.allocations.all()} if self.voucher else {}
        if request.GET.get("bill"):
            b = Bill.objects.filter(pk=request.GET["bill"]).first()
            if b:
                current[b.pk] = b.balance
                form.initial["amount"] = b.balance
                form.initial["description"] = f"{tr('payment_for')} {b.number}"
        bills = self.open_bills(party, current)
        allocs = AllocationFormSet(prefix="alloc", initial=[{"bill": b.pk, "amount": current.get(b.pk)}
                                                            for b in bills])
        return self.show(form, allocs, bills)

    def show(self, form, allocs, bills):
        title = (f"{tr('edit')} {self.voucher.number}" if self.voucher
                 else tr("new_receipt" if self.kind == VoucherKind.RECEIPT else "new_payment"))
        rows = list(zip(bills, allocs.forms))
        return self.render_to_response(self.get_context_data(
            form=form, allocs=allocs, alloc_rows=rows, kind=self.kind, voucher=self.voucher,
            parties=party_datalist(), title=title, can_post=has_perm(self.request.user, "vouchers", "post"),
            back_url=reverse("voucher_detail", args=[self.voucher.pk]) if self.voucher else reverse("voucher_list")))

    def post(self, request, pk=None, kind=None):
        self.kind = kind or self.kind
        self.setup_voucher(pk)
        form = VoucherForm(request.POST, instance=self.voucher, kind=self.kind)
        allocs = AllocationFormSet(request.POST, prefix="alloc")
        party = None
        if form.is_valid():
            party = form.cleaned_data.get("party")
        bills = []
        if allocs.is_valid():
            ids = [f.cleaned_data.get("bill") for f in allocs.forms if f.cleaned_data]
            bills = list(Bill.objects.filter(pk__in=ids))
            bills.sort(key=lambda b: ids.index(b.pk))
        if request.POST.get("refresh") or not form.is_valid() or not allocs.is_valid():
            if not request.POST.get("refresh"):
                messages.error(request, tr("fix_errors"))
            if request.POST.get("refresh"):
                bills = self.open_bills(party)
                allocs = AllocationFormSet(prefix="alloc", initial=[{"bill": b.pk} for b in bills])
            return self.show(form, allocs, bills)
        with transaction.atomic():
            is_new = self.voucher is None
            v = form.save(commit=False)
            v.kind = self.kind
            v.rate = form.cleaned_data["rate"]
            if is_new:
                v.created_by, v.branch = request.user, request.branch
            v.save()
            v.allocations.all().delete()
            if v.party_id:
                for f in allocs.forms:
                    amount = f.cleaned_data.get("amount")
                    if amount:
                        VoucherAllocation.objects.create(voucher=v, bill_id=f.cleaned_data["bill"], amount=amount)
                posting.check_allocations(v)
            log(request, "create" if is_new else "update", v, form.changed_data)
            maybe_post(request, v, "vouchers")
        return redirect("voucher_detail", v.pk)


class VoucherDetail(DocumentDetail):
    module, model = "vouchers", Voucher
    list_url, edit_url, action_url = "voucher_list", "voucher_edit", "voucher_action"

    def fields(self, v):
        cur = v.treasury.account.currency
        target = v.party.name if v.party_id else str(v.account or v.category or "—")
        out = [(tr("treasury"), v.treasury.name), (tr("party") if v.party_id else tr("counter_account"), target)]
        if v.payee:
            out.append((tr("payee"), v.payee))
        if cur and not cur.is_base:
            out.append((tr("currency"), f"{cur.code} @ {v.rate}"))
        out += [(tr("project"), str(v.project or "—")), (tr("cheque_ref"), v.reference or "—"),
                (tr("description"), v.description)]
        return out

    def extra(self, v):
        return {"allocations": v.allocations.select_related("bill"), "amount_words": ""}


class VoucherAction(DocumentAction):
    module, model, detail_url = "vouchers", Voucher, "voucher_detail"


# ======================================================================= treasury, transfers, reconciliation
class TreasuryList(PermissionMixin, TemplateView):
    module = "treasury"
    template_name = "ledger/treasuries.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        rows = []
        for t in Treasury.objects.select_related("account", "account__currency", "branch"):
            cur = t.account.currency
            rows.append({"t": t, "balance": treasury_balance(t),
                         "fx": treasury_balance(t, in_currency=True) if cur and not cur.is_base else None,
                         "unposted": line_qs(include_unposted=True).filter(account=t.account).exclude(
                             entry__status=EntryStatus.POSTED).count()})
        ctx.update(title=tr("nav_treasury"), rows=rows, total=sum((r["balance"] for r in rows), ZERO),
                   transfers=Transfer.objects.select_related("source", "target")[:10],
                   reconciliations=BankReconciliation.objects.select_related("treasury")[:10],
                   can_edit=has_perm(self.request.user, "treasury", "edit"),
                   can_reconcile=has_perm(self.request.user, "treasury", "reconcile"))
        return ctx


class TreasuryFormMixin(CrudFormMixin):
    module, model, form_class = "treasury", Treasury, TreasuryForm
    entity_key, list_url = "ent_treasury", "treasury_list"

    def form_valid(self, form):
        if not form.instance.pk and not form.cleaned_data.get("account"):
            # create its own ledger account under "cash on hand" or "cash at banks"
            parent = Account.objects.get(code="111" if form.cleaned_data["kind"] == "cash" else "112") \
                if Account.objects.filter(code__in=["111", "112"]).count() == 2 else None
            if parent is None:
                form.add_error("account", tr("required"))
                return self.form_invalid(form)
            codes = [c for c in parent.children.values_list("code", flat=True) if c.isdigit()]
            code = str(max(int(c) for c in codes) + 1) if codes else parent.code + "1"
            cur = form.cleaned_data.get("currency")
            form.instance.account = Account.objects.create(
                code=code, name_ar=form.cleaned_data["name_ar"], name_en=form.cleaned_data["name_en"], type="asset",
                nature="debit", parent=parent, currency=cur if cur and not cur.is_base else None)
        return super().form_valid(form)


class TreasuryCreate(TreasuryFormMixin, CreateView):
    pass


class TreasuryUpdate(TreasuryFormMixin, UpdateView):
    pass


class TransferList(CrudListView):
    module = "treasury"
    title_key, subtitle_key = "nav_transfers", "transfers_sub"
    has_status = False
    create_url = "transfer_create"
    search_fields = ["number", "description", "reference"]
    row_actions = [("open", "transfer_detail", "clipboard", "treasury.view")]
    columns = [
        Col("number", lambda o: o.number, "title", sub=lambda o: o.date.strftime("%Y-%m-%d")),
        Col("transfer_from", lambda o: o.source.name), Col("transfer_to", lambda o: o.target.name),
        Col("amount", lambda o: o.amount, "money"), Col("fees", lambda o: o.fees, "money-muted"),
        Col("status", lambda o: o.get_status_display(), "badge"),
    ]

    def get_queryset(self):
        self.queryset = Transfer.objects.select_related("source", "target")
        return super().get_queryset()


class TransferCreate(CrudFormMixin, CreateView):
    module, model, form_class = "treasury", Transfer, TransferForm
    entity_key, list_url = "ent_transfer", "transfer_list"

    def get_initial(self):
        return {"date": timezone.localdate()}

    def form_valid(self, form):
        t = form.save(commit=False)
        t.source_rate = form.cleaned_data["source_rate"]
        t.target_rate = form.cleaned_data["target_rate"]
        t.created_by, t.branch = self.request.user, self.request.branch
        t.save()
        self.object = t
        log(self.request, "create", t)
        posting.post_document(t, self.request.user)
        messages.success(self.request, tr("doc_posted", t.number))
        return redirect("transfer_detail", t.pk)


class TransferDetail(DocumentDetail):
    module, model = "treasury", Transfer
    list_url, edit_url, action_url = "transfer_list", None, "transfer_action"

    def doc_title(self, doc):
        return tr("transfer")

    def fields(self, t):
        out = [(tr("transfer_from"), t.source.name), (tr("transfer_to"), t.target.name),
               (tr("fees"), t.fees), (tr("description"), t.description or "—"), (tr("reference"), t.reference or "—")]
        if t.target_amount:
            out.append((tr("target_amount"), t.target_amount))
        return out

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can"]["cancel"] = ctx["doc"].status == DocStatus.POSTED and has_perm(self.request.user, "treasury",
                                                                                   "edit")
        return ctx


class TransferAction(DocumentAction):
    module, model, detail_url = "treasury", Transfer, "transfer_detail"

    def post(self, request, pk):
        need(request, "treasury", "edit")
        doc = get_object_or_404(Transfer, pk=pk)
        if request.POST.get("do") != "cancel":
            raise Http404
        posting.cancel_document(doc, request.user, request.POST.get("reason", ""))
        log(request, "status", doc, ["cancelled"])
        messages.success(request, tr("doc_cancelled", doc.number))
        return redirect("transfer_detail", pk)


class ReconcileCreate(CrudFormMixin, CreateView):
    module, action, model, form_class = "treasury", "reconcile", BankReconciliation, ReconciliationForm
    entity_key, list_url = "ent_reconciliation", "treasury_list"

    def get_initial(self):
        return {"statement_date": timezone.localdate(), "treasury": self.request.GET.get("treasury")}

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("reconcile_detail", args=[self.object.pk])


class ReconcileDetail(PermissionMixin, TemplateView):
    module, action = "treasury", "reconcile"
    template_name = "ledger/reconcile.html"

    def data(self, rec):
        acc = rec.treasury.account
        base = line_qs().filter(account=acc, entry__date__lte=rec.statement_date)
        lines = list(base.filter(Q(reconciliation__isnull=True) | Q(reconciliation=rec))
                     .select_related("entry").order_by("entry__date", "id"))
        cleared_before = base.filter(reconciliation__is_done=True).exclude(reconciliation=rec).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        cleared_before = (cleared_before["d"] or ZERO) - (cleared_before["c"] or ZERO)
        book = treasury_balance(rec.treasury, rec.statement_date)
        return lines, cleared_before, book

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        rec = get_object_or_404(BankReconciliation.objects.select_related("treasury__account"), pk=kwargs["pk"])
        lines, cleared_before, book = self.data(rec)
        cleared_now = sum((ln.debit - ln.credit for ln in lines if ln.reconciliation_id == rec.pk), ZERO)
        cleared = cleared_before + cleared_now
        outstanding_in = sum((ln.debit for ln in lines if ln.reconciliation_id != rec.pk), ZERO)
        outstanding_out = sum((ln.credit for ln in lines if ln.reconciliation_id != rec.pk), ZERO)
        ctx.update(title=f"{tr('nav_reconcile')} · {rec}", rec=rec, lines=lines, book=book, cleared=cleared,
                   cleared_before=cleared_before,
                   difference=rec.statement_balance - cleared, outstanding_in=outstanding_in,
                   outstanding_out=outstanding_out)
        return ctx

    def post(self, request, pk):
        rec = get_object_or_404(BankReconciliation, pk=pk)
        if rec.is_done:
            if request.POST.get("do") == "reopen":
                need(request, "periods", "reopen")
                rec.is_done = False
                rec.save()
                log(request, "reopen", rec)
            return redirect("reconcile_detail", pk)
        lines, cleared_before, _ = self.data(rec)
        ticked = {int(x) for x in request.POST.getlist("line") if x.isdigit()}
        allowed = {ln.pk for ln in lines}
        JournalLine.objects.filter(reconciliation=rec).exclude(pk__in=ticked).update(reconciliation=None)
        JournalLine.objects.filter(pk__in=ticked & allowed).update(reconciliation=rec)
        if request.POST.get("do") == "finish":
            agg = JournalLine.objects.filter(reconciliation=rec).aggregate(d=Sum("debit"), c=Sum("credit"))
            cleared = cleared_before + (agg["d"] or ZERO) - (agg["c"] or ZERO)
            if cleared != rec.statement_balance:
                raise LedgerError(tr("reconcile_not_zero", f"{rec.statement_balance - cleared:,.2f}"))
            rec.is_done, rec.done_by, rec.done_at = True, request.user, timezone.now()
            rec.save()
            log(request, "close", rec)
            messages.success(request, tr("reconcile_done"))
        else:
            messages.success(request, tr("saved"))
        return redirect("reconcile_detail", pk)


# ======================================================================= custody
class CustodyList(CrudListView):
    module = "custody"
    title_key, subtitle_key = "nav_custody", "custody_sub"
    has_status = False
    create_url = "custody_create"
    search_fields = ["number", "holder__name", "purpose"]
    row_actions = [("open", "custody_detail", "clipboard", "custody.view")]
    columns = [
        Col("number", lambda o: o.number, "title", sub=lambda o: o.date.strftime("%Y-%m-%d")),
        Col("custody_kind", lambda o: o.get_kind_display(), "badge"),
        Col("custody_holder", lambda o: o.holder.name, sub=lambda o: o.purpose),
        Col("issued_amount", lambda o: o.figures()["issued"], "money"),
        Col("spent", lambda o: o.figures()["spent"], "money-muted"),
        Col("remaining", lambda o: o.figures()["remaining"], "money"),
        Col("status", lambda o: tr("closed") if o.is_closed else tr("open_"), "badge"),
    ]

    def get_queryset(self):
        self.queryset = Custody.objects.select_related("holder")
        self.filters = [("open", "is_closed", [("False", tr("open_")), ("True", tr("closed"))]),
                        ("kind", "kind", [(k, str(v)) for k, v in Custody._meta.get_field("kind").choices])]
        return super().get_queryset()


class CustodyCreate(PermissionMixin, TemplateView):
    module, action = "custody", "edit"
    template_name = "ledger/form_with_parties.html"

    def get(self, request):
        return self.show(CustodyForm(initial={"date": timezone.localdate()}))

    def show(self, form):
        return self.render_to_response(self.get_context_data(
            form=form, title=tr("new_custody"), back_url=reverse("custody_list"), parties=party_datalist()))

    def post(self, request):
        form = CustodyForm(request.POST)
        if not form.is_valid():
            messages.error(request, tr("fix_errors"))
            return self.show(form)
        c = form.save(commit=False)
        c.created_by, c.branch = request.user, request.branch
        c.save()
        mv = CustodyMovement.objects.create(custody=c, kind=MovementKind.ISSUE, date=c.date,
                                            amount=form.cleaned_data["amount"], treasury=form.cleaned_data["treasury"],
                                            description=c.purpose, created_by=request.user, branch=request.branch)
        log(request, "create", c)
        if has_perm(request.user, "custody", "post"):
            posting.post_document(mv, request.user)
        messages.success(request, tr("saved"))
        return redirect("custody_detail", c.pk)


class CustodyDetail(PermissionMixin, TemplateView):
    module = "custody"
    template_name = "ledger/custody.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        c = get_object_or_404(Custody.objects.select_related("holder", "project"), pk=kwargs["pk"])
        u = self.request.user
        ctx.update(title=f"{c.get_kind_display()} {c.number}", c=c, fig=c.figures(),
                   movements=c.movements.select_related("treasury", "category", "account", "entry", "project"),
                   form=kwargs.get("form") or MovementForm(initial={"date": timezone.localdate(), "kind": "expense"}),
                   attachments=attachments_of(c), target="custody",
                   can_edit=has_perm(u, "custody", "edit") and not c.is_closed,
                   can_post=has_perm(u, "custody", "post"), can_cancel=has_perm(u, "custody", "cancel"))
        return ctx

    def post(self, request, pk):
        c = get_object_or_404(Custody, pk=pk)
        do = request.POST.get("do")
        if do == "add":
            need(request, "custody", "edit")
            form = MovementForm(request.POST)
            if not form.is_valid():
                messages.error(request, tr("fix_errors"))
                return self.render_to_response(self.get_context_data(pk=pk, form=form))
            mv = form.save(commit=False)
            mv.custody, mv.created_by, mv.branch = c, request.user, request.branch
            posting.custody_check(mv)
            mv.save()
            log(request, "create", mv)
            if has_perm(request.user, "custody", "post"):
                posting.post_document(mv, request.user)
            messages.success(request, tr("saved"))
        elif do in ("post", "cancel"):
            mv = get_object_or_404(CustodyMovement, pk=request.POST.get("movement"), custody=c)
            if do == "post":
                need(request, "custody", "post")
                posting.post_document(mv, request.user)
            else:
                need(request, "custody", "cancel")
                posting.cancel_document(mv, request.user, request.POST.get("reason", ""))
            log(request, "status", mv, [do])
            messages.success(request, tr("saved"))
        elif do == "close":
            need(request, "custody", "edit")
            if c.figures()["remaining"] != 0:
                raise LedgerError(tr("custody_not_settled", f"{c.figures()['remaining']:,.2f}"))
            c.is_closed, c.closed_at = True, timezone.now()
            c.save()
            log(request, "close", c)
            messages.success(request, tr("custody_closed_ok"))
        else:
            raise Http404
        return redirect("custody_detail", pk)


# ======================================================================= parties
class PartyList(CrudListView):
    module = "parties"
    title_key, subtitle_key = "nav_parties", "parties_sub"
    create_url, edit_url = "party_create", "party_edit"
    search_fields = ["code", "name", "phone", "tax_no"]
    row_actions = [("open", "party_detail", "clipboard", "parties.view")]
    columns = [
        Col("code", lambda o: o.code, "code"),
        Col("name", lambda o: o.name, "title", sub=lambda o: o.phone),
        Col("type", lambda o: o.get_kind_display(), "badge"),
        Col("balance", lambda o: (o.d or ZERO) - (o.c or ZERO), "money"),
    ]

    def get_queryset(self):
        kind = self.request.GET.get("kind", "")
        base = Party.objects.all()
        if not kind:
            base = base.exclude(kind="patient")
        self.queryset = base.annotate(d=Sum("lines__debit", filter=Q(lines__entry__status="posted")),
                                      c=Sum("lines__credit", filter=Q(lines__entry__status="posted"))).order_by("name")
        self.filters = [("kind", "kind", PartyKind.choices)]
        return super().get_queryset()


class PartyFormMixin(CrudFormMixin):
    module, model, form_class = "parties", Party, PartyForm
    entity_key, list_url = "ent_party", "party_list"

    def get_success_url(self):
        return reverse("party_detail", args=[self.object.pk])


class PartyCreate(PartyFormMixin, CreateView):
    def get_initial(self):
        return {"kind": self.request.GET.get("kind", "supplier")}


class PartyUpdate(PartyFormMixin, UpdateView):
    pass


class PartyDetail(PermissionMixin, TemplateView):
    module = "parties"
    template_name = "ledger/party.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        p = get_object_or_404(Party, pk=kwargs["pk"])
        today = timezone.localdate()
        bills = list(p.bills.select_related("currency").order_by("-date")[:50])
        ctx.update(title=p.name, p=p, balance=party_balance(p), aging=reports.age_party(p, today),
                   bills=bills, open_bills=[b for b in bills if b.status == "posted" and b.balance > 0],
                   vouchers=p.vouchers.select_related("treasury").order_by("-date")[:30],
                   custodies=p.custodies.all()[:20],
                   recent=line_qs(party=p).select_related("entry", "account").order_by("-entry__date", "-id")[:20],
                   statement_url=reverse("ledger_report", args=["party_statement"]) + f"?party={p.pk}",
                   can_edit=has_perm(self.request.user, "parties", "edit"))
        for b in bills:
            b.badge = STATUS_BADGE.get(b.payment_state, "")
        return ctx


# ======================================================================= fixed assets
class AssetList(CrudListView):
    module = "assets"
    title_key, subtitle_key = "nav_assets", "assets_sub"
    has_status = False
    create_url = "asset_create"
    search_fields = ["code", "name_ar", "name_en", "serial_number", "location"]
    row_actions = [("open", "asset_detail", "clipboard", "assets.view")]
    header_links = [("dep_runs", "depreciation", "repeat", "assets.view"),
                    ("nav_asset_categories", "assetcategory_list", "list", "assets.view")]
    columns = [
        Col("code", lambda o: o.code, "code"),
        Col("name", lambda o: o.name, "title", sub=lambda o: o.category.name),
        Col("in_service_date", lambda o: o.in_service_date, "date"),
        Col("cost", lambda o: o.total_cost() if o.status != "draft" else o.cost, "money"),
        Col("accumulated_dep", lambda o: o.accumulated(), "money-muted"),
        Col("book_value", lambda o: (o.total_cost() - o.accumulated()) if o.status not in ("draft", "disposed")
            else ZERO, "money"),
        Col("status", lambda o: o.get_status_display(), "badge"),
    ]

    def get_queryset(self):
        self.queryset = FixedAsset.objects.select_related("category")
        self.filters = [("state", "status", AssetStatus.choices),
                        ("category", "category_id", [(c.pk, c.name) for c in AssetCategory.objects.all()])]
        return super().get_queryset()


class AssetFormMixin(CrudFormMixin):
    module, model, form_class = "assets", FixedAsset, AssetForm
    entity_key, list_url = "ent_asset", "asset_list"
    template_name = "ledger/form_with_parties.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "parties": party_datalist()}

    def form_valid(self, form):
        form.instance.life_months = form.cleaned_data["life_months"]
        if not form.instance.pk:
            form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("asset_detail", args=[self.object.pk])


class AssetCreate(AssetFormMixin, CreateView):
    def get_initial(self):
        return {"purchase_date": timezone.localdate(), "in_service_date": timezone.localdate(),
                "branch": self.request.branch}


class AssetUpdate(AssetFormMixin, UpdateView):
    def get_queryset(self):
        return FixedAsset.objects.filter(status=AssetStatus.DRAFT)


class AssetDetail(PermissionMixin, TemplateView):
    module = "assets"
    template_name = "ledger/asset.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        a = get_object_or_404(FixedAsset.objects.select_related("category", "treasury", "supplier", "branch"),
                              pk=kwargs["pk"])
        today = timezone.localdate()
        u = self.request.user
        horizon = asset_svc.month_end(date(today.year + 1, today.month, 1))
        preview = []
        if a.status == AssetStatus.ACTIVE:
            preview = asset_svc.schedule(a, horizon)[:13]
        ctx.update(title=str(a), a=a, events=a.events.select_related("entry", "created_by"),
                   cost=a.total_cost() if a.status != "draft" else a.cost, accumulated=a.accumulated(),
                   preview=preview, attachments=attachments_of(a), target="fixedasset",
                   improve_form=kwargs.get("improve_form") or ImproveForm(initial={"date": today}),
                   dispose_form=kwargs.get("dispose_form") or DisposeForm(initial={"date": today}),
                   activate_date=today, parties=party_datalist(),
                   can_edit=has_perm(u, "assets", "edit"), can_dispose=has_perm(u, "assets", "dispose"))
        ctx["book"] = ctx["cost"] - ctx["accumulated"]
        return ctx

    def post(self, request, pk):
        a = get_object_or_404(FixedAsset, pk=pk)
        do = request.POST.get("do")
        if do == "activate":
            need(request, "assets", "edit")
            day = reports._date(request.POST.get("date"), a.purchase_date)
            asset_svc.acquire(a, request.user, day=day)
            log(request, "post", a)
            messages.success(request, tr("asset_activated"))
        elif do == "improve":
            need(request, "assets", "edit")
            form = ImproveForm(request.POST)
            if not form.is_valid():
                return self.render_to_response(self.get_context_data(pk=pk, improve_form=form))
            d = form.cleaned_data
            asset_svc.improve(a, request.user, day=d["date"], amount=d["amount"],
                              extend_months=d.get("extend_months") or 0, funding=d["funding"],
                              treasury=d.get("treasury"), supplier=d.get("supplier"), description=d["description"])
            log(request, "update", a, [f"improvement {d['amount']}"])
            messages.success(request, tr("saved"))
        elif do == "dispose":
            need(request, "assets", "dispose")
            form = DisposeForm(request.POST)
            if not form.is_valid():
                return self.render_to_response(self.get_context_data(pk=pk, dispose_form=form))
            d = form.cleaned_data
            asset_svc.dispose(a, request.user, day=d["date"], proceeds=d["proceeds"] or ZERO,
                              treasury=d.get("treasury"), description=d.get("description", ""))
            log(request, "status", a, ["disposed"])
            messages.success(request, tr("asset_disposed"))
        elif do == "delete" and a.status == AssetStatus.DRAFT:
            need(request, "assets", "edit")
            log(request, "delete", a)
            a.delete()
            return redirect("asset_list")
        else:
            raise Http404
        return redirect("asset_detail", pk)


class Depreciation(PermissionMixin, TemplateView):
    module = "assets"
    template_name = "ledger/depreciation.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        last_end = date(today.year, today.month, 1) - timedelta(days=1)
        up_to = reports._date(self.request.GET.get("date"), last_end)
        preview = []
        for a in FixedAsset.objects.filter(status=AssetStatus.ACTIVE).select_related("category"):
            rows = asset_svc.schedule(a, asset_svc.month_end(up_to))
            if rows:
                preview.append({"a": a, "months": len(rows), "amount": sum((x for _, x in rows), ZERO)})
        latest = DepreciationRun.objects.filter(is_reversed=False).order_by("-date", "-id").first()
        ctx.update(title=tr("dep_runs"), runs=DepreciationRun.objects.select_related("entry", "created_by")[:30],
                   form=RunForm(initial={"date": up_to}), preview=preview, up_to=asset_svc.month_end(up_to),
                   preview_total=sum((p["amount"] for p in preview), ZERO), latest=latest,
                   can_run=has_perm(self.request.user, "assets", "depreciate"))
        return ctx

    def post(self, request):
        need(request, "assets", "depreciate")
        if request.POST.get("do") == "reverse":
            run = get_object_or_404(DepreciationRun, pk=request.POST.get("run"))
            asset_svc.reverse_run(run, request.user)
            log(request, "reverse", run)
            messages.success(request, tr("dep_reversed", run.number))
            return redirect("depreciation")
        form = RunForm(request.POST)
        if form.is_valid():
            run = asset_svc.run_depreciation(form.cleaned_data["date"], request.user)
            log(request, "post", run, [f"{run.total}"])
            messages.success(request, tr("dep_done", run.number, f"{run.total:,.2f}"))
        return redirect("depreciation")


class AssetCategoryList(CrudListView):
    module = "assets"
    model = AssetCategory
    title_key = "nav_asset_categories"
    create_url, edit_url = "assetcategory_create", "assetcategory_edit"
    columns = [Col("name", lambda o: o.name, "title"), Col("asset_account", lambda o: str(o.asset_account)),
               Col("life_months", lambda o: o.life_months), Col("dep_method", lambda o: o.get_method_display())]


class AssetCategoryForm_(CrudFormMixin):
    module, model, form_class = "assets", AssetCategory, AssetCategoryForm
    entity_key, list_url = "ent_asset_category", "assetcategory_list"


class AssetCategoryCreate(AssetCategoryForm_, CreateView):
    pass


class AssetCategoryUpdate(AssetCategoryForm_, UpdateView):
    pass


# ======================================================================= reports
class ReportIndex(PermissionMixin, TemplateView):
    module = "statements"
    template_name = "ledger/reports.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(title=tr("nav_statements"), reports=[
            {"key": k, "label": tr(v[1]), "icon": v[2], "help": tr(f"{v[1]}_help")} for k, v in reports.REPORTS.items()])
        return ctx


class ReportView(PermissionMixin, TemplateView):
    module = "statements"
    template_name = "ledger/report.html"

    def get(self, request, key):
        if key not in reports.REPORTS:
            raise Http404
        report, f = reports.build(key, request.GET)
        fmt = request.GET.get("format")
        if fmt == "xlsx":
            data = reports.to_xlsx(report, current_lang())
            resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            resp["Content-Disposition"] = f'attachment; filename="{key}_{f.date_from:%Y%m%d}_{f.date_to:%Y%m%d}.xlsx"'
            log(request, "export", None, [f"{key}.xlsx"])
            return resp
        ctx = self.get_context_data(
            report=report, f=f, key=key, uses=reports.REPORTS[key][3], title=report.title,
            branches=request.branches, projects=Project.objects.all(),
            accounts=Account.objects.order_by("code"), parties=party_datalist(),
            party_kinds=PartyKind.choices, levels=range(1, 6),
            party_code=f.party.code if f.party else "")
        if fmt == "print":
            self.template_name = "ledger/report_print.html"
        return self.render_to_response(ctx)


# ======================================================================= settings
class SettingsHub(PermissionMixin, TemplateView):
    module = "acc_settings"
    template_name = "ledger/settings.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        s = LedgerSettings.get()
        methods = PaymentMethod.objects.all()
        ctx.update(title=tr("nav_acc_settings"), form=kwargs.get("form") or SettingsForm(instance=s),
                   mform=kwargs.get("mform") or MethodAccountForm(methods=methods),
                   can_edit=has_perm(self.request.user, "acc_settings", "edit"),
                   counts={"currencies": Currency.objects.count(), "taxes": TaxRate.objects.count(),
                           "projects": Project.objects.count(), "expcats": ExpenseCategory.objects.count(),
                           "asset_cats": AssetCategory.objects.count()})
        return ctx

    def post(self, request):
        need(request, "acc_settings", "edit")
        s = LedgerSettings.get()
        if request.POST.get("do") == "methods":
            methods = PaymentMethod.objects.all()
            mform = MethodAccountForm(request.POST, methods=methods)
            if mform.is_valid():
                for m in methods:
                    acc = mform.cleaned_data.get(f"m{m.pk}")
                    if acc:
                        PaymentMethodAccount.objects.update_or_create(method=m, defaults={"account": acc})
                    else:
                        PaymentMethodAccount.objects.filter(method=m).delete()
                log(request, "update", s, ["payment method accounts"])
                messages.success(request, tr("saved"))
                return redirect("ledger_settings")
            return self.render_to_response(self.get_context_data(mform=mform))
        form = SettingsForm(request.POST, instance=s)
        if form.is_valid():
            form.save()
            log(request, "update", s, form.changed_data)
            messages.success(request, tr("saved"))
            return redirect("ledger_settings")
        messages.error(request, tr("fix_errors"))
        return self.render_to_response(self.get_context_data(form=form))


def simple_crud(model_, form_, module_, entity, list_name, cols, title, search=(), status=True, ordering=None):
    """List + create + edit views for a settings table."""

    class L(CrudListView):
        module = module_
        model = model_
        title_key = title
        create_url, edit_url = f"{list_name}_create", f"{list_name}_edit"
        columns = cols
        search_fields = list(search)
        has_status = status

        def get_queryset(self):
            qs = super().get_queryset()
            return qs.order_by(*ordering) if ordering else qs

    class F(CrudFormMixin):
        module = module_
        model = model_
        form_class = form_
        entity_key, list_url = entity, f"{list_name}_list"

        def get_initial(self):
            init = super().get_initial()
            if "date" in getattr(form_, "_meta").fields:
                init["date"] = timezone.localdate()
            return init

        def form_valid(self, form):
            if hasattr(form.instance, "created_by_id") and not form.instance.pk:
                form.instance.created_by = self.request.user
            return super().form_valid(form)

    class C(F, CreateView):
        pass

    class U(F, UpdateView):
        pass

    return L, C, U


ProjectList, ProjectCreate, ProjectUpdate = simple_crud(
    Project, ProjectForm, "acc_settings", "ent_project", "project",
    [Col("code", lambda o: o.code, "code"), Col("name", lambda o: o.name, "title"),
     Col("budget", lambda o: o.budget or ZERO, "money")], "nav_projects", search=["code", "name_ar"])
ExpCatList, ExpCatCreate, ExpCatUpdate = simple_crud(
    ExpenseCategory, ExpenseCategoryForm, "acc_settings", "ent_expcat", "expcat",
    [Col("code", lambda o: o.code, "code"), Col("name", lambda o: o.name, "title"),
     Col("expense_group", lambda o: o.get_group_display(), "badge"), Col("account", lambda o: str(o.account))],
    "nav_expcats", search=["code", "name_ar"])
TaxList, TaxCreate, TaxUpdate = simple_crud(
    TaxRate, TaxRateForm, "acc_settings", "ent_tax", "tax",
    [Col("name", lambda o: o.name, "title", sub=lambda o: o.legal_reference),
     Col("tax_rate_pct", lambda o: f"{o.rate.normalize()}%"), Col("tax_kind", lambda o: o.get_kind_display(), "badge"),
     Col("account", lambda o: str(o.account))], "nav_taxes")
CurrencyList, CurrencyCreate, CurrencyUpdate = simple_crud(
    Currency, CurrencyForm, "acc_settings", "ent_currency", "currency",
    [Col("code", lambda o: o.code, "code"), Col("name", lambda o: o.name, "title",
                                                sub=lambda o: tr("base_currency") if o.is_base else ""),
     Col("latest_rate", lambda o: (o.rates.first().rate if o.rates.exists() else "—") if not o.is_base else 1)],
    "nav_currencies")
RateList, RateCreate, RateUpdate = simple_crud(
    ExchangeRate, RateForm, "acc_settings", "ent_rate", "rate",
    [Col("date", lambda o: o.date, "date"), Col("currency", lambda o: o.currency.code, "code"),
     Col("rate", lambda o: o.rate)], "nav_rates", status=False)


class Periods(PermissionMixin, TemplateView):
    module = "periods"
    template_name = "ledger/periods.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        engine.get_year(timezone.localdate())
        years = []
        for y in FiscalYear.objects.prefetch_related("periods"):
            periods = []
            for p in y.periods.all():
                p.n_pending = closing.pending_documents(p).count()
                p.n_posted = JournalEntry.objects.filter(date__gte=p.start, date__lte=p.end,
                                                        status=EntryStatus.POSTED).count()
                periods.append(p)
            years.append({"y": y, "periods": periods})
        u = self.request.user
        ctx.update(title=tr("nav_periods"), years=years, today=timezone.localdate(),
                   can_close=has_perm(u, "periods", "edit"), can_reopen=has_perm(u, "periods", "reopen"),
                   can_reval=has_perm(u, "journal", "post"),
                   foreign=Account.objects.filter(currency__isnull=False, currency__is_base=False).exists())
        return ctx

    def post(self, request):
        do = request.POST.get("do")
        u = request.user
        if do in ("close", "reopen"):
            p = closing.period_of(request.POST.get("period"))
            if do == "close":
                need(request, "periods", "edit")
                closing.close_period(p, u, force=request.POST.get("force") == "1")
            else:
                need(request, "periods", "reopen")
                closing.reopen_period(p, u)
            log(request, do, p)
            messages.success(request, tr("period_closed_ok" if do == "close" else "period_reopened_ok", str(p)))
        elif do in ("close_year", "reopen_year"):
            y = get_object_or_404(FiscalYear, pk=request.POST.get("year"))
            if do == "close_year":
                need(request, "periods", "edit")
                closing.close_year(y, u)
            else:
                need(request, "periods", "reopen")
                closing.reopen_year(y, u)
            log(request, "close" if do == "close_year" else "reopen", y)
            messages.success(request, tr(f"{do}_ok", y.name))
        elif do == "revalue":
            need(request, "journal", "post")
            day = reports._date(request.POST.get("date"), timezone.localdate())
            e = closing.revalue_currencies(day, u)
            log(request, "post", e, ["fx revaluation"])
            messages.success(request, tr("entry_posted", e.number))
            return redirect("journal_detail", e.pk)
        else:
            raise Http404
        return redirect("period_list")


# ---------------------------------------------------------------- permissions
FIN_MODULES = ["acc_home", "coa", "journal", "vouchers", "bills", "treasury", "custody", "parties", "assets",
               "statements", "periods", "acc_settings", "backup", "invoices", "expenses", "cashbox",
               "doctor_accounts", "payouts", "finance_reports"]


def finance_perms():
    out = []
    for m in FIN_MODULES:
        for action in PERMISSIONS.get(m, {}):
            out.append((m, action))
    return out


class FinancePermissions(PermissionMixin, TemplateView):
    module, action = "users", "edit"
    template_name = "ledger/permissions.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        users = list(User.objects.filter(is_active=True, is_superuser=False).exclude(
            role__in=[Role.DOCTOR, Role.NURSE, Role.STOREKEEPER]).order_by("role", "first_name"))
        extra = self.request.GET.get("user")
        if extra:
            u = User.objects.filter(pk=extra, is_superuser=False).first()
            if u and u not in users:
                users.append(u)
        over = {(o.user_id, o.perm): o.allow for o in PermissionOverride.objects.all()}
        perms = finance_perms()
        rows = []
        for m, a in perms:
            cells = []
            for u in users:
                key = f"{m}.{a}"
                default = role_allows(u.role, m, a)
                o = over.get((u.pk, key))
                cells.append({"user": u, "name": f"p_{u.pk}_{key}", "default": default,
                              "value": "" if o is None else ("1" if o else "0"),
                              "effective": default if o is None else o})
            rows.append({"module": m, "action": a, "cells": cells})
        ctx.update(title=tr("nav_fin_permissions"), users=users, rows=rows,
                   all_users=User.objects.filter(is_active=True, is_superuser=False))
        return ctx

    def post(self, request):
        perms = {f"{m}.{a}" for m, a in finance_perms()}
        changes = []
        with transaction.atomic():
            for key, value in request.POST.items():
                if not key.startswith("p_"):
                    continue
                _, uid, perm = key.split("_", 2)
                if perm not in perms or not uid.isdigit():
                    continue
                user = User.objects.filter(pk=uid, is_superuser=False).first()
                if user is None:
                    continue
                old = PermissionOverride.objects.filter(user=user, perm=perm).first()
                if value == "":
                    if old:
                        old.delete()
                        changes.append(f"{user.username}:{perm}=default")
                elif value in ("0", "1"):
                    allow = value == "1"
                    if old is None or old.allow != allow:
                        PermissionOverride.objects.update_or_create(user=user, perm=perm, defaults={"allow": allow})
                        changes.append(f"{user.username}:{perm}={'allow' if allow else 'deny'}")
        if changes:
            log(request, "permissions", None, changes)
        messages.success(request, tr("saved"))
        return redirect(request.get_full_path())



# ---------------------------------------------------------------- backup
BACKUP_APPS = ["core", "accounts", "clinic", "finance", "records", "ledger", "auth.group"]


class Backup(PermissionMixin, TemplateView):
    module = "backup"
    template_name = "ledger/backup.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from core.models import AuditLog

        ctx.update(title=tr("nav_backup"), history=AuditLog.objects.filter(action__in=["backup", "restore"])[:10],
                   can_restore=has_perm(self.request.user, "backup", "edit"))
        return ctx

    def post(self, request):
        do = request.POST.get("do")
        if do == "download":
            from io import StringIO

            out = StringIO()
            call_command("dumpdata", *BACKUP_APPS, "--natural-foreign", "--indent", "0", stdout=out,
                         exclude=["auth.permission", "contenttypes"])
            log(request, "backup", None, [f"{len(out.getvalue()) // 1024} KB"])
            stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
            resp = HttpResponse(out.getvalue(), content_type="application/json")
            resp["Content-Disposition"] = f'attachment; filename="clinic-backup-{stamp}.json"'
            return resp
        if do == "restore":
            need(request, "backup", "edit")
            if request.POST.get("confirm", "").strip().upper() != "RESTORE":
                messages.error(request, tr("restore_confirm_needed"))
                return redirect("backup")
            f = request.FILES.get("file")
            if not f:
                messages.error(request, tr("required"))
                return redirect("backup")
            import os
            import tempfile

            raw = f.read()
            try:
                data = json.loads(raw.decode("utf-8"))
                assert isinstance(data, list) and all("model" in x for x in data[:5])
            except Exception:
                messages.error(request, tr("restore_bad_file"))
                return redirect("backup")
            labels = {x["model"] for x in data}
            if not any(lbl.startswith("ledger.") for lbl in labels):
                messages.error(request, tr("restore_bad_file"))
                return redirect("backup")
            fd, path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
                me = request.user.username
                with engine.purging():
                    call_command("flush", "--no-input")
                    call_command("loaddata", path)
            finally:
                os.remove(path)
            from core.audit import log as audit

            audit(None, "restore", None, [f.name], username=me)
            messages.success(request, tr("restore_done"))
            return redirect("login")
        raise Http404

