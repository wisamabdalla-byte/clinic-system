"""Closing months and years, and revaluing foreign-currency accounts."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.i18n import tr

from .balances import lines as line_qs
from .engine import ZERO, cr, create_entry, dr, q, reverse
from .models import (
    Account, EntryKind, EntryStatus, FiscalPeriod, JournalEntry, LedgerError, LedgerSettings,
)


def pending_documents(period):
    """Entries in the period still waiting for review/approval/posting."""
    return JournalEntry.objects.filter(date__gte=period.start, date__lte=period.end).exclude(
        status=EntryStatus.POSTED)


@transaction.atomic
def close_period(period, user, force=False):
    if period.is_closed:
        raise LedgerError(tr("wrong_status"))
    if not force and pending_documents(period).exists():
        raise LedgerError(tr("period_has_drafts", pending_documents(period).count()))
    period.is_closed, period.closed_by, period.closed_at = True, user, timezone.now()
    period.save()


@transaction.atomic
def reopen_period(period, user):
    if period.year.is_closed:
        raise LedgerError(tr("year_closed_reopen_first"))
    period.is_closed, period.closed_by, period.closed_at = False, None, None
    period.save()


def pnl_balances(start, end):
    qs = (line_qs(date_from=start, date_to=end).filter(account__type__in=["revenue", "expense"])
          .exclude(entry__kind=EntryKind.CLOSING)
          .values("account").annotate(d=Sum("debit"), c=Sum("credit")))
    return {r["account"]: (r["d"] or ZERO) - (r["c"] or ZERO) for r in qs}


@transaction.atomic
def close_year(year, user):
    """Move the year's revenue and expense balances to retained earnings and lock the year."""
    if year.is_closed:
        raise LedgerError(tr("wrong_status"))
    pending = JournalEntry.objects.filter(date__gte=year.start, date__lte=year.end).exclude(
        status=EntryStatus.POSTED)
    if pending.exists():
        raise LedgerError(tr("period_has_drafts", pending.count()))
    balances = pnl_balances(year.start, year.end)
    accounts = Account.objects.in_bulk(balances.keys())
    lines, net = [], ZERO  # net = debit − credit of all P&L lines (negative = profit)
    for acc_id, bal in sorted(balances.items(), key=lambda kv: accounts[kv[0]].code):
        if bal == 0:
            continue
        net += bal
        lines.append(cr(accounts[acc_id], bal) if bal > 0 else dr(accounts[acc_id], -bal))
    entry = None
    if lines:
        re_acc = LedgerSettings.get().acct("retained_earnings")
        lines.append(dr(re_acc, net) if net > 0 else cr(re_acc, -net))
        last = year.periods.get(end=year.end)
        was_closed = last.is_closed
        last.is_closed = False
        last.save()
        entry = create_entry(date=year.end, description=f"{tr('closing_entry')} {year.name}", lines=lines,
                             user=user, kind=EntryKind.CLOSING, source=year, source_label=year.name)
        last.is_closed = was_closed
        last.save()
    now = timezone.now()
    year.periods.filter(is_closed=False).update(is_closed=True, closed_by=user, closed_at=now)
    year.is_closed, year.closing_entry, year.closed_by, year.closed_at = True, entry, user, now
    year.save()
    return entry


@transaction.atomic
def reopen_year(year, user):
    if not year.is_closed:
        raise LedgerError(tr("wrong_status"))
    year.is_closed = False
    year.save()
    last = year.periods.get(end=year.end)
    was_closed = last.is_closed
    last.is_closed = False
    last.save()
    if year.closing_entry_id:
        reverse(year.closing_entry, user, note=tr("year_reopened"))
    last.is_closed = was_closed
    last.save()
    year.closing_entry = None
    year.save()


@transaction.atomic
def revalue_currencies(day, user):
    """Bring every foreign-currency account to the closing rate of `day`; post the difference
    as unrealised exchange gain/loss."""
    s = LedgerSettings.get()
    lines = []
    for account in Account.objects.filter(currency__isnull=False, currency__is_base=False, is_group=False):
        qs = line_qs(date_to=day).filter(account=account)
        if not qs.exists():
            continue
        fx_total = Decimal(0)
        for d, c, fx in qs.values_list("debit", "credit", "fx_amount"):
            if fx:
                fx_total += fx if d else -fx
        agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
        book = (agg["d"] or ZERO) - (agg["c"] or ZERO)
        target = q(fx_total * account.currency.rate_on(day))
        diff = target - book
        if diff:
            kw = {"currency": account.currency, "fx_amount": Decimal(0), "fx_rate": account.currency.rate_on(day),
                  "description": tr("fx_revaluation")}
            lines.append(dr(account, diff, **kw) if diff > 0 else cr(account, -diff, **kw))
            lines.append(cr(s.acct("fx_gain"), diff) if diff > 0 else dr(s.acct("fx_loss"), -diff))
    if not lines:
        raise LedgerError(tr("nothing_to_revalue"))
    return create_entry(date=day, description=f"{tr('fx_revaluation')} {day:%Y-%m-%d}", lines=lines, user=user,
                        kind=EntryKind.ADJUSTMENT)


def period_of(pk):
    return FiscalPeriod.objects.select_related("year").get(pk=pk)
