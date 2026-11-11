"""Fixed assets: acquisition, improvements, monthly depreciation and disposal.

Depreciation is monthly. It starts in the month the asset goes into service (for assets brought
in with an opening balance: the month they were registered, the earlier depreciation being the
opening accumulated amount). Every month:

    straight line:  (book value − salvage) ÷ months of life left
    declining:      book value × annual rate ÷ 12   (never below salvage; the last month takes the rest)

Spreading what is left over the months left means an improvement (added cost, optional added
life) is depreciated automatically from the following month on.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction

from core.i18n import tr

from .engine import ZERO, cr, create_entry, dr, ensure_open, q, reverse
from .models import (
    AssetEvent, AssetStatus, DepMethod, DepreciationRun, FixedAsset, FundingKind, LedgerError, LedgerSettings,
)


def month_end(d):
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def months_between(a, b):
    """Whole months from the month of a to the month of b."""
    return (b.year - a.year) * 12 + b.month - a.month


def _acquisition(asset):
    return asset.events.filter(kind="acquisition").first()


def first_month(asset):
    acq = _acquisition(asset)
    if asset.funding == FundingKind.OPENING and acq:
        return date(acq.date.year, acq.date.month, 1)
    return date(asset.in_service_date.year, asset.in_service_date.month, 1)


def funding_line(asset, amount, treasury=None, supplier=None, funding=None):
    funding = funding or asset.funding
    s = LedgerSettings.get()
    if funding == FundingKind.TREASURY:
        treasury = treasury or asset.treasury
        if treasury is None:
            raise LedgerError(tr("treasury_required"))
        return cr(treasury.account, amount, description=asset.code)
    if funding == FundingKind.SUPPLIER:
        supplier = supplier or asset.supplier
        if supplier is None:
            raise LedgerError(tr("supplier_required"))
        return cr(supplier.default_account(), amount, party=supplier, description=asset.code)
    return cr(s.acct("opening_equity"), amount, description=asset.code)


@transaction.atomic
def acquire(asset, user, day=None):
    """Draft → active: book the cost (and any opening accumulated depreciation)."""
    if asset.status != AssetStatus.DRAFT:
        raise LedgerError(tr("wrong_status"))
    if asset.salvage_value >= asset.cost:
        raise LedgerError(tr("salvage_too_high"))
    if asset.opening_accumulated and asset.funding != FundingKind.OPENING:
        raise LedgerError(tr("opening_acc_needs_opening"))
    day = day or asset.purchase_date
    cat = asset.category
    lines = [dr(cat.asset_account, asset.cost, description=asset.name, project=asset.project)]
    net = asset.cost - asset.opening_accumulated
    if asset.opening_accumulated:
        lines.append(cr(cat.accumulated_account, asset.opening_accumulated, description=asset.name))
    lines.append(funding_line(asset, net))
    entry = create_entry(date=day, description=f"{tr('ae_acquisition')} {asset}", lines=lines, user=user,
                         source=asset, source_label=asset.code, branch=asset.branch)
    AssetEvent.objects.create(asset=asset, kind="acquisition", date=day, amount=asset.cost, entry=entry,
                              description=asset.name, created_by=user)
    asset.status = AssetStatus.ACTIVE
    if asset.funding == FundingKind.OPENING:
        asset.depreciated_to = date(day.year, day.month, 1) - timedelta(days=1)
    asset.save(update_fields=["status", "depreciated_to", "updated_at"])
    return entry


@transaction.atomic
def improve(asset, user, *, day, amount, extend_months=0, funding=FundingKind.TREASURY, treasury=None,
            supplier=None, description=""):
    if asset.status not in (AssetStatus.ACTIVE, AssetStatus.DEPRECIATED):
        raise LedgerError(tr("wrong_status"))
    lines = [dr(asset.category.asset_account, amount, description=description or asset.name,
                project=asset.project),
             funding_line(asset, amount, treasury=treasury, supplier=supplier, funding=funding)]
    entry = create_entry(date=day, description=f"{tr('ae_improvement')} {asset}: {description}", lines=lines,
                         user=user, source=asset, source_label=asset.code, branch=asset.branch)
    AssetEvent.objects.create(asset=asset, kind="improvement", date=day, amount=amount, months=extend_months,
                              entry=entry, description=description, created_by=user)
    asset.cost = asset.total_cost()
    asset.life_months += extend_months
    asset.status = AssetStatus.ACTIVE
    asset.save(update_fields=["cost", "life_months", "status", "updated_at"])
    return entry


def schedule(asset, up_to):
    """[(month_end, amount)] still to depreciate up to `up_to` (not saved)."""
    if asset.status != AssetStatus.ACTIVE:
        return []
    start = first_month(asset)
    if asset.depreciated_to:
        nxt = asset.depreciated_to + timedelta(days=1)
        start = max(start, date(nxt.year, nxt.month, 1))
    out = []
    events = list(asset.events.exclude(kind="depreciation").order_by("date"))
    accumulated = asset.accumulated()
    life = asset.life_months
    m = start
    while month_end(m) <= up_to:
        end = month_end(m)
        cost = sum((e.amount for e in events if e.kind in ("acquisition", "improvement") and e.date <= end), ZERO)
        book = cost - accumulated
        left = book - asset.salvage_value
        if left <= 0:
            break
        elapsed = months_between(date(asset.in_service_date.year, asset.in_service_date.month, 1), m)
        remaining = max(life - elapsed, 1)
        if asset.method == DepMethod.DECLINING:
            rate = asset.declining_rate or q(Decimal(200) / (Decimal(life) / 12))  # double declining
            amount = q(book * rate / 100 / 12)
            if remaining <= 1:
                amount = left
        else:
            amount = q(left / remaining)
        amount = min(amount, left)
        if amount <= 0:
            break
        out.append((end, amount))
        accumulated += amount
        m = end + timedelta(days=1)
    return out


@transaction.atomic
def run_depreciation(up_to, user):
    """Depreciate every active asset up to the month-end `up_to` in one journal entry."""
    up_to = month_end(up_to)
    ensure_open(up_to, user)
    plan = []
    for asset in FixedAsset.objects.filter(status=AssetStatus.ACTIVE).select_related("category"):
        rows = schedule(asset, up_to)
        if rows:
            plan.append((asset, rows))
    if not plan:
        raise LedgerError(tr("nothing_to_depreciate"))
    lines, total = [], ZERO
    for asset, rows in plan:
        amount = sum((a for _, a in rows), ZERO)
        total += amount
        label = f"{asset.code} · {rows[0][0]:%Y-%m}" + (f" → {rows[-1][0]:%Y-%m}" if len(rows) > 1 else "")
        lines.append(dr(asset.category.expense_account, amount, description=label, project=asset.project,
                        branch=asset.branch))
        lines.append(cr(asset.category.accumulated_account, amount, description=label, branch=asset.branch))
    run = DepreciationRun.objects.create(date=up_to, total=total, created_by=user)
    entry = create_entry(date=up_to, description=f"{tr('dep_run')} {run.number} — {up_to:%Y-%m}", lines=lines,
                         user=user, source=run, source_label=run.number)
    run.entry = entry
    run.save(update_fields=["entry"])
    for asset, rows in plan:
        for end, amount in rows:
            AssetEvent.objects.create(asset=asset, kind="depreciation", date=end, amount=amount, months=1,
                                      entry=entry, run=run, description=f"{end:%Y-%m}", created_by=user)
        asset.depreciated_to = rows[-1][0]
        if asset.book_value() <= asset.salvage_value:
            asset.status = AssetStatus.DEPRECIATED
        asset.save(update_fields=["depreciated_to", "status", "updated_at"])
    return run


@transaction.atomic
def reverse_run(run, user):
    latest = DepreciationRun.objects.filter(is_reversed=False).order_by("-date", "-id").first()
    if run.is_reversed or latest is None or latest.pk != run.pk:
        raise LedgerError(tr("only_latest_run"))
    assets = {e.asset for e in run.events.select_related("asset")}
    if any(a.status == AssetStatus.DISPOSED for a in assets):
        raise LedgerError(tr("run_has_disposed"))
    reverse(run.entry, user, note=run.number)
    run.events.all().delete()
    run.is_reversed = True
    run.save(update_fields=["is_reversed", "updated_at"])
    for a in assets:
        last = a.events.filter(kind="depreciation").order_by("-date").first()
        if last:
            a.depreciated_to = last.date
        else:
            acq = _acquisition(a)
            a.depreciated_to = (date(acq.date.year, acq.date.month, 1) - timedelta(days=1)
                                if a.funding == FundingKind.OPENING and acq else None)
        a.status = AssetStatus.ACTIVE
        a.save(update_fields=["depreciated_to", "status", "updated_at"])


@transaction.atomic
def dispose(asset, user, *, day, proceeds=ZERO, treasury=None, description=""):
    """Sell or scrap an asset: remove its cost and accumulated depreciation, book the gain or loss."""
    if asset.status not in (AssetStatus.ACTIVE, AssetStatus.DEPRECIATED):
        raise LedgerError(tr("wrong_status"))
    prev_end = date(day.year, day.month, 1) - timedelta(days=1)
    if schedule(asset, prev_end):
        raise LedgerError(tr("depreciate_first", f"{prev_end:%Y-%m}"))
    if proceeds and treasury is None:
        raise LedgerError(tr("treasury_required"))
    s = LedgerSettings.get()
    cat = asset.category
    cost, accumulated = asset.total_cost(), asset.accumulated()
    book = cost - accumulated
    lines = [dr(cat.accumulated_account, accumulated, description=asset.code)]
    if proceeds:
        lines.append(dr(treasury.account, proceeds, description=asset.code))
    lines.append(cr(cat.asset_account, cost, description=asset.code))
    result = proceeds - book
    if result > 0:
        lines.append(cr(s.acct("asset_gain"), result, description=asset.code))
    elif result < 0:
        lines.append(dr(s.acct("asset_loss"), -result, description=asset.code))
    entry = create_entry(date=day, description=f"{tr('ae_disposal')} {asset}" + (f": {description}" if description else ""),
                         lines=lines, user=user, source=asset, source_label=asset.code, branch=asset.branch)
    AssetEvent.objects.create(asset=asset, kind="disposal", date=day, amount=book, proceeds=proceeds,
                              entry=entry, description=description, created_by=user)
    asset.status, asset.disposed_on = AssetStatus.DISPOSED, day
    asset.save(update_fields=["status", "disposed_on", "updated_at"])
    return entry
