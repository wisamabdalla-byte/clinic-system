"""Which entry each financial document produces (see the table in docs/accounting-design.md).

Patient invoices, payments, expenses, doctor settlements and payouts from the finance app
are kept in sync automatically through signals (ledger/signals.py). Vouchers, bills,
transfers, custody movements and asset operations are posted explicitly with post_document().
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction

from core.i18n import tr

from .engine import ZERO, active_entry, cr, create_entry, dr, ensure_open, q, reverse, sync_source
from .models import (
    BillKind, DocStatus, ExpenseCategory, LedgerError, LedgerSettings, MovementKind, Party, PartyKind,
    PaymentMethodAccount, VoucherKind,
)


def settings_():
    return LedgerSettings.get()


# ---------------------------------------------------------------- parties for clinic people
def party_for_patient(patient):
    try:
        return patient.party
    except Party.DoesNotExist:
        return Party.objects.create(kind=PartyKind.PATIENT, name=patient.full_name, phone=patient.phone or "",
                                    patient=patient)


def party_for_user(user, kind=PartyKind.DOCTOR):
    try:
        return user.party
    except Party.DoesNotExist:
        return Party.objects.create(kind=kind, name=str(user), phone=user.phone or "", email=user.email or "",
                                    user=user)


def method_account(method):
    try:
        return method.ledger_account.account
    except PaymentMethodAccount.DoesNotExist:
        s = settings_()
        return s.acct("cash_account" if method.kind == "cash" else "bank_account")


def category_account(code):
    cat = ExpenseCategory.objects.filter(code=code).select_related("account").first()
    return cat.account if cat else settings_().acct("default_expense")


REVENUE_KEY = {"dental": "revenue_dental", "derma": "revenue_derma"}


# ---------------------------------------------------------------- finance app documents (auto-synced)
def sync_clinic_invoice(inv):
    s = settings_()
    lines = []
    if not inv.is_cancelled and inv.total > 0:
        party = party_for_patient(inv.patient)
        revenue = {}
        for item in inv.items.select_related("service"):
            dept = item.service.department if item.service_id else (inv.doctor.department or "general")
            acc = s.acct(REVENUE_KEY.get(dept, "revenue_general"))
            revenue[acc] = revenue.get(acc, ZERO) + item.line_total
        lines.append(dr(s.acct("patients_receivable"), inv.total, party=party, description=inv.patient.full_name))
        discount = q(sum(revenue.values(), ZERO) - inv.total)
        if discount > 0:
            lines.append(dr(s.acct("sales_discount"), discount, description=tr("discount")))
        for acc, amount in revenue.items():
            lines.append(cr(acc, amount, description=inv.number))
    return sync_source(inv, date=inv.date, branch=inv.branch, lines=lines, reference=inv.number,
                       description=f"{tr('ent_invoice')} {inv.number} — {inv.patient.full_name}",
                       label=inv.number)


def sync_clinic_payment(p):
    s = settings_()
    inv = p.invoice
    party = party_for_patient(inv.patient)
    lines = [dr(method_account(p.method), p.amount, description=p.method.name),
             cr(s.acct("patients_receivable"), p.amount, party=party, description=inv.number)]
    return sync_source(p, date=p.date, branch=p.branch, lines=lines, reference=p.reference or inv.number,
                       description=f"{tr('payment')} {inv.number} — {inv.patient.full_name}", label=inv.number)


def sync_clinic_expense(e):
    lines = [dr(category_account(e.category), e.amount, description=e.description),
             cr(method_account(e.method), e.amount, description=e.method.name)]
    return sync_source(e, date=e.date, branch=e.branch, lines=lines, reference=e.reference,
                       description=f"{tr('ent_expense')}: {e.description}", label=tr("ent_expense"))


def sync_doctor_settlement(st):
    from finance.models import next_month

    s = settings_()
    party = party_for_user(st.doctor)
    day = next_month(st.period) - timedelta(days=1)
    lines = [dr(s.acct("doctor_commission"), st.total_due, description=f"{st.period:%Y-%m}"),
             cr(s.acct("doctors_payable"), st.total_due, party=party, description=str(st.doctor))]
    return sync_source(st, date=day, lines=lines, label=f"{st.period:%Y-%m}",
                       description=f"{tr('month_share', f'{st.period:%Y-%m}')} — {st.doctor}")


def sync_doctor_payout(p):
    s = settings_()
    lines = [dr(s.acct("doctors_payable"), p.amount, party=party_for_user(p.doctor), description=p.notes),
             cr(method_account(p.method), p.amount, description=p.method.name)]
    return sync_source(p, date=p.date, branch=p.branch, lines=lines, reference=p.reference,
                       description=f"{tr('payout')} — {p.doctor}", label=tr("payout"))


SYNCERS = {
    "finance.invoice": sync_clinic_invoice,
    "finance.payment": sync_clinic_payment,
    "finance.expense": sync_clinic_expense,
    "finance.doctorsettlement": sync_doctor_settlement,
    "finance.doctorpayout": sync_doctor_payout,
}


def sync_all_finance():
    """Create the entries for finance documents written before the ledger existed. Safe to run again."""
    from finance.models import DoctorPayout, DoctorSettlement, Expense, Invoice, Payment

    count = 0
    for model, fn in ((Invoice, sync_clinic_invoice), (Payment, sync_clinic_payment),
                      (Expense, sync_clinic_expense), (DoctorSettlement, sync_doctor_settlement),
                      (DoctorPayout, sync_doctor_payout)):
        for obj in model.objects.order_by("id"):
            before = active_entry(obj)
            after = fn(obj)
            if after is not None and (before is None or before.pk != after.pk):
                count += 1
    return count


# ---------------------------------------------------------------- ledger documents (posted on request)
def _fx(currency, amount, rate):
    if currency is None or currency.is_base:
        return {}
    return {"currency": currency, "fx_amount": q(amount), "fx_rate": rate}


def bill_lines(bill):
    party = bill.party
    party_acc = party.default_account()
    rate = bill.rate if bill.currency_id and not bill.currency.is_base else Decimal(1)
    lines = []
    total_base = ZERO
    for ln in bill.lines.select_related("category", "account", "tax", "tax__account", "project"):
        acc = ln.target_account()
        if acc is None:
            raise LedgerError(tr("bill_line_needs_account", ln.description))
        base = q(ln.net * rate)
        tax_base = q(ln.tax_amount * rate)
        total_base += base + tax_base
        maker = dr if bill.kind == BillKind.PURCHASE else cr
        lines.append(maker(acc, base, description=ln.description, project=ln.project,
                           **_fx(bill.currency, ln.net, rate)))
        if tax_base:
            lines.append(maker(ln.tax.account, tax_base, description=str(ln.tax), **_fx(bill.currency,
                                                                                        ln.tax_amount, rate)))
    other = cr if bill.kind == BillKind.PURCHASE else dr
    lines.append(other(party_acc, total_base, party=party, description=bill.party_reference or bill.number,
                       **_fx(bill.currency, bill.total, rate)))
    return lines


def voucher_lines(v):
    """Treasury on one side; party/account on the other. Payments against foreign bills
    settle the bill at the bill's rate; the difference goes to exchange gain/loss."""
    s = settings_()
    treasury_acc = v.treasury.account
    cur = treasury_acc.currency
    rate = v.rate if cur and not cur.is_base else Decimal(1)
    base = q(v.amount * rate)
    receipt = v.kind == VoucherKind.RECEIPT
    lines = [(dr if receipt else cr)(treasury_acc, base, description=v.description, **_fx(cur, v.amount, rate))]
    side = cr if receipt else dr
    if v.party_id:
        party_acc = v.party.default_account()
        settled_base = ZERO
        remaining = v.amount
        for a in v.allocations.select_related("bill", "bill__currency"):
            b_rate = a.bill.rate if a.bill.currency_id and not a.bill.currency.is_base else Decimal(1)
            at_bill = q(a.amount * b_rate)
            settled_base += at_bill
            remaining -= a.amount
            lines.append(side(party_acc, at_bill, party=v.party, description=a.bill.number,
                              **_fx(a.bill.currency, a.amount, b_rate)))
        if remaining > 0:
            lines.append(side(party_acc, q(remaining * rate), party=v.party, description=v.description,
                              **_fx(cur, remaining, rate)))
            settled_base += q(remaining * rate)
        diff = base - settled_base  # receipt: + means we got more LYD than booked → gain
        if diff:
            gain = diff > 0 if receipt else diff < 0
            acc = s.acct("fx_gain" if gain else "fx_loss")
            lines.append((cr if gain else dr)(acc, abs(diff), description=tr("fx_difference")))
    else:
        acc = v.account or (v.category.account if v.category_id else None)
        if acc is None:
            raise LedgerError(tr("voucher_needs_target"))
        lines.append(side(acc, base, description=v.payee or v.description, project=v.project))
    return lines


def transfer_lines(t):
    s = settings_()
    src, dst = t.source.account, t.target.account
    src_rate = t.source_rate if src.currency and not src.currency.is_base else Decimal(1)
    dst_rate = t.target_rate if dst.currency and not dst.currency.is_base else Decimal(1)
    out_base = q(t.amount * src_rate)
    target_amount = t.target_amount if t.target_amount else t.amount
    in_base = q(target_amount * dst_rate)
    lines = [dr(dst, in_base, description=t.description, **_fx(dst.currency, target_amount, dst_rate)),
             cr(src, out_base, description=t.description, **_fx(src.currency, t.amount, src_rate))]
    diff = in_base - out_base
    if diff:
        lines.append(cr(s.acct("fx_gain"), diff) if diff > 0 else dr(s.acct("fx_loss"), -diff))
    if t.fees:
        fee_base = q(t.fees * src_rate)
        lines += [dr(s.acct("bank_charges"), fee_base, description=tr("bank_fees")),
                  cr(src, fee_base, description=tr("bank_fees"), **_fx(src.currency, t.fees, src_rate))]
    return lines


def custody_lines(m):
    c = m.custody
    custody_acc = c.account()
    if m.kind == MovementKind.ISSUE:
        return [dr(custody_acc, m.amount, party=c.holder, description=c.purpose, project=c.project),
                cr(m.treasury.account, m.amount, description=c.number)]
    if m.kind == MovementKind.RETURN:
        return [dr(m.treasury.account, m.amount, description=c.number),
                cr(custody_acc, m.amount, party=c.holder, description=tr("mk_return"))]
    acc = m.account or (m.category.account if m.category_id else None)
    if acc is None:
        raise LedgerError(tr("voucher_needs_target"))
    return [dr(acc, m.amount, description=m.description, project=m.project or c.project),
            cr(custody_acc, m.amount, party=c.holder, description=c.number)]


def custody_check(m):
    c = m.custody
    if c.is_closed:
        raise LedgerError(tr("custody_closed"))
    if m.kind in (MovementKind.ISSUE, MovementKind.RETURN) and m.treasury_id is None:
        raise LedgerError(tr("treasury_required"))
    if m.kind != MovementKind.ISSUE and m.amount > c.figures()["remaining"]:
        raise LedgerError(tr("custody_over", f"{c.figures()['remaining']:,.2f}"))


def describe(doc):
    from .models import Bill, CustodyMovement, Transfer, Voucher

    if isinstance(doc, Voucher):
        who = doc.party.name if doc.party_id else doc.payee
        return f"{doc.get_kind_display()} {doc.number}" + (f" — {who}" if who else "") + f": {doc.description}"
    if isinstance(doc, Bill):
        return f"{doc.get_kind_display()} {doc.number} — {doc.party.name}"
    if isinstance(doc, Transfer):
        return f"{tr('transfer')} {doc.number}: {doc.source.name} → {doc.target.name}"
    if isinstance(doc, CustodyMovement):
        return f"{doc.get_kind_display()} {doc.custody.number} — {doc.custody.holder.name}"
    return str(doc)


def build_lines(doc):
    from .models import Bill, CustodyMovement, Transfer, Voucher

    if isinstance(doc, Voucher):
        return voucher_lines(doc)
    if isinstance(doc, Bill):
        return bill_lines(doc)
    if isinstance(doc, Transfer):
        return transfer_lines(doc)
    if isinstance(doc, CustodyMovement):
        custody_check(doc)
        return custody_lines(doc)
    raise LedgerError("unknown document")


def post_document(doc, user):
    """Draft → posted: write and post its journal entry."""
    from .models import Bill, Voucher

    if doc.status != DocStatus.DRAFT:
        raise LedgerError(tr("wrong_status"))
    with transaction.atomic():
        if isinstance(doc, Voucher):
            check_allocations(doc)
        entry = create_entry(date=doc.date, description=describe(doc), lines=build_lines(doc), user=user,
                             source=doc, source_label=doc.number, branch=doc.branch,
                             reference=getattr(doc, "reference", "") or getattr(doc, "party_reference", ""))
        doc.entry, doc.status = entry, DocStatus.POSTED
        doc.save(update_fields=["entry", "status", "updated_at"])
        if isinstance(doc, Voucher):
            for a in doc.allocations.select_related("bill"):
                a.bill.refresh_paid()
        if isinstance(doc, Bill):
            doc.refresh_paid()
    return entry


def cancel_document(doc, user, reason=""):
    """Posted → cancelled: reverse its entry (a draft is simply cancelled)."""
    from .models import Bill, CustodyMovement, Voucher

    if doc.status == DocStatus.CANCELLED:
        raise LedgerError(tr("wrong_status"))
    with transaction.atomic():
        if isinstance(doc, Bill) and doc.allocations.filter(voucher__status=DocStatus.POSTED).exists():
            raise LedgerError(tr("bill_has_payments"))
        if isinstance(doc, CustodyMovement) and doc.kind == MovementKind.ISSUE and doc.status == DocStatus.POSTED:
            f = doc.custody.figures()
            if f["remaining"] < doc.amount:
                raise LedgerError(tr("custody_issue_used"))
        if doc.status == DocStatus.POSTED and doc.entry_id:
            ensure_open(doc.entry.date, user)
            reverse(doc.entry, user, note=reason)
        doc.status, doc.cancel_reason = DocStatus.CANCELLED, (reason or "")[:255]
        doc.save(update_fields=["status", "cancel_reason", "updated_at"])
        if isinstance(doc, Voucher):
            for a in doc.allocations.select_related("bill"):
                a.bill.refresh_paid()


def same_currency(a, b):
    return (a.code if a and not a.is_base else None) == (b.code if b and not b.is_base else None)


def check_allocations(v):
    total = ZERO
    for a in v.allocations.select_related("bill", "bill__currency"):
        b = a.bill
        if not same_currency(b.currency, v.treasury.account.currency):
            raise LedgerError(tr("alloc_currency", b.number))
        if b.party_id != v.party_id or b.status != DocStatus.POSTED:
            raise LedgerError(tr("alloc_wrong_bill", b.number))
        want = BillKind.SALES if v.kind == VoucherKind.RECEIPT else BillKind.PURCHASE
        if b.kind != want:
            raise LedgerError(tr("alloc_wrong_bill", b.number))
        if a.amount > b.balance:
            raise LedgerError(tr("alloc_over_balance", b.number, f"{b.balance:,.2f}"))
        total += a.amount
    if total > v.amount:
        raise LedgerError(tr("alloc_over_amount"))
