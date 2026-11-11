from django.urls import path

from . import views as v

urlpatterns = [
    path("accounting/", v.Home.as_view(), name="acc_home"),
    path("accounting/sync/", v.SyncFinance.as_view(), name="ledger_sync"),
    # chart of accounts
    path("accounting/accounts/", v.AccountTree.as_view(), name="account_list"),
    path("accounting/accounts/new/", v.AccountCreate.as_view(), name="account_create"),
    path("accounting/accounts/<int:pk>/edit/", v.AccountUpdate.as_view(), name="account_edit"),
    # journal
    path("accounting/journal/", v.JournalList.as_view(), name="journal_list"),
    path("accounting/journal/new/", v.EntryFormView.as_view(), name="journal_create"),
    path("accounting/journal/<int:pk>/", v.EntryDetail.as_view(), name="journal_detail"),
    path("accounting/journal/<int:pk>/edit/", v.EntryFormView.as_view(), name="journal_edit"),
    path("accounting/journal/<int:pk>/action/", v.EntryAction.as_view(), name="journal_action"),
    path("accounting/attach/<str:target>/<int:pk>/", v.AttachmentUpload.as_view(), name="attachment_upload"),
    path("accounting/attachments/<int:pk>/", v.AttachmentGet.as_view(), name="attachment_get"),
    # bills
    path("accounting/bills/", v.BillList.as_view(), name="bill_list"),
    path("accounting/bills/new/sales/", v.BillFormView.as_view(), {"kind": "sales"}, name="bill_create_sales"),
    path("accounting/bills/new/purchase/", v.BillFormView.as_view(), {"kind": "purchase"},
         name="bill_create_purchase"),
    path("accounting/bills/<int:pk>/", v.BillDetail.as_view(), name="bill_detail"),
    path("accounting/bills/<int:pk>/edit/", v.BillFormView.as_view(), name="bill_edit"),
    path("accounting/bills/<int:pk>/action/", v.BillAction.as_view(), name="bill_action"),
    # vouchers
    path("accounting/vouchers/", v.VoucherList.as_view(), name="voucher_list"),
    path("accounting/vouchers/new/receipt/", v.VoucherFormView.as_view(), {"kind": "receipt"},
         name="voucher_create_receipt"),
    path("accounting/vouchers/new/payment/", v.VoucherFormView.as_view(), {"kind": "payment"},
         name="voucher_create_payment"),
    path("accounting/vouchers/<int:pk>/", v.VoucherDetail.as_view(), name="voucher_detail"),
    path("accounting/vouchers/<int:pk>/edit/", v.VoucherFormView.as_view(), name="voucher_edit"),
    path("accounting/vouchers/<int:pk>/action/", v.VoucherAction.as_view(), name="voucher_action"),
    # cash and banks
    path("accounting/treasury/", v.TreasuryList.as_view(), name="treasury_list"),
    path("accounting/treasury/new/", v.TreasuryCreate.as_view(), name="treasury_create"),
    path("accounting/treasury/<int:pk>/edit/", v.TreasuryUpdate.as_view(), name="treasury_edit"),
    path("accounting/transfers/", v.TransferList.as_view(), name="transfer_list"),
    path("accounting/transfers/new/", v.TransferCreate.as_view(), name="transfer_create"),
    path("accounting/transfers/<int:pk>/", v.TransferDetail.as_view(), name="transfer_detail"),
    path("accounting/transfers/<int:pk>/action/", v.TransferAction.as_view(), name="transfer_action"),
    path("accounting/reconcile/new/", v.ReconcileCreate.as_view(), name="reconcile_create"),
    path("accounting/reconcile/<int:pk>/", v.ReconcileDetail.as_view(), name="reconcile_detail"),
    # custody
    path("accounting/custody/", v.CustodyList.as_view(), name="custody_list"),
    path("accounting/custody/new/", v.CustodyCreate.as_view(), name="custody_create"),
    path("accounting/custody/<int:pk>/", v.CustodyDetail.as_view(), name="custody_detail"),
    # customers & suppliers
    path("accounting/parties/", v.PartyList.as_view(), name="party_list"),
    path("accounting/parties/new/", v.PartyCreate.as_view(), name="party_create"),
    path("accounting/parties/<int:pk>/", v.PartyDetail.as_view(), name="party_detail"),
    path("accounting/parties/<int:pk>/edit/", v.PartyUpdate.as_view(), name="party_edit"),
    # fixed assets
    path("accounting/assets/", v.AssetList.as_view(), name="asset_list"),
    path("accounting/assets/new/", v.AssetCreate.as_view(), name="asset_create"),
    path("accounting/assets/<int:pk>/", v.AssetDetail.as_view(), name="asset_detail"),
    path("accounting/assets/<int:pk>/edit/", v.AssetUpdate.as_view(), name="asset_edit"),
    path("accounting/assets/depreciation/", v.Depreciation.as_view(), name="depreciation"),
    path("accounting/assets/categories/", v.AssetCategoryList.as_view(), name="assetcategory_list"),
    path("accounting/assets/categories/new/", v.AssetCategoryCreate.as_view(), name="assetcategory_create"),
    path("accounting/assets/categories/<int:pk>/edit/", v.AssetCategoryUpdate.as_view(),
         name="assetcategory_edit"),
    # reports
    path("accounting/reports/", v.ReportIndex.as_view(), name="ledger_reports"),
    path("accounting/reports/<str:key>/", v.ReportView.as_view(), name="ledger_report"),
    # settings
    path("accounting/settings/", v.SettingsHub.as_view(), name="ledger_settings"),
    path("accounting/settings/periods/", v.Periods.as_view(), name="period_list"),
    path("accounting/settings/permissions/", v.FinancePermissions.as_view(), name="fin_permissions"),
    path("accounting/settings/backup/", v.Backup.as_view(), name="backup"),
]

for _name, _views in {
    "project": (v.ProjectList, v.ProjectCreate, v.ProjectUpdate),
    "expcat": (v.ExpCatList, v.ExpCatCreate, v.ExpCatUpdate),
    "tax": (v.TaxList, v.TaxCreate, v.TaxUpdate),
    "currency": (v.CurrencyList, v.CurrencyCreate, v.CurrencyUpdate),
    "rate": (v.RateList, v.RateCreate, v.RateUpdate),
}.items():
    _l, _c, _u = _views
    urlpatterns += [
        path(f"accounting/settings/{_name}/", _l.as_view(), name=f"{_name}_list"),
        path(f"accounting/settings/{_name}/new/", _c.as_view(), name=f"{_name}_create"),
        path(f"accounting/settings/{_name}/<int:pk>/edit/", _u.as_view(), name=f"{_name}_edit"),
    ]
