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
    {"key": "nav_clinical", "icon": "clipboard", "soon": 3},
    {"section": "sec_setup"},
    {"key": "nav_services", "url": "service_list", "icon": "activity", "module": "services", "match": ["service_", "category_"]},
    {"key": "nav_pricelists", "url": "pricelist_list", "icon": "tag", "module": "pricelists", "match": ["pricelist_"]},
    {"key": "nav_packages", "url": "package_list", "icon": "package", "module": "packages", "match": ["package_"]},
    {"key": "nav_payment_methods", "url": "paymentmethod_list", "icon": "card", "module": "paymentmethods", "match": ["paymentmethod_"]},
    {"key": "nav_branches", "url": "branch_list", "icon": "building", "module": "branches", "match": ["branch_"]},
    {"key": "nav_users", "url": "user_list", "icon": "users", "module": "users", "match": ["user_"]},
    {"section": "sec_management"},
    {"key": "nav_finance", "icon": "wallet", "soon": 4},
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
    lang = current_lang()
    ctx = {
        "lang": lang,
        "dir": "rtl" if lang == "ar" else "ltr",
        "clinic_name": settings.CLINIC_NAME_EN if lang == "en" else settings.CLINIC_NAME_AR,
        "currency": settings.CLINIC_CURRENCY_EN if lang == "en" else settings.CLINIC_CURRENCY_AR,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        ctx["nav"] = build_nav(request)
    return ctx
