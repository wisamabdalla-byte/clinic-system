"""The posting engine: the only code that creates, posts and reverses journal entries.

Rules enforced here:
* every entry balances (total debit == total credit, to the cent) and has at least two lines;
* each line is one-sided and positive, on an active postable (non-group) account;
* control accounts (receivables/payables) need a party on the line;
* nothing is posted, reversed or changed inside a closed period unless the user
  has the special "periods.override" permission;
* posted entries are never edited or deleted: they are reversed by a mirror entry.
"""
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as date_cls, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from accounts.roles import has_perm
from core.i18n import tr

from .models import (
    ZERO, Account, EntryKind, EntryStatus, FiscalPeriod, FiscalYear, JournalEntry, JournalLine, LedgerError,
    LedgerSettings, Sequence,
)

CENT = Decimal("0.01")
_state = threading.local()


def q(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- numbering
def next_number(prefix, day=None, yearly=True, width=5):
    """Gap-free running numbers: JV-2026-00001, RV-2026-00002, FA-00001 ..."""
    year = (day or timezone.localdate()).year
    key = f"{prefix}-{year}" if yearly else prefix
    with transaction.atomic():
        seq, _ = Sequence.objects.select_for_update().get_or_create(key=key)
        seq.last += 1
        seq.save(update_fields=["last"])
    return f"{key}-{seq.last:0{width}d}"


# ---------------------------------------------------------------- periods
def _add_months(d, n):
    m = d.month - 1 + n
    return date_cls(d.year + m // 12, m % 12 + 1, 1)


def year_bounds(day):
    start_month = LedgerSettings.get().year_start_month
    start_year = day.year if day.month >= start_month else day.year - 1
    start = date_cls(start_year, start_month, 1)
    return start, _add_months(start, 12) - timedelta(days=1)


def get_year(day, create=True):
    year = FiscalYear.objects.filter(start__lte=day, end__gte=day).first()
    if year or not create:
        return year
    start, end = year_bounds(day)
    name = str(start.year) if start.month == 1 else f"{start.year}/{end.year}"
    with transaction.atomic():
        year = FiscalYear.objects.create(name=name, start=start, end=end)
        for i in range(12):
            p_start = _add_months(start, i)
            FiscalPeriod.objects.create(year=year, number=i + 1, start=p_start,
                                        end=_add_months(start, i + 1) - timedelta(days=1))
    return year


def get_period(day):
    period = FiscalPeriod.objects.filter(start__lte=day, end__gte=day).select_related("year").first()
    if period is None:
        get_year(day)
        period = FiscalPeriod.objects.select_related("year").get(start__lte=day, end__gte=day)
    return period


def can_override(user):
    return user is not None and has_perm(user, "periods", "override")


def ensure_open(day, user=None):
    period = get_period(day)
    if (period.is_closed or period.year.is_closed) and not can_override(user):
        raise LedgerError(tr("period_closed", f"{day:%Y-%m}"))
    return period


def current_user():
    return getattr(_state, "user", None)


@contextmanager
def acting_user(user):
    """Remember who is working, so automatic entries made by signals carry their name."""
    previous = getattr(_state, "user", None)
    _state.user = user
    try:
        yield
    finally:
        _state.user = previous


@contextmanager
def purging():
    """Allows deleting automatic entries outright (used only when erasing example data)."""
    previous = getattr(_state, "purge", False)
    _state.purge = True
    try:
        yield
    finally:
        _state.purge = previous


def purge_allowed():
    return getattr(_state, "purge", False)


# ---------------------------------------------------------------- lines
@dataclass
class Line:
    account: Account
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    description: str = ""
    party: object = None
    project: object = None
    branch: object = None
    currency: object = None
    fx_amount: Decimal = None
    fx_rate: Decimal = None
    extra: dict = field(default_factory=dict)

    def key(self):
        return (self.account.pk, q(self.debit), q(self.credit), getattr(self.party, "pk", None),
                getattr(self.project, "pk", None), getattr(self.branch, "pk", None))


def dr(account, amount, **kw):
    return Line(account=account, debit=q(amount), **kw)


def cr(account, amount, **kw):
    return Line(account=account, credit=q(amount), **kw)


def clean_lines(lines):
    """Round, turn negatives to the other side, drop zero lines."""
    out = []
    for ln in lines:
        d, c = q(ln.debit), q(ln.credit)
        net = d - c
        if net == 0:
            continue
        ln.debit, ln.credit = (net, ZERO) if net > 0 else (ZERO, -net)
        out.append(ln)
    return out


def validate_lines(lines, manual=False):
    if len(lines) < 2:
        raise LedgerError(tr("entry_min_lines"))
    total_d = sum((ln.debit for ln in lines), ZERO)
    total_c = sum((ln.credit for ln in lines), ZERO)
    if total_d != total_c:
        raise LedgerError(tr("entry_unbalanced", f"{total_d:,.2f}", f"{total_c:,.2f}"))
    for ln in lines:
        acc = ln.account
        if acc.is_group:
            raise LedgerError(tr("acc_is_group", str(acc)))
        if not acc.is_active:
            raise LedgerError(tr("acc_inactive", str(acc)))
        if manual and not acc.allow_manual:
            raise LedgerError(tr("acc_no_manual", str(acc)))
        if acc.is_control and ln.party is None:
            raise LedgerError(tr("acc_needs_party", str(acc)))
        if ln.debit < 0 or ln.credit < 0 or (ln.debit and ln.credit):
            raise LedgerError(tr("line_one_side"))
    return total_d


# ---------------------------------------------------------------- entries
def create_entry(*, date, description, lines, user=None, kind=EntryKind.AUTO, status=EntryStatus.POSTED,
                 source=None, source_label="", branch=None, reference="", auto_reverse_on=None, reversal_of=None):
    """Write an entry with its lines. Auto entries are posted at once; manual ones start as drafts."""
    user = user or current_user()
    lines = clean_lines(list(lines))
    total = validate_lines(lines, manual=kind in (EntryKind.MANUAL, EntryKind.ADJUSTMENT, EntryKind.OPENING))
    period = ensure_open(date, user) if status == EntryStatus.POSTED else get_period(date)
    now = timezone.now()
    entry = JournalEntry(
        number=next_number("JV", date), date=date, period=period, kind=kind, status=status,
        description=(description or "")[:255], reference=(reference or "")[:100], branch=branch,
        source_model=source._meta.label_lower if source is not None else "",
        source_id=str(source.pk) if source is not None else "", source_label=(source_label or "")[:60],
        reversal_of=reversal_of, auto_reverse_on=auto_reverse_on, total=total, created_by=user,
    )
    if status == EntryStatus.POSTED:
        entry.posted_by, entry.posted_at = user, now
    entry.save()
    write_lines(entry, lines)
    return entry


def write_lines(entry, lines):
    entry.lines.all().delete()
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=ln.account, description=(ln.description or "")[:255], debit=ln.debit,
                    credit=ln.credit, party=ln.party, project=ln.project, branch=ln.branch or entry.branch,
                    currency=ln.currency, fx_amount=ln.fx_amount, fx_rate=ln.fx_rate)
        for ln in lines
    ])


def lines_of(entry):
    return [Line(account=ln.account, debit=ln.debit, credit=ln.credit, description=ln.description,
                 party=ln.party, project=ln.project, branch=ln.branch, currency=ln.currency,
                 fx_amount=ln.fx_amount, fx_rate=ln.fx_rate)
            for ln in entry.lines.select_related("account", "party", "project", "branch", "currency")]


# ---------------------------------------------------------------- workflow for manual entries
def _check_editable(entry):
    if entry.status == EntryStatus.POSTED:
        raise LedgerError(tr("posted_cannot_change"))


def submit_review(entry, user):
    _check_editable(entry)
    if entry.status != EntryStatus.DRAFT:
        raise LedgerError(tr("wrong_status"))
    validate_lines(lines_of(entry), manual=True)
    entry.status, entry.reviewed_by, entry.reviewed_at, entry.return_note = (
        EntryStatus.REVIEWED, user, timezone.now(), "")
    entry.save(update_fields=["status", "reviewed_by", "reviewed_at", "return_note", "updated_at"])


def approve(entry, user):
    _check_editable(entry)
    s = LedgerSettings.get()
    if s.require_review and entry.status != EntryStatus.REVIEWED:
        raise LedgerError(tr("needs_review_first"))
    if entry.status not in (EntryStatus.DRAFT, EntryStatus.REVIEWED):
        raise LedgerError(tr("wrong_status"))
    if not s.allow_self_approval and entry.created_by_id == user.pk and not user.is_superuser:
        raise LedgerError(tr("no_self_approval"))
    validate_lines(lines_of(entry), manual=True)
    entry.status, entry.approved_by, entry.approved_at, entry.return_note = (
        EntryStatus.APPROVED, user, timezone.now(), "")
    entry.save(update_fields=["status", "approved_by", "approved_at", "return_note", "updated_at"])


def return_to_draft(entry, user, note=""):
    _check_editable(entry)
    entry.status, entry.return_note = EntryStatus.DRAFT, (note or "")[:255]
    entry.reviewed_by = entry.approved_by = None
    entry.reviewed_at = entry.approved_at = None
    entry.save()


def post(entry, user):
    """Post a manual entry once it went through the review/approval steps the settings require."""
    _check_editable(entry)
    s = LedgerSettings.get()
    if s.require_approval and entry.status != EntryStatus.APPROVED:
        raise LedgerError(tr("needs_approval_first"))
    if s.require_review and not s.require_approval and entry.status != EntryStatus.REVIEWED:
        raise LedgerError(tr("needs_review_first"))
    lines = lines_of(entry)
    entry.total = validate_lines(lines, manual=True)
    entry.period = ensure_open(entry.date, user)
    entry.status, entry.posted_by, entry.posted_at = EntryStatus.POSTED, user, timezone.now()
    entry.save()
    if entry.auto_reverse_on:
        reverse(entry, user, day=entry.auto_reverse_on, note=tr("auto_reversal"))
    return entry


def reverse(entry, user=None, day=None, note=""):
    """Post the mirror image of a posted entry and mark the original as reversed."""
    user = user or current_user()
    if entry.status != EntryStatus.POSTED:
        raise LedgerError(tr("only_posted_reverse"))
    if entry.is_reversed or entry.kind == EntryKind.REVERSAL:
        raise LedgerError(tr("already_reversed"))
    day = day or entry.date
    swapped = []
    for ln in lines_of(entry):
        ln.debit, ln.credit = ln.credit, ln.debit
        swapped.append(ln)
    rev = create_entry(
        date=day, description=f"{tr('reversal_of')} {entry.number}" + (f" — {note}" if note else ""),
        lines=swapped, user=user, kind=EntryKind.REVERSAL, source=None, branch=entry.branch,
        reference=entry.number, reversal_of=entry,
    )
    rev.source_model, rev.source_id, rev.source_label = entry.source_model, entry.source_id, entry.source_label
    rev.save(update_fields=["source_model", "source_id", "source_label"])
    entry.is_reversed = True
    entry.save(update_fields=["is_reversed", "updated_at"])
    return rev


def copy_as_draft(entry, user):
    """'Reverse & correct': a new editable draft with the same lines."""
    new = create_entry(date=timezone.localdate(), description=entry.description, lines=lines_of(entry),
                       user=user, kind=EntryKind.MANUAL, status=EntryStatus.DRAFT, branch=entry.branch,
                       reference=entry.number)
    return new


# ---------------------------------------------------------------- documents → entries
def active_entry(obj):
    return (JournalEntry.objects.filter(source_model=obj._meta.label_lower, source_id=str(obj.pk),
                                        status=EntryStatus.POSTED, is_reversed=False)
            .exclude(kind=EntryKind.REVERSAL).order_by("-id").first())


def sync_source(obj, *, date, description, lines, branch=None, reference="", label="", user=None):
    """Make the ledger match a document: post its entry, or reverse the old one and post a new one
    when the document changed. Passing no lines (cancelled/deleted document) only reverses."""
    user = user or current_user()
    lines = clean_lines(list(lines))
    for ln in lines:
        ln.branch = ln.branch or branch
    current = active_entry(obj)
    if current is not None:
        same = (current.date == date and current.branch_id == getattr(branch, "pk", None)
                and sorted(ln.key() for ln in lines_of(current)) == sorted(ln.key() for ln in lines))
        if same:
            if current.description != description[:255]:
                current.description = description[:255]
                current.save(update_fields=["description", "updated_at"])
            return current
        ensure_open(current.date, user)
        reverse(current, user, note=tr("document_changed"))
    if not lines:
        return None
    return create_entry(date=date, description=description, lines=lines, user=user, source=obj,
                        source_label=label or str(obj), branch=branch, reference=reference)


def unsync_source(model_label, pk, user=None):
    """The document was deleted: reverse (or, while erasing examples, delete) its entries."""
    entries = JournalEntry.objects.filter(source_model=model_label, source_id=str(pk))
    if purge_allowed():
        for e in entries.order_by("-id"):
            JournalEntry.objects.filter(pk=e.pk).update(reversal_of=None)
        for e in entries.order_by("-id"):
            e.delete()
        return
    for e in entries.filter(status=EntryStatus.POSTED, is_reversed=False).exclude(kind=EntryKind.REVERSAL):
        ensure_open(e.date, user or current_user())
        reverse(e, user, note=tr("document_deleted"))
