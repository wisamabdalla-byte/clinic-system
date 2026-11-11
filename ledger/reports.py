"""Financial statements and reports, all built from journal lines.

Every report returns a `Report` (columns + rows), which is rendered as a web page, a print/PDF
page or an Excel file by the same code. Only posted entries count, unless the user ticks
"include unposted entries", in which case the report carries a visible warning.
"""
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from core.i18n import tr
from core.models import Branch

from .balances import Tree, lines as line_qs, natural, totals_by_account
from .engine import ZERO, year_bounds
from .models import (
    Account, AssetStatus, EntryKind, EntryStatus, FixedAsset, JournalEntry, Party, PartyKind, Project, Treasury,
)


# ---------------------------------------------------------------- structures
@dataclass
class Col:
    key: str
    label: str
    kind: str = "text"  # text | money | date | code | pct


@dataclass
class Row:
    cells: dict
    style: str = ""  # "" | group | sub | total | grand | muted | warn
    level: int = 0
    url: str = ""


@dataclass
class Report:
    key: str
    title: str
    columns: list
    rows: list = field(default_factory=list)
    subtitle: str = ""
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    summary: list = field(default_factory=list)  # [(label, amount)]

    @property
    def link_key(self):
        """Rows with a url link from their first text column."""
        return next((c.key for c in self.columns if c.kind == "text"), "")


@dataclass
class Filters:
    date_from: date
    date_to: date
    compare: str = ""
    c_from: date = None
    c_to: date = None
    branch: object = None
    project: object = None
    account: object = None
    party: object = None
    party_kind: str = ""
    level: int = 0
    include_unposted: bool = False
    hide_zero: bool = True

    @property
    def kw(self):
        return {"branch": self.branch, "project": self.project, "include_unposted": self.include_unposted}

    def period_label(self):
        return f"{self.date_from:%Y-%m-%d} → {self.date_to:%Y-%m-%d}"

    def compare_label(self):
        return f"{self.c_from:%Y-%m-%d} → {self.c_to:%Y-%m-%d}" if self.c_from else ""


def _date(value, default):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


def parse_filters(params):
    today = timezone.localdate()
    y_start, _ = year_bounds(today)
    f = Filters(date_from=_date(params.get("from"), y_start), date_to=_date(params.get("to"), today))
    if f.date_from > f.date_to:
        f.date_from, f.date_to = f.date_to, f.date_from
    f.compare = params.get("compare", "")
    if f.compare == "prev":
        days = (f.date_to - f.date_from).days + 1
        f.c_to = f.date_from - timedelta(days=1)
        f.c_from = f.c_to - timedelta(days=days - 1)
    elif f.compare == "prev_year":
        f.c_from, f.c_to = _shift_year(f.date_from), _shift_year(f.date_to)
    elif f.compare == "custom":
        f.c_from = _date(params.get("c_from"), None)
        f.c_to = _date(params.get("c_to"), None)
        if not (f.c_from and f.c_to):
            f.compare = ""
    else:
        f.compare = ""
    pick = lambda model, key: model.objects.filter(pk=params.get(key)).first() if str(params.get(key, "")).isdigit() else None  # noqa: E731
    f.branch = pick(Branch, "branch")
    f.project = pick(Project, "project")
    f.account = pick(Account, "account")
    f.party = pick(Party, "party")
    f.party_kind = params.get("party_kind", "") if params.get("party_kind") in PartyKind.values else ""
    f.level = int(params["level"]) if str(params.get("level", "")).isdigit() else 0
    f.include_unposted = params.get("unposted") == "1"
    f.hide_zero = params.get("zeros") != "1"
    return f


def _shift_year(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - 1, day=28)


def _warn(f, report):
    if f.include_unposted:
        n = JournalEntry.objects.filter(date__gte=f.date_from, date__lte=f.date_to).exclude(
            status=EntryStatus.POSTED).count()
        report.warnings.append(tr("rep_includes_unposted", n))
    report.subtitle = f.period_label() + (f" · {f.branch.name}" if f.branch else "") + (
        f" · {f.project}" if f.project else "")


def _acc_url(account, f):
    return reverse("ledger_report", args=["general_ledger"]) + (
        f"?account={account.pk}&from={f.date_from:%Y-%m-%d}&to={f.date_to:%Y-%m-%d}")


def _entry_url(entry_id):
    return reverse("journal_detail", args=[entry_id])


# ---------------------------------------------------------------- journal
def journal(f):
    r = Report("journal", tr("rep_journal"), [
        Col("date", tr("date"), "date"), Col("number", tr("entry_no"), "code"), Col("account", tr("account")),
        Col("description", tr("description")), Col("debit", tr("debit"), "money"),
        Col("credit", tr("credit"), "money")])
    _warn(f, r)
    entries = JournalEntry.objects.filter(date__gte=f.date_from, date__lte=f.date_to)
    if not f.include_unposted:
        entries = entries.filter(status=EntryStatus.POSTED)
    if f.branch:
        entries = entries.filter(Q(branch=f.branch) | Q(lines__branch=f.branch)).distinct()
    entries = entries.order_by("date", "id").prefetch_related("lines__account", "lines__party")[:3000]
    td = tc = ZERO
    for e in entries:
        status = "" if e.status == EntryStatus.POSTED else f" [{e.get_status_display()}]"
        r.rows.append(Row({"date": e.date, "number": e.number, "account": e.description + status,
                           "description": e.reference}, "group", url=_entry_url(e.pk)))
        for ln in e.lines.all():
            desc = ln.description + (f" · {ln.party.name}" if ln.party_id else "")
            r.rows.append(Row({"account": str(ln.account), "description": desc, "debit": ln.debit or None,
                               "credit": ln.credit or None}, level=1))
            td += ln.debit
            tc += ln.credit
    r.rows.append(Row({"account": tr("total"), "debit": td, "credit": tc}, "grand"))
    return r


# ---------------------------------------------------------------- general ledger
def general_ledger(f):
    r = Report("general_ledger", tr("rep_general_ledger"), [
        Col("date", tr("date"), "date"), Col("number", tr("entry_no"), "code"),
        Col("description", tr("description")), Col("party", tr("party")), Col("debit", tr("debit"), "money"),
        Col("credit", tr("credit"), "money"), Col("balance", tr("balance"), "money")])
    _warn(f, r)
    tree = Tree()
    if f.account:
        accounts = [a for a in tree.descendants(f.account) if not a.is_group]
        r.title += f" — {f.account}"
    else:
        active = set(line_qs(date_to=f.date_to, **f.kw).values_list("account", flat=True).distinct())
        accounts = [a for a, _ in tree.ordered() if a.pk in active]
    for a in sorted(accounts, key=lambda x: x.code):
        before = line_qs(date_to=f.date_from - timedelta(days=1), **f.kw).filter(account=a).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        opening = natural(a, before["d"] or ZERO, before["c"] or ZERO)
        period = (line_qs(date_from=f.date_from, date_to=f.date_to, **f.kw).filter(account=a)
                  .select_related("entry", "party").order_by("entry__date", "entry_id", "id"))
        rows = list(period)
        if not rows and not opening and f.hide_zero:
            continue
        r.rows.append(Row({"date": None, "number": a.code, "description": a.name}, "group", url=_acc_url(a, f)))
        r.rows.append(Row({"description": tr("opening_balance"), "balance": opening}, "muted", 1))
        bal, td, tc = opening, ZERO, ZERO
        for ln in rows:
            bal += natural(a, ln.debit, ln.credit)
            td += ln.debit
            tc += ln.credit
            r.rows.append(Row({"date": ln.entry.date, "number": ln.entry.number,
                               "description": ln.description or ln.entry.description,
                               "party": ln.party.name if ln.party_id else "", "debit": ln.debit or None,
                               "credit": ln.credit or None, "balance": bal}, level=1, url=_entry_url(ln.entry_id)))
        r.rows.append(Row({"description": tr("closing_balance"), "debit": td, "credit": tc, "balance": bal},
                          "total", 1))
    r.notes.append(tr("rep_gl_note"))
    return r


# ---------------------------------------------------------------- trial balance
def trial_balance(f):
    r = Report("trial_balance", tr("rep_trial_balance"), [
        Col("code", tr("code"), "code"), Col("account", tr("account")),
        Col("open_d", tr("opening_debit"), "money"), Col("open_c", tr("opening_credit"), "money"),
        Col("mov_d", tr("period_debit"), "money"), Col("mov_c", tr("period_credit"), "money"),
        Col("close_d", tr("closing_debit"), "money"), Col("close_c", tr("closing_credit"), "money")])
    _warn(f, r)
    tree = Tree()
    opening = tree.rollup(totals_by_account(line_qs(date_to=f.date_from - timedelta(days=1), **f.kw)))
    movement = tree.rollup(totals_by_account(line_qs(date_from=f.date_from, date_to=f.date_to, **f.kw)))
    sums = defaultdict(lambda: ZERO)

    def split(d, c):
        net = d - c
        return (net, ZERO) if net > 0 else (ZERO, -net)

    for a, depth in tree.ordered():
        if f.level and depth > f.level:
            continue
        od, oc = split(*opening[a.pk])
        md, mc = movement[a.pk]
        cd, cc = split(opening[a.pk][0] + md, opening[a.pk][1] + mc)
        if f.hide_zero and not any([od, oc, md, mc, cd, cc]):
            continue
        r.rows.append(Row({"code": a.code, "account": a.name, "open_d": od, "open_c": oc, "mov_d": md,
                           "mov_c": mc, "close_d": cd, "close_c": cc},
                          "" if not a.is_group else ("group" if depth == 1 else "sub"), depth - 1,
                          url=_acc_url(a, f)))
        if depth == 1:
            for k, v in zip(["open_d", "open_c", "mov_d", "mov_c", "close_d", "close_c"], [od, oc, md, mc, cd, cc]):
                sums[k] += v
    r.rows.append(Row({"account": tr("total"), **sums}, "grand"))
    if sums["close_d"] != sums["close_c"] or sums["mov_d"] != sums["mov_c"]:
        r.warnings.append(tr("rep_unbalanced"))
    return r


# ---------------------------------------------------------------- income statement
def _pnl_totals(f, start, end):
    qs = line_qs(date_from=start, date_to=end, **f.kw).exclude(entry__kind=EntryKind.CLOSING)
    return totals_by_account(qs.filter(account__type__in=["revenue", "expense"]))


def _compare_cols(f, r, label_now=None):
    r.columns.append(Col("now", label_now or f.period_label(), "money"))
    if f.compare:
        r.columns += [Col("then", f.compare_label(), "money"), Col("change", tr("change"), "money"),
                      Col("pct", "%", "pct")]


def _cmp_cells(now, then, f):
    cells = {"now": now}
    if f.compare:
        cells.update(then=then, change=now - then, pct=(now - then) / abs(then) * 100 if then else None)
    return cells


def income_statement(f):
    r = Report("income_statement", tr("rep_income_statement"), [Col("account", tr("account"))])
    _compare_cols(f, r)
    _warn(f, r)
    tree = Tree()
    now = tree.rollup(_pnl_totals(f, f.date_from, f.date_to))
    then = tree.rollup(_pnl_totals(f, f.c_from, f.c_to)) if f.compare else {}
    max_depth = f.level or 3
    results = {}
    for typ, title in (("revenue", tr("at_revenue")), ("expense", tr("at_expense"))):
        sign = 1 if typ == "revenue" else -1  # show both as positive amounts
        total_now = total_then = ZERO
        r.rows.append(Row({"account": title}, "group"))
        for a, depth in tree.ordered():
            if a.type != typ or depth == 1 or depth > max_depth:
                continue
            d, c = now[a.pk]
            v_now = (c - d) * sign
            td, tc = then.get(a.pk, (ZERO, ZERO))
            v_then = (tc - td) * sign
            if f.hide_zero and not v_now and not v_then:
                continue
            r.rows.append(Row({"account": f"{a.code} · {a.name}", **_cmp_cells(v_now, v_then, f)},
                              "sub" if a.is_group and depth < max_depth else "", depth - 1, url=_acc_url(a, f)))
        for root in tree.roots():
            if root.type == typ:
                d, c = now[root.pk]
                total_now += (c - d) * sign
                td, tc = then.get(root.pk, (ZERO, ZERO))
                total_then += (tc - td) * sign
        r.rows.append(Row({"account": tr(f"total_{typ}"), **_cmp_cells(total_now, total_then, f)}, "total"))
        results[typ] = (total_now, total_then)
    net_now = results["revenue"][0] - results["expense"][0]
    net_then = results["revenue"][1] - results["expense"][1]
    r.rows.append(Row({"account": tr("net_profit") if net_now >= 0 else tr("net_loss"),
                       **_cmp_cells(net_now, net_then, f)}, "grand"))
    r.summary = [(tr("total_revenue"), results["revenue"][0]), (tr("total_expense"), results["expense"][0]),
                 (tr("net_result"), net_now)]
    return r


# ---------------------------------------------------------------- balance sheet
def balance_sheet(f):
    r = Report("balance_sheet", tr("rep_balance_sheet"), [Col("account", tr("account"))])
    as_of_then = f.c_to if f.compare else None
    r.columns.append(Col("now", f"{tr('as_of')} {f.date_to:%Y-%m-%d}", "money"))
    if f.compare:
        r.columns += [Col("then", f"{tr('as_of')} {as_of_then:%Y-%m-%d}", "money"),
                      Col("change", tr("change"), "money"), Col("pct", "%", "pct")]
    _warn(f, r)
    r.subtitle = f"{tr('as_of')} {f.date_to:%Y-%m-%d}" + (f" · {f.branch.name}" if f.branch else "")
    tree = Tree()
    now = tree.rollup(totals_by_account(line_qs(date_to=f.date_to, **f.kw)))
    then = tree.rollup(totals_by_account(line_qs(date_to=as_of_then, **f.kw))) if f.compare else {}
    max_depth = f.level or 3

    def value(a, data):
        d, c = data.get(a.pk, (ZERO, ZERO))
        return (d - c) if a.type == "asset" else (c - d)

    totals = {}
    for typ in ("asset", "liability", "equity"):
        r.rows.append(Row({"account": tr(f"at_{typ}")}, "group"))
        for a, depth in tree.ordered():
            if a.type != typ or depth == 1 or depth > max_depth:
                continue
            v_now, v_then = value(a, now), value(a, then) if f.compare else ZERO
            if f.hide_zero and not v_now and not v_then:
                continue
            r.rows.append(Row({"account": f"{a.code} · {a.name}", **_cmp_cells(v_now, v_then, f)},
                              "sub" if a.is_group and depth < max_depth else "", depth - 1, url=_acc_url(a, f)))
        t_now = sum((value(x, now) for x in tree.roots() if x.type == typ), ZERO)
        t_then = sum((value(x, then) for x in tree.roots() if x.type == typ), ZERO) if f.compare else ZERO
        if typ == "equity":
            # revenue − expense not yet closed to retained earnings
            p_now = sum(((now[x.pk][1] - now[x.pk][0]) for x in tree.roots() if x.type in ("revenue", "expense")),
                        ZERO)
            p_then = sum(((then[x.pk][1] - then[x.pk][0]) for x in tree.roots()
                          if x.type in ("revenue", "expense")), ZERO) if f.compare else ZERO
            r.rows.append(Row({"account": tr("current_result"), **_cmp_cells(p_now, p_then, f)}, "", 1))
            t_now, t_then = t_now + p_now, t_then + p_then
        r.rows.append(Row({"account": tr(f"total_{typ}"), **_cmp_cells(t_now, t_then, f)}, "total"))
        totals[typ] = (t_now, t_then)
    le_now = totals["liability"][0] + totals["equity"][0]
    le_then = totals["liability"][1] + totals["equity"][1]
    r.rows.append(Row({"account": tr("total_liab_equity"), **_cmp_cells(le_now, le_then, f)}, "grand"))
    if le_now != totals["asset"][0]:
        r.warnings.append(tr("rep_bs_unbalanced", f"{totals['asset'][0] - le_now:,.2f}"))
    r.summary = [(tr("total_asset"), totals["asset"][0]), (tr("total_liability"), totals["liability"][0]),
                 (tr("total_equity"), totals["equity"][0])]
    return r


# ---------------------------------------------------------------- cash flow (direct method)
def cash_accounts():
    return list(Account.objects.filter(treasury__isnull=False))


def _cash_flow_class(a):
    if a.cash_flow:
        return a.cash_flow
    if a.type == "equity":
        return "financing"
    return "operating"


def cash_flow(f):
    r = Report("cash_flow", tr("rep_cash_flow"), [Col("account", tr("account")), Col("now", tr("amount"), "money")])
    _warn(f, r)
    cash_ids = {a.pk for a in cash_accounts()}
    base = line_qs(date_from=f.date_from, date_to=f.date_to, **f.kw)
    entry_ids = base.filter(account_id__in=cash_ids).values_list("entry_id", flat=True).distinct()
    other = (base.filter(entry_id__in=entry_ids).exclude(account_id__in=cash_ids)
             .values("account").annotate(d=Sum("debit"), c=Sum("credit")))
    accounts = Account.objects.in_bulk([o["account"] for o in other])
    sections = OrderedDict((k, []) for k in ("operating", "investing", "financing"))
    for o in other:
        a = accounts[o["account"]]
        amount = (o["c"] or ZERO) - (o["d"] or ZERO)
        if amount:
            sections[_cash_flow_class(a)].append((a, amount))
    net = ZERO
    for key, items in sections.items():
        r.rows.append(Row({"account": tr(f"cf_{key}")}, "group"))
        inflow = sum((x for _, x in items if x > 0), ZERO)
        outflow = sum((x for _, x in items if x < 0), ZERO)
        for a, amount in sorted(items, key=lambda t: t[0].code):
            r.rows.append(Row({"account": f"{a.code} · {a.name}", "now": amount}, "", 1, url=_acc_url(a, f)))
        r.rows.append(Row({"account": f"{tr('cf_in')}: {inflow:,.2f} · {tr('cf_out')}: {-outflow:,.2f}"},
                          "muted", 1))
        total = inflow + outflow
        net += total
        r.rows.append(Row({"account": tr(f"cf_net_{key}"), "now": total}, "total"))
    opening = line_qs(date_to=f.date_from - timedelta(days=1), **f.kw).filter(account_id__in=cash_ids).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    opening = (opening["d"] or ZERO) - (opening["c"] or ZERO)
    closing = line_qs(date_to=f.date_to, **f.kw).filter(account_id__in=cash_ids).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    closing = (closing["d"] or ZERO) - (closing["c"] or ZERO)
    r.rows.append(Row({"account": tr("cf_net_change"), "now": net}, "grand"))
    r.rows.append(Row({"account": tr("cf_opening_cash"), "now": opening}))
    r.rows.append(Row({"account": tr("cf_closing_cash"), "now": opening + net}, "grand"))
    if opening + net != closing:
        r.warnings.append(tr("rep_cf_mismatch", f"{closing:,.2f}"))
    r.notes.append(tr("rep_cf_note"))
    r.summary = [(tr("cf_opening_cash"), opening), (tr("cf_net_change"), net), (tr("cf_closing_cash"), closing)]
    return r


# ---------------------------------------------------------------- customers & suppliers
def party_statement(f):
    r = Report("party_statement", tr("rep_party_statement"), [
        Col("date", tr("date"), "date"), Col("number", tr("entry_no"), "code"),
        Col("description", tr("description")), Col("debit", tr("debit"), "money"),
        Col("credit", tr("credit"), "money"), Col("balance", tr("balance"), "money")])
    _warn(f, r)
    if not f.party:
        r.notes.append(tr("rep_pick_party"))
        return r
    p = f.party
    r.title += f" — {p.name} ({p.code})"
    before = line_qs(date_to=f.date_from - timedelta(days=1), party=p, include_unposted=f.include_unposted).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    bal = (before["d"] or ZERO) - (before["c"] or ZERO)
    r.rows.append(Row({"description": tr("opening_balance"), "balance": bal}, "muted"))
    td = tc = ZERO
    for ln in (line_qs(date_from=f.date_from, date_to=f.date_to, party=p, include_unposted=f.include_unposted)
               .select_related("entry").order_by("entry__date", "entry_id", "id")):
        bal += ln.debit - ln.credit
        td += ln.debit
        tc += ln.credit
        r.rows.append(Row({"date": ln.entry.date, "number": ln.entry.number,
                           "description": ln.entry.description, "debit": ln.debit or None,
                           "credit": ln.credit or None, "balance": bal}, url=_entry_url(ln.entry_id)))
    r.rows.append(Row({"description": tr("closing_balance"), "debit": td, "credit": tc, "balance": bal}, "grand"))
    r.notes.append(tr("rep_party_sign"))
    aging = age_party(p, f.date_to, f.include_unposted)
    r.summary = [(tr("closing_balance"), bal)] + [(tr(f"age_{k}"), v) for k, v in aging.items()]
    return r


AGE_BUCKETS = (("current", 30), ("30", 60), ("60", 90), ("90", None))


def age_party(party, as_of, include_unposted=False):
    """Split the balance by age (first in, first out): the oldest charges are settled first."""
    qs = line_qs(date_to=as_of, party=party, include_unposted=include_unposted).select_related("entry")
    agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    balance = (agg["d"] or ZERO) - (agg["c"] or ZERO)
    buckets = OrderedDict((k, ZERO) for k, _ in AGE_BUCKETS)
    if not balance:
        return buckets
    side = "debit" if balance > 0 else "credit"
    left = abs(balance)
    for ln in qs.filter(**{f"{side}__gt": 0}).order_by("-entry__date", "-id"):
        if left <= 0:
            break
        take = min(getattr(ln, side), left)
        left -= take
        days = (as_of - ln.entry.date).days
        for key, limit in AGE_BUCKETS:
            if limit is None or days < limit:
                buckets[key] += take if balance > 0 else -take
                break
    return buckets


def party_balances(f):
    r = Report("party_balances", tr("rep_party_balances"), [
        Col("code", tr("code"), "code"), Col("name", tr("name")), Col("kind", tr("type")),
        Col("debit", tr("debit"), "money"), Col("credit", tr("credit"), "money"),
        Col("balance", tr("balance"), "money")] + [Col(k, tr(f"age_{k}"), "money") for k, _ in AGE_BUCKETS])
    _warn(f, r)
    r.subtitle = f"{tr('as_of')} {f.date_to:%Y-%m-%d}"
    qs = line_qs(date_to=f.date_to, include_unposted=f.include_unposted, branch=f.branch).filter(party__isnull=False)
    if f.party_kind:
        qs = qs.filter(party__kind=f.party_kind)
    rows = qs.values("party").annotate(d=Sum("debit"), c=Sum("credit"))
    parties = Party.objects.in_bulk([x["party"] for x in rows])
    totals = defaultdict(lambda: ZERO)
    for x in sorted(rows, key=lambda x: parties[x["party"]].name):
        p = parties[x["party"]]
        bal = (x["d"] or ZERO) - (x["c"] or ZERO)
        if f.hide_zero and not bal:
            continue
        aging = age_party(p, f.date_to, f.include_unposted)
        cells = {"code": p.code, "name": p.name, "kind": p.get_kind_display(), "debit": x["d"], "credit": x["c"],
                 "balance": bal, **aging}
        for k in ["debit", "credit", "balance", *aging.keys()]:
            totals[k] += cells[k] or ZERO
        url = reverse("ledger_report", args=["party_statement"]) + f"?party={p.pk}&to={f.date_to:%Y-%m-%d}"
        r.rows.append(Row(cells, url=url))
    r.rows.append(Row({"name": tr("total"), **totals}, "grand"))
    r.notes.append(tr("rep_party_sign"))
    return r


# ---------------------------------------------------------------- revenue & expenses by month
def _months(start, end):
    out, m = [], date(start.year, start.month, 1)
    while m <= end and len(out) < 24:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def revenue_expense(f):
    months = _months(f.date_from, f.date_to)
    r = Report("revenue_expense", tr("rep_revenue_expense"), [Col("account", tr("account"))] + [
        Col(f"m{i}", f"{m:%Y-%m}", "money") for i, m in enumerate(months)] + [Col("total", tr("total"), "money")])
    _warn(f, r)
    qs = (line_qs(date_from=f.date_from, date_to=f.date_to, **f.kw).exclude(entry__kind=EntryKind.CLOSING)
          .filter(account__type__in=["revenue", "expense"]))
    data = defaultdict(lambda: defaultdict(lambda: ZERO))
    for row in qs.values("account", "entry__date__year", "entry__date__month").annotate(d=Sum("debit"), c=Sum("credit")):
        data[row["account"]][(row["entry__date__year"], row["entry__date__month"])] += (row["c"] or ZERO) - (row["d"] or ZERO)
    accounts = Account.objects.in_bulk(data.keys())
    net = defaultdict(lambda: ZERO)
    for typ in ("revenue", "expense"):
        sign = 1 if typ == "revenue" else -1
        r.rows.append(Row({"account": tr(f"at_{typ}")}, "group"))
        sums = defaultdict(lambda: ZERO)
        for acc_id in sorted((k for k in data if accounts[k].type == typ), key=lambda k: accounts[k].code):
            a = accounts[acc_id]
            cells = {"account": f"{a.code} · {a.name}"}
            total = ZERO
            for i, m in enumerate(months):
                v = data[acc_id].get((m.year, m.month), ZERO) * sign
                cells[f"m{i}"] = v
                sums[f"m{i}"] += v
                total += v
            cells["total"] = total
            sums["total"] += total
            r.rows.append(Row(cells, level=1, url=_acc_url(a, f)))
        r.rows.append(Row({"account": tr(f"total_{typ}"), **sums}, "total"))
        for k, v in sums.items():
            net[k] += v * sign
    r.rows.append(Row({"account": tr("net_result"), **net}, "grand"))
    return r


def project_expenses(f):
    r = Report("project_expenses", tr("rep_project_expenses"), [
        Col("account", tr("account")), Col("now", tr("amount"), "money"), Col("budget", tr("budget"), "money"),
        Col("left", tr("budget_left"), "money")])
    _warn(f, r)
    qs = (line_qs(date_from=f.date_from, date_to=f.date_to, include_unposted=f.include_unposted, branch=f.branch)
          .exclude(entry__kind=EntryKind.CLOSING).filter(project__isnull=False, account__type="expense"))
    if f.project:
        qs = qs.filter(project=f.project)
    data = defaultdict(dict)
    for row in qs.values("project", "account").annotate(d=Sum("debit"), c=Sum("credit")):
        data[row["project"]][row["account"]] = (row["d"] or ZERO) - (row["c"] or ZERO)
    projects = Project.objects.in_bulk(data.keys())
    accounts = Account.objects.in_bulk({a for v in data.values() for a in v})
    grand = ZERO
    for pid in sorted(data, key=lambda k: projects[k].code):
        p = projects[pid]
        r.rows.append(Row({"account": str(p)}, "group"))
        total = ZERO
        for aid, amount in sorted(data[pid].items(), key=lambda kv: accounts[kv[0]].code):
            r.rows.append(Row({"account": str(accounts[aid]), "now": amount}, level=1))
            total += amount
        r.rows.append(Row({"account": tr("total"), "now": total, "budget": p.budget,
                           "left": (p.budget - total) if p.budget is not None else None}, "total"))
        grand += total
    r.rows.append(Row({"account": tr("total"), "now": grand}, "grand"))
    return r


# ---------------------------------------------------------------- cash and banks
def cash_balances(f):
    r = Report("cash_balances", tr("rep_cash_balances"), [
        Col("name", tr("treasury")), Col("kind", tr("type")), Col("opening", tr("opening_balance"), "money"),
        Col("in", tr("cf_in"), "money"), Col("out", tr("cf_out"), "money"),
        Col("closing", tr("closing_balance"), "money"), Col("fx", tr("balance_fc"))])
    _warn(f, r)
    totals = defaultdict(lambda: ZERO)
    for t in Treasury.objects.select_related("account", "account__currency"):
        before = line_qs(date_to=f.date_from - timedelta(days=1), **f.kw).filter(account=t.account).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        mov = line_qs(date_from=f.date_from, date_to=f.date_to, **f.kw).filter(account=t.account).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        opening = (before["d"] or ZERO) - (before["c"] or ZERO)
        closing = opening + (mov["d"] or ZERO) - (mov["c"] or ZERO)
        fx = ""
        cur = t.account.currency
        if cur and not cur.is_base:
            from .balances import treasury_balance

            fx = f"{treasury_balance(t, f.date_to, in_currency=True, **f.kw):,.2f} {cur.code}"
        if f.hide_zero and not (opening or closing or mov["d"] or mov["c"]) and not t.is_active:
            continue
        cells = {"name": t.name, "kind": t.get_kind_display(), "opening": opening, "in": mov["d"] or ZERO,
                 "out": mov["c"] or ZERO, "closing": closing, "fx": fx}
        for k in ("opening", "in", "out", "closing"):
            totals[k] += cells[k]
        r.rows.append(Row(cells, url=_acc_url(t.account, f)))
    r.rows.append(Row({"name": tr("total"), **totals}, "grand"))
    return r


# ---------------------------------------------------------------- fixed assets
def assets_register(f):
    r = Report("assets_register", tr("rep_assets"), [
        Col("code", tr("code"), "code"), Col("name", tr("name")), Col("category", tr("category")),
        Col("in_service", tr("in_service_date"), "date"), Col("cost", tr("cost"), "money"),
        Col("dep_period", tr("dep_in_period"), "money"), Col("accumulated", tr("accumulated_dep"), "money"),
        Col("book", tr("book_value"), "money"), Col("status", tr("status"))])
    _warn(f, r)
    r.subtitle = f"{tr('as_of')} {f.date_to:%Y-%m-%d} · {tr('dep_in_period')}: {f.period_label()}"
    totals = defaultdict(lambda: ZERO)
    qs = FixedAsset.objects.exclude(status=AssetStatus.DRAFT).select_related("category")
    if f.branch:
        qs = qs.filter(branch=f.branch)
    if f.project:
        qs = qs.filter(project=f.project)
    by_cat = defaultdict(list)
    for a in qs.filter(purchase_date__lte=f.date_to):
        by_cat[a.category].append(a)
    for cat in sorted(by_cat, key=lambda c: c.name):
        r.rows.append(Row({"name": cat.name}, "group"))
        sub = defaultdict(lambda: ZERO)
        for a in by_cat[cat]:
            disposed = a.disposed_on and a.disposed_on <= f.date_to
            cost = ZERO if disposed else a.total_cost(f.date_to)
            acc_dep = ZERO if disposed else a.accumulated(f.date_to)
            dep = a.events.filter(kind="depreciation", date__gte=f.date_from, date__lte=f.date_to).aggregate(
                s=Sum("amount"))["s"] or ZERO
            cells = {"code": a.code, "name": a.name, "category": cat.name, "in_service": a.in_service_date,
                     "cost": cost, "dep_period": dep, "accumulated": acc_dep, "book": cost - acc_dep,
                     "status": a.get_status_display()}
            for k in ("cost", "dep_period", "accumulated", "book"):
                sub[k] += cells[k]
                totals[k] += cells[k]
            r.rows.append(Row(cells, level=1, url=reverse("asset_detail", args=[a.pk])))
        r.rows.append(Row({"name": tr("total"), **sub}, "total"))
    r.rows.append(Row({"name": tr("total"), **totals}, "grand"))
    return r


REPORTS = OrderedDict([
    ("journal", (journal, "rep_journal", "book", {"branch"})),
    ("general_ledger", (general_ledger, "rep_general_ledger", "layers", {"account", "branch", "project"})),
    ("trial_balance", (trial_balance, "rep_trial_balance", "scale", {"branch", "project", "level"})),
    ("income_statement", (income_statement, "rep_income_statement", "chart", {"branch", "project", "compare",
                                                                              "level"})),
    ("balance_sheet", (balance_sheet, "rep_balance_sheet", "scale", {"branch", "compare", "level"})),
    ("cash_flow", (cash_flow, "rep_cash_flow", "repeat", {"branch"})),
    ("party_statement", (party_statement, "rep_party_statement", "user", {"party"})),
    ("party_balances", (party_balances, "rep_party_balances", "users", {"party_kind", "branch"})),
    ("revenue_expense", (revenue_expense, "rep_revenue_expense", "chart", {"branch", "project"})),
    ("project_expenses", (project_expenses, "rep_project_expenses", "briefcase", {"project", "branch"})),
    ("cash_balances", (cash_balances, "rep_cash_balances", "wallet", {"branch"})),
    ("assets_register", (assets_register, "rep_assets", "monitor", {"branch", "project"})),
])


def build(key, params):
    fn, _, _, _ = REPORTS[key]
    f = parse_filters(params)
    return fn(f), f


# ---------------------------------------------------------------- Excel
def to_xlsx(report, lang="ar"):
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = report.key[:30]
    ws.sheet_view.rightToLeft = lang == "ar"
    ws.append([report.title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([report.subtitle])
    for w in report.warnings:
        ws.append([w])
    ws.append([])
    ws.append([c.label for c in report.columns])
    head = ws.max_row
    for cell in ws[head]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E6F2FB")
    for row in report.rows:
        values = []
        for i, c in enumerate(report.columns):
            v = row.cells.get(c.key)
            if c.kind in ("money", "pct") and v is not None:
                v = float(v)
            elif c.kind == "date" and v is not None:
                v = v if isinstance(v, date) else str(v)
            elif v is not None:
                v = str(v)
                if i == 0 and row.level:
                    v = "    " * row.level + v
            values.append(v)
        ws.append(values)
        excel_row = ws[ws.max_row]
        for cell, c in zip(excel_row, report.columns):
            if c.kind == "money":
                cell.number_format = "#,##0.00;[Red]-#,##0.00"
            elif c.kind == "pct":
                cell.number_format = "0.0"
            elif c.kind == "date":
                cell.number_format = "yyyy-mm-dd"
            if row.style in ("group", "total", "grand", "sub"):
                cell.font = Font(bold=True)
            if row.style == "grand":
                cell.fill = PatternFill("solid", fgColor="F1F5F9")
    for label, amount in report.summary:
        ws.append([])
        ws.append([label, float(amount)])
    for i, c in enumerate(report.columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = 16 if c.kind in ("money", "date", "pct", "code") else 40
    for r_ in ws.iter_rows(min_row=head):
        for cell in r_:
            cell.alignment = Alignment(horizontal="right" if lang == "ar" else None)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()

