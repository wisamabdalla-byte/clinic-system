"""The starting chart of accounts for a dental & dermatology clinic, default settings,
expense categories and asset categories. Run once by the ledger's data migration; every
account can be renamed, moved, deactivated or extended afterwards on the Chart of accounts page.

Each row: code, Arabic name, English name, type, flags
flags: G = group (heading), C = control (needs a customer/supplier), X = contra (opposite nature),
       I = investing cash-flow, F = financing cash-flow, N = not for manual entries
"""

CHART = [
    ("1", "الأصول", "Assets", "asset", "G"),
    ("11", "الأصول المتداولة", "Current assets", "asset", "G"),
    ("111", "النقدية بالصناديق", "Cash on hand", "asset", "G"),
    ("1111", "الصندوق الرئيسي", "Main cash box", "asset", ""),
    ("112", "النقدية بالمصارف", "Cash at banks", "asset", "G"),
    ("1121", "الحساب المصرفي الرئيسي", "Main bank account", "asset", ""),
    ("113", "الذمم المدينة", "Receivables", "asset", "G"),
    ("1131", "ذمم المرضى", "Patients receivable", "asset", "C"),
    ("1132", "ذمم العملاء وشركات التأمين", "Customers & insurers receivable", "asset", "C"),
    ("114", "العهد والسلف", "Custody & advances", "asset", "G"),
    ("1141", "العهد النقدية", "Cash custody", "asset", "C"),
    ("1142", "سلف الموظفين", "Staff advances", "asset", "C"),
    ("115", "مصروفات مدفوعة مقدماً", "Prepaid expenses", "asset", ""),
    ("116", "مخزون المواد الطبية", "Medical supplies inventory", "asset", ""),
    ("117", "ضرائب ورسوم قابلة للاسترداد", "Recoverable taxes", "asset", ""),
    ("12", "الأصول الثابتة", "Fixed assets", "asset", "G"),
    ("121", "الأجهزة والمعدات الطبية", "Medical equipment", "asset", "I"),
    ("122", "الأثاث والتجهيزات", "Furniture & fittings", "asset", "I"),
    ("123", "أجهزة الحاسوب والبرمجيات", "Computers & software", "asset", "I"),
    ("124", "وسائل النقل", "Vehicles", "asset", "I"),
    ("125", "المباني والتحسينات", "Buildings & improvements", "asset", "I"),
    ("129", "مجمع الإهلاك", "Accumulated depreciation", "asset", "GX"),
    ("1291", "مجمع إهلاك الأجهزة الطبية", "Acc. depreciation — medical equipment", "asset", "XI"),
    ("1292", "مجمع إهلاك الأثاث", "Acc. depreciation — furniture", "asset", "XI"),
    ("1293", "مجمع إهلاك الحاسوب", "Acc. depreciation — computers", "asset", "XI"),
    ("1294", "مجمع إهلاك وسائل النقل", "Acc. depreciation — vehicles", "asset", "XI"),
    ("1295", "مجمع إهلاك المباني والتحسينات", "Acc. depreciation — buildings", "asset", "XI"),
    ("2", "الالتزامات", "Liabilities", "liability", "G"),
    ("21", "الالتزامات المتداولة", "Current liabilities", "liability", "G"),
    ("211", "ذمم الموردين", "Suppliers payable", "liability", "C"),
    ("212", "مستحقات الأطباء", "Doctors payable", "liability", "C"),
    ("213", "مصروفات مستحقة", "Accrued expenses", "liability", ""),
    ("214", "دفعات مقدمة من المرضى", "Patient deposits", "liability", ""),
    ("215", "ضرائب ورسوم مستحقة", "Taxes & fees payable", "liability", ""),
    ("216", "رواتب مستحقة", "Salaries payable", "liability", ""),
    ("22", "الالتزامات طويلة الأجل", "Long-term liabilities", "liability", "G"),
    ("221", "القروض", "Loans", "liability", "F"),
    ("3", "حقوق الملكية", "Equity", "equity", "G"),
    ("31", "رأس المال", "Capital", "equity", "F"),
    ("32", "الأرباح المحتجزة", "Retained earnings", "equity", ""),
    ("33", "جاري المالك / الشركاء", "Owners' current account", "equity", "F"),
    ("34", "أرصدة افتتاحية", "Opening balances", "equity", ""),
    ("4", "الإيرادات", "Revenue", "revenue", "G"),
    ("41", "إيرادات الخدمات الطبية", "Medical services revenue", "revenue", "G"),
    ("411", "إيرادات خدمات الأسنان", "Dental services revenue", "revenue", ""),
    ("412", "إيرادات خدمات الجلدية", "Dermatology services revenue", "revenue", ""),
    ("413", "إيرادات خدمات عامة", "General services revenue", "revenue", ""),
    ("419", "خصومات مسموح بها", "Discounts allowed", "revenue", "X"),
    ("42", "إيرادات أخرى", "Other income", "revenue", "G"),
    ("421", "أرباح فروق العملة", "Exchange gains", "revenue", ""),
    ("422", "أرباح بيع أصول", "Gain on asset disposal", "revenue", ""),
    ("423", "إيرادات متنوعة", "Miscellaneous income", "revenue", ""),
    ("5", "المصروفات", "Expenses", "expense", "G"),
    ("51", "المصروفات التشغيلية", "Operating expenses", "expense", "G"),
    ("511", "عمولات الأطباء", "Doctor commissions", "expense", ""),
    ("512", "مواد طبية مستهلكة", "Medical consumables", "expense", ""),
    ("513", "مصروفات المختبر", "Laboratory fees", "expense", ""),
    ("514", "صيانة الأجهزة والمبنى", "Maintenance", "expense", ""),
    ("52", "المصروفات الإدارية والعمومية", "General & administrative expenses", "expense", "G"),
    ("521", "الرواتب والأجور", "Salaries & wages", "expense", ""),
    ("522", "الإيجار", "Rent", "expense", ""),
    ("523", "الكهرباء والمياه والاتصالات", "Utilities & telecoms", "expense", ""),
    ("524", "القرطاسية والمطبوعات", "Stationery & printing", "expense", ""),
    ("525", "التسويق والإعلان", "Marketing & advertising", "expense", ""),
    ("526", "رسوم حكومية وتراخيص", "Government fees & licences", "expense", ""),
    ("527", "عمولات ورسوم مصرفية", "Bank charges", "expense", ""),
    ("529", "مصروفات متنوعة", "Miscellaneous expenses", "expense", ""),
    ("53", "الإهلاك", "Depreciation", "expense", "G"),
    ("531", "مصروف إهلاك الأصول الثابتة", "Depreciation expense", "expense", ""),
    ("54", "مصروفات أخرى", "Other expenses", "expense", "G"),
    ("541", "خسائر فروق العملة", "Exchange losses", "expense", ""),
    ("542", "خسائر بيع أصول", "Loss on asset disposal", "expense", ""),
    ("55", "مصروفات المشاريع", "Project expenses", "expense", "G"),
    ("551", "مصروفات المشاريع", "Project expenses", "expense", ""),
]

SETTINGS = {
    "cash_account": "1111", "bank_account": "1121", "patients_receivable": "1131",
    "customers_receivable": "1132", "suppliers_payable": "211", "doctors_payable": "212",
    "employee_advances": "1142", "custody_account": "1141", "revenue_dental": "411",
    "revenue_derma": "412", "revenue_general": "413", "sales_discount": "419",
    "doctor_commission": "511", "default_expense": "529", "bank_charges": "527", "fx_gain": "421",
    "fx_loss": "541", "asset_gain": "422", "asset_loss": "542", "retained_earnings": "32",
    "opening_equity": "34",
}

# code (same codes as the old fixed expense list, so existing expenses keep working), ar, en, group, account
EXPENSE_CATEGORIES = [
    ("rent", "الإيجار", "Rent", "admin", "522"),
    ("salaries", "الرواتب", "Salaries", "admin", "521"),
    ("materials", "مواد طبية", "Medical materials", "operating", "512"),
    ("lab", "مختبر", "Laboratory", "operating", "513"),
    ("utilities", "كهرباء وماء واتصالات", "Utilities", "general", "523"),
    ("maintenance", "صيانة", "Maintenance", "operating", "514"),
    ("marketing", "تسويق وإعلان", "Marketing", "general", "525"),
    ("stationery", "قرطاسية ومطبوعات", "Stationery", "admin", "524"),
    ("gov_fees", "رسوم حكومية وتراخيص", "Government fees", "admin", "526"),
    ("bank_fees", "رسوم مصرفية", "Bank charges", "general", "527"),
    ("projects", "مصروفات مشاريع", "Project expenses", "project", "551"),
    ("other", "أخرى", "Other", "other", "529"),
]

# ar, en, asset account, accumulated, life (months)
ASSET_CATEGORIES = [
    ("أجهزة ومعدات طبية", "Medical equipment", "121", "1291", 84),
    ("أثاث وتجهيزات", "Furniture & fittings", "122", "1292", 120),
    ("حاسوب وبرمجيات", "Computers & software", "123", "1293", 36),
    ("وسائل نقل", "Vehicles", "124", "1294", 60),
    ("مباني وتحسينات", "Buildings & improvements", "125", "1295", 240),
]

NATURAL = {"asset": "debit", "expense": "debit", "liability": "credit", "equity": "credit", "revenue": "credit"}
OPPOSITE = {"debit": "credit", "credit": "debit"}


def seed_defaults(apps):
    """Create the default chart and settings if the chart is still empty. `apps` is the
    migration app registry (or django.apps.apps)."""
    Account = apps.get_model("ledger", "Account")
    if Account.objects.exists():
        return
    Currency = apps.get_model("ledger", "Currency")
    LedgerSettings = apps.get_model("ledger", "LedgerSettings")
    ExpenseCategory = apps.get_model("ledger", "ExpenseCategory")
    AssetCategory = apps.get_model("ledger", "AssetCategory")
    Treasury = apps.get_model("ledger", "Treasury")
    PaymentMethod = apps.get_model("core", "PaymentMethod")
    PaymentMethodAccount = apps.get_model("ledger", "PaymentMethodAccount")

    Currency.objects.get_or_create(code="LYD", defaults={"name_ar": "دينار ليبي", "name_en": "Libyan dinar",
                                                         "symbol": "د.ل", "is_base": True})
    Currency.objects.get_or_create(code="USD", defaults={"name_ar": "دولار أمريكي", "name_en": "US dollar",
                                                         "symbol": "$"})
    Currency.objects.get_or_create(code="EUR", defaults={"name_ar": "يورو", "name_en": "Euro", "symbol": "€"})

    accounts = {}
    for code, ar, en, typ, flags in CHART:
        parent = accounts.get(code[:-1]) if len(code) > 1 else None
        nature = NATURAL[typ]
        if "X" in flags:
            nature = OPPOSITE[nature]
        cash_flow = "investing" if "I" in flags else "financing" if "F" in flags else ""
        accounts[code] = Account.objects.create(
            code=code, name_ar=ar, name_en=en, type=typ, nature=nature, parent=parent, is_group="G" in flags,
            is_control="C" in flags, allow_manual="N" not in flags, cash_flow=cash_flow,
        )
    s = LedgerSettings.objects.first() or LedgerSettings.objects.create()
    for key, code in SETTINGS.items():
        setattr(s, f"{key}_id", accounts[code].pk)
    s.save()

    for code, ar, en, group, acc in EXPENSE_CATEGORIES:
        ExpenseCategory.objects.create(code=code, name_ar=ar, name_en=en, group=group, account=accounts[acc])
    for ar, en, asset, acc_dep, life in ASSET_CATEGORIES:
        AssetCategory.objects.create(name_ar=ar, name_en=en, asset_account=accounts[asset],
                                     accumulated_account=accounts[acc_dep], expense_account=accounts["531"],
                                     life_months=life)
    Treasury.objects.create(kind="cash", name_ar="الصندوق الرئيسي", name_en="Main cash box",
                            account=accounts["1111"])
    Treasury.objects.create(kind="bank", name_ar="الحساب المصرفي الرئيسي", name_en="Main bank account",
                            account=accounts["1121"])
    for m in PaymentMethod.objects.all():
        PaymentMethodAccount.objects.get_or_create(
            method=m, defaults={"account": accounts["1111" if m.kind == "cash" else "1121"]})
