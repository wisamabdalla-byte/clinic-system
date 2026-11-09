from django.urls import path

from . import views as v

urlpatterns = [
    path("invoices/", v.InvoiceList.as_view(), name="invoice_list"),
    path("invoices/new/", v.InvoiceCreate.as_view(), name="invoice_create"),
    path("invoices/<int:pk>/", v.InvoiceDetail.as_view(), name="invoice_detail"),
    path("invoices/<int:pk>/edit/", v.InvoiceUpdate.as_view(), name="invoice_edit"),
    path("invoices/<int:pk>/print/", v.InvoicePrint.as_view(), name="invoice_print"),
    path("invoices/<int:pk>/pay/", v.PaymentCreate.as_view(), name="invoice_pay"),
    path("invoices/<int:pk>/cancel/", v.InvoiceCancel.as_view(), name="invoice_cancel"),
    path("payments/<int:pk>/void/", v.PaymentVoid.as_view(), name="payment_void"),
    path("doctor-accounts/", v.DoctorAccounts.as_view(), name="doctor_accounts"),
    path("doctor-accounts/<int:pk>/month/", v.DoctorMonth.as_view(), name="doctor_month"),
    path("doctor-accounts/<int:pk>/statement/", v.DoctorStatement.as_view(), name="doctor_statement"),
    path("payouts/", v.PayoutList.as_view(), name="payout_list"),
    path("payouts/new/", v.PayoutCreate.as_view(), name="payout_create"),
    path("expenses/", v.ExpenseList.as_view(), name="expense_list"),
    path("expenses/new/", v.ExpenseCreate.as_view(), name="expense_create"),
    path("expenses/<int:pk>/edit/", v.ExpenseUpdate.as_view(), name="expense_edit"),
    path("cashbox/", v.DailyCash.as_view(), name="cashbox"),
    path("finance/monthly/", v.MonthlyReport.as_view(), name="finance_monthly"),
]
