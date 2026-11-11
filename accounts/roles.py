"""Roles and what each role may do.

A module is one area of the system (services, users, ...). Each module has a
"view" and an "edit" list of roles. Superusers can do everything.
Later stages add modules here (appointments, patients, finance, ...).
"""
from django.db import models

from core.i18n import tr_lazy


class Role(models.TextChoices):
    ADMIN = "admin", tr_lazy("role_admin")
    MANAGER = "manager", tr_lazy("role_manager")
    DOCTOR = "doctor", tr_lazy("role_doctor")
    NURSE = "nurse", tr_lazy("role_nurse")
    RECEPTIONIST = "receptionist", tr_lazy("role_receptionist")
    ACCOUNTANT = "accountant", tr_lazy("role_accountant")
    STOREKEEPER = "storekeeper", tr_lazy("role_storekeeper")


R = Role
EVERYONE = set(Role.values)
FIN = {R.ADMIN, R.MANAGER, R.ACCOUNTANT}

PERMISSIONS = {
    "dashboard": {"view": EVERYONE},
    "users": {"view": {R.ADMIN, R.MANAGER}, "edit": {R.ADMIN}},
    "branches": {"view": {R.ADMIN, R.MANAGER}, "edit": {R.ADMIN}},
    "services": {"view": EVERYONE, "edit": {R.ADMIN, R.MANAGER}},
    "pricelists": {
        "view": {R.ADMIN, R.MANAGER, R.RECEPTIONIST, R.ACCOUNTANT},
        "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT},
    },
    "packages": {
        "view": {R.ADMIN, R.MANAGER, R.RECEPTIONIST, R.ACCOUNTANT, R.DOCTOR},
        "edit": {R.ADMIN, R.MANAGER},
    },
    "audit": {"view": {R.ADMIN}},
    "paymentmethods": {
        "view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.RECEPTIONIST},
        "edit": {R.ADMIN, R.ACCOUNTANT},
    },
    # Stage 2
    "patients": {
        "view": {R.ADMIN, R.MANAGER, R.DOCTOR, R.NURSE, R.RECEPTIONIST, R.ACCOUNTANT},
        "edit": {R.ADMIN, R.MANAGER, R.DOCTOR, R.NURSE, R.RECEPTIONIST},
    },
    "appointments": {
        "view": {R.ADMIN, R.MANAGER, R.DOCTOR, R.NURSE, R.RECEPTIONIST},
        "edit": {R.ADMIN, R.MANAGER, R.RECEPTIONIST},  # book / reschedule
        "status": {R.ADMIN, R.MANAGER, R.RECEPTIONIST, R.NURSE, R.DOCTOR},  # check-in, start, ...
    },
    # Stage 3: clinical records
    "clinical": {
        "view": {R.ADMIN, R.MANAGER, R.DOCTOR, R.NURSE},
        "edit": {R.ADMIN, R.DOCTOR, R.NURSE},  # notes, tooth chart, photos, treatment plans
        "prescribe": {R.ADMIN, R.DOCTOR},
    },
    # Stage 4: finance
    "invoices": {
        "view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.RECEPTIONIST},
        "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.RECEPTIONIST},  # write invoices, take payments
        "void": {R.ADMIN, R.MANAGER, R.ACCOUNTANT},  # remove a wrong payment
    },
    "doctor_accounts": {
        "view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.DOCTOR},  # a doctor sees only their own
        "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT},  # close a month
        "reopen": {R.ADMIN},
    },
    "payouts": {"view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}, "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}},
    "expenses": {"view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}, "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}},
    "cashbox": {"view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.RECEPTIONIST}},
    "finance_reports": {"view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}},
    # Clinic name, logo and colours
    "settings": {"view": {R.ADMIN}, "edit": {R.ADMIN}},
    # General ledger (ledger app). Admins can change any of these per user on the
    # "Finance permissions" page (ledger.PermissionOverride).
    "acc_home": {"view": FIN},
    "coa": {"view": FIN, "edit": {R.ADMIN, R.ACCOUNTANT}},
    "journal": {
        "view": FIN,
        "edit": {R.ADMIN, R.ACCOUNTANT},  # write and change draft entries
        "review": {R.ADMIN, R.MANAGER, R.ACCOUNTANT},
        "approve": {R.ADMIN, R.MANAGER},
        "post": {R.ADMIN, R.MANAGER, R.ACCOUNTANT},
        "reverse": {R.ADMIN, R.MANAGER},
    },
    "vouchers": {"view": FIN, "edit": {R.ADMIN, R.ACCOUNTANT}, "post": FIN, "cancel": {R.ADMIN, R.MANAGER}},
    "bills": {"view": FIN, "edit": {R.ADMIN, R.ACCOUNTANT}, "post": FIN, "cancel": {R.ADMIN, R.MANAGER}},
    "treasury": {"view": FIN, "edit": {R.ADMIN, R.ACCOUNTANT}, "reconcile": {R.ADMIN, R.ACCOUNTANT}},
    "custody": {"view": FIN, "edit": {R.ADMIN, R.ACCOUNTANT}, "post": FIN, "cancel": {R.ADMIN, R.MANAGER}},
    "parties": {"view": FIN, "edit": {R.ADMIN, R.ACCOUNTANT}},
    "assets": {"view": FIN, "edit": {R.ADMIN, R.ACCOUNTANT}, "depreciate": {R.ADMIN, R.ACCOUNTANT},
               "dispose": {R.ADMIN, R.MANAGER}},
    "statements": {"view": FIN},
    "periods": {
        "view": FIN,
        "edit": {R.ADMIN, R.MANAGER},  # close a month / a year
        "reopen": {R.ADMIN},
        "override": set(),  # post into a closed period: only superusers or users given it explicitly
    },
    "acc_settings": {"view": FIN, "edit": {R.ADMIN}},
    "backup": {"view": {R.ADMIN}, "edit": {R.ADMIN}},
}

# Status actions a role may perform (others are refused even with "status" permission).
STATUS_ACTIONS = {
    R.DOCTOR: {"start", "complete"},  # own appointments only
    R.NURSE: {"confirm", "checkin", "start", "complete", "cancel", "noshow"},
    R.RECEPTIONIST: {"confirm", "checkin", "start", "cancel", "noshow", "restore"},
}


def role_allows(role, module, action="view"):
    return role in PERMISSIONS.get(module, {}).get(action, set())


def overrides(user):
    """{"module.action": True/False} set for this user by an admin (cached on the user object)."""
    cache = getattr(user, "_perm_overrides", None)
    if cache is None:
        from ledger.models import PermissionOverride

        cache = dict(PermissionOverride.objects.filter(user_id=user.pk).values_list("perm", "allow"))
        user._perm_overrides = cache
    return cache


def has_perm(user, module, action="view"):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if user.is_superuser:
        return True
    override = overrides(user).get(f"{module}.{action}")
    if override is not None:
        return override
    return role_allows(user.role, module, action)
