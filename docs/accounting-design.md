# النظام المالي والمحاسبي — التصميم

The accounting module lives in the `ledger` app. It sits under all money in the clinic. Every
financial event, whether a patient invoice, a payment, an expense or a doctor payout from the existing `finance` app,
or a voucher, bill, transfer, custody movement or depreciation run from `ledger`, ends up as a
balanced double-entry journal entry. All financial reports read only those entries.

## 1. الوحدات والعلاقات

```
                ┌──────────────── LedgerSettings (default accounts, workflow rules) ────────────────┐
                │                                                                                    │
 Account (tree) ◄── JournalLine ──► JournalEntry ──► FiscalPeriod ──► FiscalYear                     │
   ▲   ▲   ▲          │  │  │            │  ▲                                                        │
   │   │   │      Party Project Branch   │  └─ reversal_of (self)                                    │
   │   │   │          ▲                  └── source = "<app.model>:<id>"  (one active entry per doc) │
   │   │   │          │                                                                              │
   │   │   └─ Treasury (cash box / bank) ◄── Voucher (قبض/صرف) ──► VoucherAllocation ──► Bill         │
   │   │         ▲   ▲                    ◄── Transfer                                               │
   │   │         │   └── BankReconciliation (ticks JournalLine.reconciliation)                       │
   │   │         └────── Custody ──► CustodyMovement (issue / expense / return)                     │
   │   └── ExpenseCategory, TaxRate, PaymentMethodAccount                                            │
   └────── AssetCategory ◄── FixedAsset ──► AssetEvent (acquisition / improvement / depreciation /   │
                                                    disposal) ◄── DepreciationRun                    │
 finance.Invoice / Payment / Expense / DoctorSettlement / DoctorPayout ── signals ── posting.sync ───┘
 clinic.Patient ──1:1── Party ──1:1── accounts.User (doctors, staff)
```

| Table | Purpose |
|---|---|
| `Account` | Chart of accounts: code, Arabic/English name, parent, type (asset/liability/equity/revenue/expense), nature (debit/credit), group vs. postable, control flag (needs a customer/supplier), cash-flow class, optional foreign currency |
| `FiscalYear`, `FiscalPeriod` | Monthly periods, created automatically. Closed periods refuse postings unless the user has the special `periods.override` permission. The year close posts a closing entry to retained earnings |
| `JournalEntry`, `JournalLine` | Header with auto number `JV-2026-00001`, status (draft → reviewed → approved → posted), source document, reversal link. Lines: account, debit, credit (LYD), optional currency + foreign amount + rate, customer/supplier, project, branch |
| `Attachment` | Scanned invoices and documents, stored in the database (like patient photos), attachable to any entry or document |
| `Party` | Customers, suppliers, employees and doctors. A patient gets a party automatically on their first invoice |
| `Treasury` | Cash boxes and bank accounts, each tied to its own ledger account |
| `Voucher` + `VoucherAllocation` | Receipt and payment vouchers, allocated against bills (paid / partly paid / unpaid) |
| `Bill` + `BillLine` | Sales and purchase invoices for non-patient business (suppliers, companies) with configurable taxes |
| `Transfer` | Cash ↔ bank ↔ bank transfers, with exchange difference and fees |
| `BankReconciliation` | Statement balance vs. book balance, ticking cleared lines |
| `Custody` + `CustodyMovement` | Cash custody and staff advances: issue, expenses paid from them, return, remaining balance |
| `FixedAsset`, `AssetCategory`, `AssetEvent`, `DepreciationRun` | Asset register, monthly depreciation (straight line / declining balance), improvements, disposal with gain or loss |
| `Currency`, `ExchangeRate` | LYD is the base currency. Foreign amounts are converted at the document rate; realised differences and period-end revaluation post to FX gain/loss |
| `TaxRate` | No rates are assumed. The admin enters whatever Libyan law requires |
| `PermissionOverride` | Per-user allow/deny for finance actions, on top of the role defaults |

## 2. قواعد المحاسبة المطبقة

* **Double entry.** No entry can be saved posted unless total debit equals total credit (to the dirham). Every line is one-sided and positive.
* **Accrual basis.** Invoices post revenue and receivables on the invoice date. Payments only settle the receivable. Doctor commissions are recognised when the month is closed for the doctor. Adjusting entries can be set to reverse automatically on the first day of the next period.
* **Posted entries are immutable.** They cannot be edited or deleted, and the database layer refuses it as well. A mistake is corrected by **reversal**, which creates a mirror entry and keeps both. "Reverse & correct" also opens an editable copy.
* **Documents keep their entry in sync.** When a patient invoice is edited, the old entry is reversed and a new one is posted. When a payment is voided, its entry is reversed. Nothing is ever silently overwritten.
* **Closed periods** refuse any posting, reversal or document change dated inside them.
* **Reports** use posted entries only. Including unposted entries is an explicit option, and the report then carries a warning banner.

## 3. القيود الآلية

| Event | Debit | Credit |
|---|---|---|
| Patient invoice | Patients receivable (patient) total + Sales discounts | Dental / dermatology / general revenue |
| Patient payment | Cash or bank (by payment method) | Patients receivable (patient) |
| Daily expense | Expense account of the category | Cash or bank |
| Doctor month closed | Doctor commissions | Doctors payable (doctor) |
| Doctor payout | Doctors payable (doctor) | Cash or bank |
| Sales bill | Customer receivable | Revenue lines + output tax |
| Purchase bill | Expense / asset lines + input tax | Supplier payable |
| Receipt / payment voucher | Treasury / party (or the reverse), with FX difference | |
| Transfer | Destination treasury (+ fees, FX) | Source treasury |
| Custody issue / expense / return | Custody ↔ treasury / expense | |
| Asset purchase / improvement | Asset cost | Treasury / supplier / opening equity |
| Depreciation run | Depreciation expense | Accumulated depreciation |
| Asset disposal | Accumulated depreciation + proceeds (+ loss) | Asset cost (+ gain) |
| Year close | Revenues | Expenses, with the difference to retained earnings |

## 4. الصلاحيات

Each finance action has its own permission: enter, review, approve, post, reverse, close periods,
reopen periods, post into a closed period, settings and backup. Roles get sensible defaults
(`accounts/roles.py`). The admin can allow or deny any action for any user on the
"Finance permissions" page. Optionally, nobody may approve an entry they created themselves.
Every action is written to the activity log with who, when and what changed.
