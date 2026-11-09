from django.conf import settings

from accounts.roles import has_perm

from .i18n import current_lang, tr

# Sidebar. "match" = URL-name prefixes that mark the item active.
# Items with "soon" are later stages, shown greyed out so staff see what's coming.
NAV = [
    {"key": "nav_dashboard", "url": "dashboard", "icon": "home", "module": "dashboard", "match": ["dashboard"]},
    {"section": "sec_operations"},
    {"key": "nav_appointments", "url": "appointment_list", "icon": "calendar", "module": "appointments", "match": ["appointment_"]},
    {"key": "nav_patients", "url": "patient_list", "icon": "user", "module": "patients", "match": ["patient_"]},
    {"key": "queue_screen", "url": "queue_screen", "icon": "tv", "module": "appointments", "match": ["queue_"]},
    {"key": "nav_clinical", "url": "clinical_home", "icon": "clipboard", "module": "clinical", "match": ["clinical_", "note_", "rx_", "plan_", "photo_", "tooth_"]},
    {"section": "sec_finance"},
    {"key": "nav_invoices", "url": "invoice_list", "icon": "receipt", "module": "invoices", "match": ["invoice_"]},
    {"key": "nav_cashbox", "url": "cashbox", "icon": "wallet", "module": "cashbox", "match": ["cashbox"]},
    {"key": "nav_doctor_accounts", "url": "doctor_accounts", "icon": "percent", "module": "doctor_accounts", "match": ["doctor_"]},
    {"key": "nav_payouts", "url": "payout_list", "icon": "send", "module": "payouts", "match": ["payout_"]},
    {"key": "nav_expenses", "url": "expense_list", "icon": "minus-circle", "module": "expenses", "match": ["expense_"]},
    {"key": "nav_fin_reports", "url": "finance_monthly", "icon": "chart", "module": "finance_reports", "match": ["finance_"]},
    {"section": "sec_setup"},
    {"key": "nav_services", "url": "service_list", "icon": "activity", "module": "services", "match": ["service_", "category_"]},
    {"key": "nav_pricelists", "url": "pricelist_list", "icon": "tag", "module": "pricelists", "match": ["pricelist_"]},
    {"key": "nav_packages", "url": "package_list", "icon": "package", "module": "packages", "match": ["package_"]},
    {"key": "nav_payment_methods", "url": "paymentmethod_list", "icon": "card", "module": "paymentmethods", "match": ["paymentmethod_"]},
    {"key": "nav_branches", "url": "branch_list", "icon": "building", "module": "branches", "match": ["branch_"]},
    {"key": "nav_users", "url": "user_list", "icon": "users", "module": "users", "match": ["user_"]},
    {"key": "nav_clinic_settings", "url": "clinic_settings", "icon": "settings", "module": "settings", "match": ["clinic_settings"]},
    {"key": "nav_examples", "url": "examples", "icon": "sparkle", "module": "settings", "match": ["examples"]},
    {"section": "sec_management"},
    {"key": "nav_warehouse", "icon": "archive", "soon": 5},
    {"key": "nav_assets", "icon": "monitor", "soon": 5},
    {"key": "nav_hr", "icon": "idcard", "soon": 6},
    {"key": "nav_reports", "icon": "chart", "soon": 7},
    {"key": "nav_audit", "url": "audit_list", "icon": "shield", "module": "audit", "match": ["audit_"]},
]


def build_nav(request):
    current = getattr(request.resolver_match, "url_name", "") or ""
    items, pending_section = [], None
    for entry in NAV:
        if "section" in entry:
            pending_section = {"section": tr(entry["section"])}
            continue
        if "module" in entry and not has_perm(request.user, entry["module"]):
            continue
        if pending_section:
            items.append(pending_section)
            pending_section = None
        items.append({
            "label": tr(entry["key"]),
            "url": entry.get("url"),
            "icon": entry["icon"],
            "soon": entry.get("soon"),
            "active": any(current.startswith(m) for m in entry.get("match", [])),
        })
    return items


def app_context(request):
    from .models import ClinicProfile

    lang = current_lang()
    profile = ClinicProfile.get()
    ctx = {
        "lang": lang,
        "dir": "rtl" if lang == "ar" else "ltr",
        "clinic": profile,
        "default_logo": settings.CLINIC_DEFAULT_LOGO,
        "clinic_name": profile.display_name(lang),
        "clinic_tagline": profile.display_tagline(lang) or tr("app_tagline"),
        "currency": settings.CLINIC_CURRENCY_EN if lang == "en" else settings.CLINIC_CURRENCY_AR,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        ctx["nav"] = build_nav(request)
    return ctx
