"""Balance queries. Everything here reads posted entries only, unless include_unposted is asked
for explicitly (reports then show a warning)."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum

from .models import ZERO, Account, EntryStatus, JournalLine


def lines(date_from=None, date_to=None, include_unposted=False, branch=None, project=None, party=None,
          accounts=None):
    qs = JournalLine.objects.all()
    if not include_unposted:
        qs = qs.filter(entry__status=EntryStatus.POSTED)
    if date_from:
        qs = qs.filter(entry__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__date__lte=date_to)
    if branch:
        qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True, entry__branch=branch))
    if project:
        qs = qs.filter(project=project)
    if party:
        qs = qs.filter(party=party)
    if accounts is not None:
        qs = qs.filter(account__in=accounts)
    return qs


def totals_by_account(qs):
    """{account_id: (debit, credit)}"""
    return {r["account"]: (r["d"] or ZERO, r["c"] or ZERO)
            for r in qs.values("account").annotate(d=Sum("debit"), c=Sum("credit"))}


class Tree:
    """The chart of accounts in memory, with debit/credit rolled up to every group."""

    def __init__(self, accounts=None):
        self.accounts = list(accounts if accounts is not None else Account.objects.all().order_by("code"))
        self.by_id = {a.pk: a for a in self.accounts}
        self.children = defaultdict(list)
        for a in self.accounts:
            self.children[a.parent_id].append(a)

    def roots(self):
        return self.children[None]

    def descendants(self, account):
        out, stack = [], [account]
        while stack:
            node = stack.pop()
            out.append(node)
            stack.extend(self.children[node.pk])
        return out

    def rollup(self, totals):
        """totals {id: (d, c)} for postable accounts → same for every account incl. groups."""
        result = {}

        def walk(a):
            d, c = totals.get(a.pk, (ZERO, ZERO))
            for ch in self.children[a.pk]:
                cd, cc = walk(ch)
                d, c = d + cd, c + cc
            result[a.pk] = (d, c)
            return d, c

        for r in self.roots():
            walk(r)
        return result

    def depth(self, account):
        n, node = 1, account
        while node.parent_id:
            node = self.by_id[node.parent_id]
            n += 1
        return n

    def ordered(self):
        """Depth-first, by code: (account, depth)."""
        out = []

        def walk(a, depth):
            out.append((a, depth))
            for ch in sorted(self.children[a.pk], key=lambda x: x.code):
                walk(ch, depth + 1)

        for r in sorted(self.roots(), key=lambda x: x.code):
            walk(r, 1)
        return out


def natural(account, debit, credit):
    """Balance shown in the account's own direction (positive = normal)."""
    return (debit - credit) if account.nature == "debit" else (credit - debit)


def account_balance(account, date_to=None, date_from=None, **kw):
    tree = Tree()
    ids = [a.pk for a in tree.descendants(account)]
    agg = lines(date_from=date_from, date_to=date_to, **kw).filter(account_id__in=ids).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    return natural(account, agg["d"] or ZERO, agg["c"] or ZERO)


def party_balance(party, date_to=None, **kw):
    """Debit − credit across the party's lines: positive = they owe us."""
    agg = lines(date_to=date_to, party=party, **kw).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or ZERO) - (agg["c"] or ZERO)


def treasury_balance(treasury, date_to=None, in_currency=False, **kw):
    qs = lines(date_to=date_to, **kw).filter(account=treasury.account)
    if in_currency and treasury.account.currency and not treasury.account.currency.is_base:
        signed = ZERO
        for d, c, fx in qs.values_list("debit", "credit", "fx_amount"):
            signed += (fx or ZERO) * (1 if d else -1)
        return signed
    agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or ZERO) - (agg["c"] or ZERO)


def trial_balance_check(date_to=None):
    agg = lines(date_to=date_to).aggregate(d=Sum("debit"), c=Sum("credit"))
    return agg["d"] or Decimal(0), agg["c"] or Decimal(0)
